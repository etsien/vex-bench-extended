"""Codex CLI harness -- adapted from upstream VEX-Bench."""

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

HOST_ENV_FILE = Path("env/codex/codex.env")
HOST_CONFIG = Path("env/codex/config.toml")
CONTAINER_CONFIG = "/tmp/.codex/config.toml"


class CodexHarness(BaseHarness):
    def __init__(self, model: str, **kwargs):
        self.model = model

    def run(self, prompt: str, ctx: RunContext) -> AgentResult:
        import tempfile as _tempfile

        env_file = HOST_ENV_FILE.resolve()
        if not env_file.exists():
            raise RuntimeError(f"Env file not found: {env_file}")
        config_file = HOST_CONFIG.resolve()
        if not config_file.exists():
            raise RuntimeError(f"Config file not found: {config_file}")

        image = f"vex-bench-{ctx.language}-codex:latest"
        script = (
            f'codex exec --sandbox danger-full-access --json '
            f'--skip-git-repo-check --model {self.model} '
            f'"$(cat /tmp/prompt.txt)"'
        )

        with _tempfile.TemporaryDirectory(prefix="vex-codex-") as tmp:
            prompt_path = Path(tmp) / "prompt.txt"
            prompt_path.write_text(prompt, encoding="utf-8")

            stdout = run_in_container(
                image, ["-lc", script],
                entrypoint="sh",
                src=ctx.cwd,
                timeout=ctx.timeout,
                env_file=env_file,
                mounts=[
                    (config_file, CONTAINER_CONFIG),
                    (prompt_path, "/tmp/prompt.txt"),
                ],
            )
        return AgentResult(raw_output=stdout)

    def result_filename(self) -> str:
        return "result.jsonl"

    def extract_text(self, jsonl_text: str) -> str:
        events = _parse_jsonl(jsonl_text)
        texts = [t for e in events if (t := _agent_message_text(e))]
        return texts[-1].strip() if texts else ""

    def extract_stats(self, jsonl_text: str) -> RunStats:
        events = _parse_jsonl(jsonl_text)
        steps = sum(1 for e in events if e.get("type") == "item.completed")
        completed = any(e.get("type") == "turn.completed" for e in events)
        usage = next(
            (
                e["usage"]
                for e in events
                if e.get("type") == "turn.completed" and isinstance(e.get("usage"), dict)
            ),
            {},
        )
        cached = usage.get("cached_input_tokens")
        reasoning = usage.get("reasoning_output_tokens")
        return RunStats(
            steps=steps,
            completed=completed,
            input_tokens=_minus_nested(usage.get("input_tokens"), cached),
            cached_input_tokens=cached,
            output_tokens=_minus_nested(usage.get("output_tokens"), reasoning),
            reasoning_tokens=reasoning,
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


def _minus_nested(total: int | None, nested: int | None) -> int | None:
    if total is None:
        return None
    return max(0, total - (nested or 0))


def _agent_message_text(event: dict) -> str | None:
    if event.get("type") != "item.completed":
        return None
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "agent_message":
        return None
    text = item.get("text")
    return text if isinstance(text, str) else None


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
