"""Vertex AI Claude Code harness -- runs inside disposable Docker containers.

Runs Claude Code CLI pointed at a Vertex AI proxy on the host.
Your setup routes Claude Code through a localhost proxy that handles
Vertex AI auth and region routing. The Docker container uses
--network host to reach it.

Like the standard Claude Code harness, execution happens inside a
disposable Docker container. Claude Code is installed in the image,
not invoked from the host. Your local ~/.claude/ config, CLAUDE.md,
MCP servers, etc. are NOT loaded. The agent sees only:
  - The source tree (copied in via docker cp)
  - The proxy connection via env/vertex_ai/vertex.env
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from harnesses.base import (
    AgentResult,
    BaseHarness,
    ParsedResult,
    RunContext,
    RunStats,
    run_in_container,
)
from evaluate.result_parser import parse_category

logger = logging.getLogger(__name__)

HOST_ENV_FILE = Path("env/vertex_ai/vertex.env")
GCP_ADC_PATH = Path.home() / ".config/gcloud/application_default_credentials.json"
CONTAINER_ADC_PATH = "/tmp/gcp-adc.json"

VERTEX_MODELS = {
    "claude-opus-4-6",
}

EFFORT_BY_MODEL = {
    "claude-opus-4-6": "high",
}


class VertexClaudeCodeHarness(BaseHarness):
    """Claude Code CLI backed by a Vertex AI proxy.

    Runs inside a disposable Docker container with --network host so
    it can reach the localhost proxy. Does NOT use the host's claude
    binary or ~/.claude/ config.
    """

    def __init__(self, model: str, **kwargs):
        if model not in VERTEX_MODELS:
            raise ValueError(
                f"Model {model!r} not registered for Vertex AI harness. "
                f"Available: {sorted(VERTEX_MODELS)}"
            )
        self.model = model

    def run(self, prompt: str, ctx: RunContext) -> AgentResult:
        import tempfile as _tempfile

        env_file = HOST_ENV_FILE.resolve()
        if not env_file.exists():
            raise RuntimeError(
                f"Vertex AI env file not found: {env_file}. "
                "Copy env/vertex_ai/vertex.env.example and fill in credentials."
            )

        image = f"vex-bench-{ctx.language}-claude:latest"
        effort = EFFORT_BY_MODEL.get(self.model)
        effort_flag = f"--effort {effort} " if effort else ""

        script = (
            f'claude -p --output-format stream-json --verbose '
            f'--model {self.model} --dangerously-skip-permissions '
            f'{effort_flag}"$(cat /tmp/prompt.txt)"'
        )

        with _tempfile.TemporaryDirectory(prefix="vex-vertex-") as tmp:
            prompt_path = Path(tmp) / "prompt.txt"
            prompt_path.write_text(prompt, encoding="utf-8")

            mounts = [(prompt_path, "/tmp/prompt.txt")]
            if GCP_ADC_PATH.exists():
                mounts.append((GCP_ADC_PATH, CONTAINER_ADC_PATH))

            stdout = run_in_container(
                image, ["-lc", script],
                entrypoint="sh",
                src=ctx.cwd,
                timeout=ctx.timeout,
                env_file=env_file,
                network="host",
                workdir="/home/bench/work",
                mounts=mounts,
            )
        return AgentResult(raw_output=stdout)

    def result_filename(self) -> str:
        return "result.jsonl"

    def extract_text(self, jsonl_text: str) -> str:
        events = _parse_jsonl(jsonl_text)
        for e in reversed(events):
            if e.get("type") == "result":
                final = e.get("result")
                if isinstance(final, str) and final.strip():
                    return final.strip()
                break
        for e in reversed(events):
            if e.get("type") == "assistant":
                msg = e.get("message", {})
                content = msg.get("content", [])
                texts = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                joined = "\n".join(t for t in texts if t).strip()
                if joined:
                    return joined
        return ""

    def extract_stats(self, jsonl_text: str) -> RunStats:
        events = _parse_jsonl(jsonl_text)
        result_event = next(
            (e for e in reversed(events) if e.get("type") == "result"), None
        )
        if result_event is None:
            return RunStats()

        completed = (
            result_event.get("subtype") == "success"
            and not result_event.get("is_error", False)
        )
        steps = result_event.get("num_turns")

        per_model = [
            m
            for m in (result_event.get("modelUsage") or {}).values()
            if isinstance(m, dict)
        ]
        if not per_model:
            return RunStats(steps=steps, completed=completed)
        return RunStats(
            steps=steps,
            completed=completed,
            input_tokens=sum(m.get("inputTokens", 0) for m in per_model),
            cached_input_tokens=sum(m.get("cacheReadInputTokens", 0) for m in per_model),
            cache_write_tokens=sum(m.get("cacheCreationInputTokens", 0) for m in per_model),
            output_tokens=sum(m.get("outputTokens", 0) for m in per_model),
        )

    def load_result(self, run_dir: Path) -> ParsedResult | None:
        path = run_dir / "result.jsonl"
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

    def has_result(self, run_dir: Path) -> bool:
        return (run_dir / "result.jsonl").exists()


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
