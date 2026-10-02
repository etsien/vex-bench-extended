"""Claude Code CLI harness -- runs inside disposable Docker containers.

Adapted from upstream VEX-Bench. Claude Code is installed INSIDE the
Docker image, not invoked from the host. This means:
  - Your local ~/.claude/ config, CLAUDE.md, MCP servers, etc. are
    NOT loaded.
  - The agent sees only the source tree (copied in via docker cp)
    and the API credentials from env/claude/claude.env.
  - The container is destroyed after each run.

This is the same isolation model used in the original VEX-Bench paper.
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

HOST_ENV_FILE = Path("env/claude/claude.env")

EFFORT_BY_MODEL = {
    "claude-sonnet-4-6": "high",
    "claude-opus-4-6": "high",
}


class ClaudeCodeHarness(BaseHarness):
    """Claude Code CLI in a disposable Docker container.

    Does NOT use the host's claude binary or ~/.claude/ config.
    """

    def __init__(self, model: str, **kwargs):
        self.model = model

    def run(self, prompt: str, ctx: RunContext) -> AgentResult:
        env_file = HOST_ENV_FILE.resolve()
        if not env_file.exists():
            raise RuntimeError(f"Env file not found: {env_file}")

        image = f"vex-bench-{ctx.language}-claude:latest"
        args = [
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--model", self.model,
            "--dangerously-skip-permissions",
        ]
        effort = EFFORT_BY_MODEL.get(self.model)
        if effort is not None:
            args += ["--effort", effort]
        args.append(prompt)

        stdout = run_in_container(
            image, args,
            entrypoint="claude",
            src=ctx.cwd,
            timeout=ctx.timeout,
            env_file=env_file,
            network="host",
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
