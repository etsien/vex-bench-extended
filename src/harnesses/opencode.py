"""OpenCode CLI harness -- adapted from upstream VEX-Bench."""

from __future__ import annotations

import json
import logging
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from harnesses.base import AgentResult, BaseHarness, ParsedResult, RunContext, RunStats
from evaluate.result_parser import parse_category

logger = logging.getLogger(__name__)

HOST_ENV_FILE = Path("env/opencode/opencode.env")
HOST_CONFIG = Path("env/opencode/opencode.json")
CONTAINER_CONFIG = "/root/.config/opencode/opencode.json"

MODEL2NAME = {
    "kimi-k2.6": "openrouter-proxy/moonshotai/kimi-k2.6",
    "deepseek-v4-pro": "openrouter-proxy/deepseek/deepseek-v4-pro",
    "deepseek-v4-flash": "openrouter-proxy/deepseek/deepseek-v4-flash",
    "minimax-m2.7": "openrouter-proxy/minimax/minimax-m2.7",
    "glm-5.1": "openrouter-proxy/z-ai/glm-5.1",
}


class OpenCodeHarness(BaseHarness):
    def __init__(self, model: str, **kwargs):
        if model not in MODEL2NAME:
            raise ValueError(f"Unknown model: {model!r}. Available: {sorted(MODEL2NAME)}")
        self.model_name = MODEL2NAME[model]

    def run(self, prompt: str, ctx: RunContext) -> AgentResult:
        env_file = HOST_ENV_FILE.resolve()
        if not env_file.exists():
            raise RuntimeError(f"Env file not found: {env_file}")
        config_file = HOST_CONFIG.resolve()
        if not config_file.exists():
            raise RuntimeError(f"Config file not found: {config_file}")

        image = f"vex-bench-{ctx.language}-opencode:latest"
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


def _run_opencode_and_export(
    image: str, *, model: str, prompt: str, src: Path,
    timeout: int, env_file: Path, config_file: Path,
    artifacts_dir: Path | None,
) -> str:
    script = _build_export_script(model=model, timeout=timeout)
    cid = _create_container(image, env_file=env_file, config_file=config_file, script=script)

    with tempfile.TemporaryDirectory(prefix="vex-opencode-") as tmp:
        tmpdir = Path(tmp)
        prompt_path = tmpdir / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        try:
            _copy_inputs(cid, src=src, prompt_path=prompt_path, config_file=config_file)
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
    try:
        started = subprocess.run(
            ["docker", "start", "-a", cid],
            capture_output=True, text=True, timeout=timeout + 120,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"container timed out after {timeout}s"
        ) from exc
    if started.returncode != 0:
        stderr = started.stderr.strip()
        stdout_tail = (started.stdout or "").strip()[-4000:]
        combined = f"container exited {started.returncode}"
        if stderr:
            combined += f"\nstderr: {stderr[-2000:]}"
        if stdout_tail:
            combined += f"\nstdout tail: {stdout_tail}"
        logger.error(combined)
        raise RuntimeError(combined)


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

# OpenCode requires a git repo for project-scoped storage
if [ ! -d .git ]; then
  git init -q
  git config user.email "bench@vex-bench.local"
  git config user.name "VEX-Bench"
  git add -A
  git commit -q -m "bench snapshot" --allow-empty
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
