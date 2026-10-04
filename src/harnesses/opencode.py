"""OpenCode CLI harness -- adapted from upstream VEX-Bench.

Runs OpenCode inside disposable Docker containers with a host-side proxy
for OpenRouter fp8 provider pinning. The proxy is started automatically
on first use and shared across all container runs in the process.

Logging:
  - Terminal (INFO): one-line start/end per task, errors, billing alerts
  - File (DEBUG): full pipeline detail (container IDs, copy steps, proxy health)
  - Proxy log: written to a separate file, path printed at startup
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from harnesses.base import AgentResult, BaseHarness, ParsedResult, RunContext, RunStats
from evaluate.result_parser import parse_category

logger = logging.getLogger(__name__)

LOG_DIR = Path("logs")
_file_handler: logging.FileHandler | None = None


def _setup_file_logging() -> None:
    """Add a DEBUG-level file handler on first use."""
    global _file_handler
    if _file_handler is not None:
        return
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / f"opencode-harness-{int(time.time())}.log"
    _file_handler = logging.FileHandler(path, encoding="utf-8")
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    logging.getLogger("harnesses.opencode").addHandler(_file_handler)
    logger.info("harness log: %s", path)

HOST_ENV_FILE = Path("env/opencode/opencode.env")
HOST_CONFIG = Path("env/opencode/opencode.json")
PROXY_SCRIPT = Path("scripts/openrouter_proxy.py")
PROXY_PORT = 8788
CONTAINER_CONFIG = "/root/.config/opencode/opencode.json"

MODEL2NAME = {
    "kimi-k2.6": "openrouter-proxy/moonshotai/kimi-k2.6",
    "deepseek-v4-pro": "openrouter-proxy/deepseek/deepseek-v4-pro",
    "deepseek-v4-flash": "openrouter-proxy/deepseek/deepseek-v4-flash",
    "minimax-m2.7": "openrouter-proxy/minimax/minimax-m2.7",
    "glm-5.1": "openrouter-proxy/z-ai/glm-5.1",
}

_proxy_proc: subprocess.Popen | None = None
_proxy_log: Path | None = None
_proxy_lock = __import__("threading").Lock()


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _load_env_file(path: Path) -> dict[str, str]:
    """Parse a KEY=value env file, skipping comments and blanks."""
    result = {}
    if not path.exists():
        return result
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip().strip("'\"")
    return result


def _ensure_proxy() -> None:
    """Start the OpenRouter proxy if it isn't already listening."""
    global _proxy_proc, _proxy_log

    with _proxy_lock:
        if _proxy_proc is not None:
            rc = _proxy_proc.poll()
            if rc is not None:
                logger.error("proxy died pid=%d exit=%d log=%s", _proxy_proc.pid, rc, _proxy_log)
                if _proxy_log and _proxy_log.exists():
                    logger.debug("proxy log tail:\n%s", _proxy_log.read_text()[-2000:])
                _proxy_proc = None

        if _port_open(PROXY_PORT):
            return

        _stop_proxy_locked()

        if not os.environ.get("OPENROUTER_API_KEY"):
            env_vars = _load_env_file(HOST_ENV_FILE)
            key = env_vars.get("OPENROUTER_API_KEY", "")
            if key and key != "proxy" and not key.startswith("sk-or-..."):
                os.environ["OPENROUTER_API_KEY"] = key
            else:
                raise RuntimeError(
                    "OPENROUTER_API_KEY not found in env or env/opencode/opencode.env"
                )

        script = PROXY_SCRIPT.resolve()
        if not script.exists():
            raise RuntimeError(f"proxy script missing: {script}")

        LOG_DIR.mkdir(exist_ok=True)
        _proxy_log = LOG_DIR / f"proxy-{int(time.time())}.log"
        log_fh = open(_proxy_log, "w")

        logger.info("proxy starting on :%d (log: %s)", PROXY_PORT, _proxy_log)
        _proxy_proc = subprocess.Popen(
            [sys.executable, str(script), "--port", str(PROXY_PORT)],
            stdout=log_fh,
            stderr=log_fh,
        )
        atexit.register(_stop_proxy)

        for _ in range(30):
            if _proxy_proc.poll() is not None:
                detail = _proxy_log.read_text()[-1000:] if _proxy_log.exists() else ""
                raise RuntimeError(f"proxy exited immediately (exit {_proxy_proc.returncode}):\n{detail}")
            if _port_open(PROXY_PORT):
                logger.info("proxy ready pid=%d", _proxy_proc.pid)
                return
            time.sleep(0.2)
        raise RuntimeError(f"proxy failed to start in 6s, see {_proxy_log}")


def _check_proxy_alive() -> None:
    """Verify the proxy is still responsive. Called before each container run."""
    with _proxy_lock:
        if _proxy_proc is not None:
            rc = _proxy_proc.poll()
            if rc is not None:
                logger.error("proxy dead pid=%d exit=%d", _proxy_proc.pid, rc)
                logger.debug("proxy log tail:\n%s",
                             _proxy_log.read_text()[-500:] if _proxy_log and _proxy_log.exists() else "")
                raise RuntimeError(f"proxy died (exit {rc}), log: {_proxy_log}")

    if not _port_open(PROXY_PORT):
        raise RuntimeError(f"proxy port {PROXY_PORT} not reachable")


def _stop_proxy_locked() -> None:
    """Must be called while holding _proxy_lock."""
    global _proxy_proc
    if _proxy_proc is not None:
        pid = _proxy_proc.pid
        try:
            _proxy_proc.terminate()
            _proxy_proc.wait(timeout=5)
        except Exception:
            _proxy_proc.kill()
            _proxy_proc.wait(timeout=2)
        logger.debug("proxy stopped pid=%d", pid)
        _proxy_proc = None


def _stop_proxy() -> None:
    with _proxy_lock:
        _stop_proxy_locked()


class OpenCodeHarness(BaseHarness):
    def __init__(self, model: str, **kwargs):
        if model not in MODEL2NAME:
            raise ValueError(f"Unknown model: {model!r}. Available: {sorted(MODEL2NAME)}")
        self.model_name = MODEL2NAME[model]

    def run(self, prompt: str, ctx: RunContext) -> AgentResult:
        _setup_file_logging()
        _ensure_proxy()
        _check_proxy_alive()

        env_file = HOST_ENV_FILE.resolve()
        if not env_file.exists():
            raise RuntimeError(f"env file missing: {env_file}")
        config_file = HOST_CONFIG.resolve()
        if not config_file.exists():
            raise RuntimeError(f"config missing: {config_file}")

        image = f"vex-bench-{ctx.language}-opencode:latest"
        logger.debug("run model=%s image=%s src=%s", self.model_name, image, ctx.cwd)
        t0 = time.monotonic()
        output = _run_opencode_and_export(
            image,
            model=self.model_name,
            prompt=prompt,
            src=ctx.cwd,
            timeout=ctx.timeout,
            env_file=env_file,
            config_file=config_file,
            artifacts_dir=ctx.artifacts_dir,
        )
        elapsed = time.monotonic() - t0
        _validate_output(output, elapsed)
        return AgentResult(raw_output=output)

    def result_filename(self) -> str:
        return "result.json"

    def extract_text(self, raw_output: str) -> str:
        export = _parse_export(raw_output)
        if export is not None:
            return _extract_export_text(export)
        return _extract_jsonl_text(_parse_jsonl(raw_output))

    def extract_stats(self, raw_output: str) -> RunStats:
        export = _parse_export(raw_output)
        if export is not None:
            return _extract_export_stats(export)
        return _extract_jsonl_stats(_parse_jsonl(raw_output))

    def load_result(self, run_dir: Path) -> ParsedResult | None:
        for filename in ("result.json", "result.jsonl"):
            parsed = self._load_result_file(run_dir / filename)
            if parsed is not None:
                return parsed
        return None

    def has_result(self, run_dir: Path) -> bool:
        return (run_dir / "result.jsonl").exists() or (run_dir / "result.json").exists()

    def _load_result_file(self, path: Path) -> ParsedResult | None:
        if not path.exists() or path.stat().st_size == 0:
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            text = self.extract_text(raw)
            category, reasoning = parse_category(text)
        except Exception:
            return None
        if category is None:
            return None
        try:
            stats = self.extract_stats(raw)
        except Exception:
            stats = RunStats()
        return ParsedResult(
            path=path, raw=raw, text=text,
            category=category, reasoning=reasoning, stats=stats,
        )


def _validate_output(output: str, elapsed: float) -> None:
    """Reject empty sessions that would be cached as valid results."""
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        raise RuntimeError(f"invalid JSON export ({len(output)}B, {elapsed:.0f}s)")

    tokens = data.get("info", {}).get("tokens", {})
    tok_in = tokens.get("input", 0) or 0
    tok_out = tokens.get("output", 0) or 0
    if tok_in + tok_out == 0:
        raise RuntimeError(f"empty session: 0 tokens ({elapsed:.0f}s)")
    logger.debug("validated: in=%d out=%d %.0fs", tok_in, tok_out, elapsed)


_BILLING_RE = __import__("re").compile(
    r"(?i)(insufficient.{0,20}(credits|funds|balance)"
    r"|quota.{0,10}exceeded"
    r"|billing.{0,10}(error|limit)"
    r"|payment.{0,10}required"
    r"|statusCode.{0,5}(402|429)"
    r"|\"code\"\s*:\s*(402|429))"
)


def _check_for_billing_errors(text: str) -> None:
    """Surface billing/quota errors with ERROR level."""
    for m in _BILLING_RE.finditer(text):
        start = max(0, m.start() - 40)
        end = min(len(text), m.end() + 80)
        context = text[start:end].replace("\n", " ").strip()
        logger.error("BILLING: ...%s...", context)
        return


def _run_opencode_and_export(
    image: str, *, model: str, prompt: str, src: Path,
    timeout: int, env_file: Path, config_file: Path,
    artifacts_dir: Path | None,
) -> str:
    script = _build_export_script(model=model, timeout=timeout)
    cid = _create_container(image, env_file=env_file, config_file=config_file, script=script)
    logger.debug("created %s", cid[:12])

    with tempfile.TemporaryDirectory(prefix="vex-opencode-") as tmp:
        tmpdir = Path(tmp)
        prompt_path = tmpdir / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        try:
            _copy_inputs(cid, src=src, prompt_path=prompt_path, config_file=config_file)
            logger.debug("copied inputs -> %s", cid[:12])
            _start_container(cid, timeout=timeout)
            return _copy_export(cid, tmpdir)
        finally:
            if artifacts_dir is not None:
                _copy_debug_artifacts(cid, artifacts_dir)
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True, check=False)


def _create_container(image: str, *, env_file: Path, config_file: Path, script: str) -> str:
    cmd = [
        "docker", "create", "--workdir", "/work",
        "--network", "host",
        "--env-file", str(env_file),
        "--entrypoint", "sh", image, "-lc", script,
    ]
    try:
        created = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"docker create failed: {exc.stderr.strip() or '(no stderr)'}"
        ) from exc
    return created.stdout.strip()


def _copy_inputs(cid: str, *, src: Path, prompt_path: Path, config_file: Path) -> None:
    _docker_cp_required(f"{src.resolve()}/.", f"{cid}:/work", "docker cp source failed")
    _docker_cp_required(str(prompt_path), f"{cid}:/tmp/prompt.txt", "docker cp prompt failed")
    _docker_cp_required(str(config_file), f"{cid}:{CONTAINER_CONFIG}", "docker cp config failed")


def _start_container(cid: str, *, timeout: int) -> None:
    t0 = time.monotonic()
    try:
        started = subprocess.run(
            ["docker", "start", "-a", cid],
            capture_output=True, text=True, timeout=timeout + 120,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - t0
        raise RuntimeError(
            f"container timed out after {elapsed:.0f}s (limit {timeout}s)"
        ) from exc
    elapsed = time.monotonic() - t0
    if started.returncode != 0:
        stderr = started.stderr.strip()
        stdout_tail = (started.stdout or "").strip()[-4000:]
        _check_for_billing_errors(stderr + "\n" + stdout_tail)
        logger.debug("container stderr:\n%s", stderr[-3000:])
        logger.debug("container stdout tail:\n%s", stdout_tail[-3000:])
        raise RuntimeError(f"container exit={started.returncode} ({elapsed:.0f}s)")
    logger.debug("container done in %.0fs", elapsed)


def _copy_export(cid: str, tmpdir: Path) -> str:
    export_path = tmpdir / "opencode-export.json"
    _docker_cp_required(
        f"{cid}:/tmp/opencode-export.json", str(export_path),
        "docker cp opencode export failed",
    )
    return export_path.read_text(encoding="utf-8")


def _build_export_script(*, model: str, timeout: int) -> str:
    model_arg = shlex.quote(model)
    node_script = (
        "let s='';"
        "process.stdin.on('data',d=>s+=d);"
        "process.stdin.on('end',()=>{"
        "const rows=JSON.parse(s);"
        "if(!rows[0]||!rows[0].id) process.exit(1);"
        "console.log(rows[0].id);"
        "});"
    )
    session_list_cmd = (
        "opencode session list --max-count 1 --format json "
        "> /tmp/session-list.json 2> /tmp/session-list.stderr"
    )
    return f"""
cd /work

# OpenCode requires a git repo for project-scoped storage.
# Only init + empty commit -- skip 'git add' on large trees to save time.
if [ ! -d .git ]; then
  git init -q
  git config user.email "bench@vex-bench.local"
  git config user.name "VEX-Bench"
  git commit -q --allow-empty -m "init"
fi

# Pre-create OpenCode data/cache dirs
mkdir -p /root/.local/share/opencode/log /root/.cache/opencode

# Run OpenCode -- capture output and check exit code explicitly
timeout {int(timeout)}s opencode run --print-logs --model {model_arg} \
  --thinking --dangerously-skip-permissions "$(cat /tmp/prompt.txt)" \
  > /tmp/opencode-stdout.txt 2> /tmp/opencode-stderr.log
RC=$?
if [ $RC -ne 0 ]; then
  echo "=== opencode run exited $RC ===" >&2
  echo "--- stderr ---" >&2
  cat /tmp/opencode-stderr.log >&2
  echo "--- server logs ---" >&2
  cat /root/.local/share/opencode/log/*.log 2>/dev/null >&2 || true
  echo "--- stdout tail ---" >&2
  tail -50 /tmp/opencode-stdout.txt >&2
  exit $RC
fi

set -e
{session_list_cmd}
node -e {shlex.quote(node_script)} < /tmp/session-list.json > /tmp/session-id
opencode export "$(cat /tmp/session-id)" > /tmp/opencode-export.json 2> /tmp/opencode-export.stderr
""".strip()


def _parse_export(text: str) -> dict | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
        return None
    return data


def _extract_export_text(export: dict) -> str:
    for message in reversed(export.get("messages", [])):
        if message.get("info", {}).get("role") != "assistant":
            continue
        for part in reversed(message.get("parts", [])):
            if part.get("type") == "text" and part.get("text"):
                return part["text"].strip()
    return ""


def _extract_export_stats(export: dict) -> RunStats:
    parts = [
        part
        for message in export.get("messages", [])
        if message.get("info", {}).get("role") == "assistant"
        for part in message.get("parts", [])
    ]
    steps = sum(1 for part in parts if part.get("type") == "step-start")
    completed = any(part.get("type") == "text" and part.get("text") for part in parts)
    tokens = [
        part["tokens"]
        for part in parts
        if part.get("type") == "step-finish" and isinstance(part.get("tokens"), dict)
    ]
    if not tokens:
        return RunStats(steps=steps, completed=completed)
    return RunStats(
        steps=steps,
        completed=completed,
        input_tokens=sum(t.get("input", 0) for t in tokens),
        cached_input_tokens=sum(t.get("cache", {}).get("read", 0) for t in tokens),
        cache_write_tokens=sum(t.get("cache", {}).get("write", 0) for t in tokens),
        output_tokens=sum(t.get("output", 0) for t in tokens),
        reasoning_tokens=sum(t.get("reasoning", 0) for t in tokens),
    )


def _parse_jsonl(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


def _extract_jsonl_text(events: list[dict]) -> str:
    texts = [
        e["part"]["text"]
        for e in events
        if e.get("type") == "text" and e.get("part", {}).get("text")
    ]
    return texts[-1].strip() if texts else ""


def _extract_jsonl_stats(events: list[dict]) -> RunStats:
    steps = sum(1 for e in events if e.get("type") == "step_start")
    completed = any(e.get("type") == "text" for e in events)
    tokens = [
        e["part"]["tokens"]
        for e in events
        if e.get("type") == "step_finish" and isinstance(e.get("part", {}).get("tokens"), dict)
    ]
    if not tokens:
        return RunStats(steps=steps, completed=completed)
    return RunStats(
        steps=steps,
        completed=completed,
        input_tokens=sum(t.get("input", 0) for t in tokens),
        cached_input_tokens=sum(t.get("cache", {}).get("read", 0) for t in tokens),
        cache_write_tokens=sum(t.get("cache", {}).get("write", 0) for t in tokens),
        output_tokens=sum(t.get("output", 0) for t in tokens),
        reasoning_tokens=sum(t.get("reasoning", 0) for t in tokens),
    )


def _copy_debug_artifacts(cid: str, artifacts_dir: Path) -> None:
    artifacts = [
        ("/tmp/opencode-export.json", "opencode-export.json"),
        ("/tmp/opencode-stdout.txt", "opencode-stdout.txt"),
        ("/tmp/opencode-stderr.log", "opencode-stderr.log"),
        ("/tmp/session-list.json", "session-list.json"),
        ("/root/.local/share/opencode/log", "opencode-log"),
    ]
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    for container_path, name in artifacts:
        dest = artifacts_dir / name
        if dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        subprocess.run(
            ["docker", "cp", f"{cid}:{container_path}", str(dest)],
            capture_output=True, check=False,
        )


def _docker_cp_required(src: str, dst: str, message: str) -> None:
    result = subprocess.run(["docker", "cp", src, dst], capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "(no output)"
        raise RuntimeError(f"{message}: {detail}")
