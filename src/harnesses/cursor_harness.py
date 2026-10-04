"""Cursor SDK harness -- runs models via Cursor local agents with isolation.

Targets models accessible through Cursor: Grok, Claude Opus variants,
Gemini. Uses the Cursor SDK to spawn **local agents** with no ambient
settings loaded (setting_sources is empty by default in the Python SDK).

Why local, not cloud:
  VEX-Bench removes git history from repo snapshots to prevent agents
  from finding the source PRs and leaking benchmark answers. Cloud
  agents clone from GitHub and would have full git history, violating
  the benchmark's anti-leakage design (paper Section 3.4). Local agents
  operate on the pre-prepared snapshot with .git stripped.

Isolation guarantees:
  - setting_sources is NOT set (Python SDK default = inline config only)
  - No user rules, skills, MCP servers, or hooks are loaded
  - Source tree is copied to a temp directory so the snapshot is not mutated
  - No .cursor/ directory exists in the temp copy

Requirements:
  - cursor-sdk Python package installed
  - CURSOR_API_KEY set in env/cursor/cursor.env (or environment)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

from harnesses.base import AgentResult, BaseHarness, ParsedResult, RunContext, RunStats
from evaluate.result_parser import parse_category

logger = logging.getLogger(__name__)

HOST_ENV_FILE = Path("env/cursor/cursor.env")

CURSOR_MODELS = {
    "grok-3",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "gemini-2.5-pro",
}


class CursorHarness(BaseHarness):
    """Execute a VEX-Bench task via the Cursor SDK (local, no ambient config).

    Each task runs as a local agent against a temp copy of the source tree.
    setting_sources is not set (Python SDK default = empty = inline only),
    so no user rules, skills, MCP servers, or hooks are loaded.
    """

    def __init__(self, model: str, **kwargs):
        if model not in CURSOR_MODELS:
            raise ValueError(
                f"Model {model!r} not registered for Cursor harness. "
                f"Available: {sorted(CURSOR_MODELS)}"
            )
        self.model = model
        self._api_key: str | None = None

    def _get_api_key(self) -> str:
        """Return the API key from env file or environment."""
        if self._api_key:
            return self._api_key

        # Try env file
        env_file = HOST_ENV_FILE.resolve()
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("CURSOR_API_KEY=") and not line.startswith("#"):
                    val = line.split("=", 1)[1].strip().strip("'\"")
                    if val:
                        self._api_key = val
                        return self._api_key

        # Try environment variable
        key = os.environ.get("CURSOR_API_KEY")
        if key:
            self._api_key = key
            return self._api_key

        raise RuntimeError(
            "CURSOR_API_KEY not found. Set it in env/cursor/cursor.env "
            "or export it before running. Get a key at: "
            "https://cursor.com/dashboard/integrations"
        )

    def run(self, prompt: str, ctx: RunContext) -> AgentResult:
        import time as _time

        try:
            from cursor_sdk import Agent, AgentOptions, LocalAgentOptions, CursorAgentError
        except ImportError:
            raise RuntimeError(
                "cursor-sdk not installed. Install with: pip install cursor-sdk"
            )

        api_key = self._get_api_key()

        with tempfile.TemporaryDirectory(prefix="vex-cursor-") as tmp:
            work_dir = Path(tmp) / "work"
            shutil.copytree(ctx.cwd, work_dir)

            cursor_dir = work_dir / ".cursor"
            if cursor_dir.exists():
                shutil.rmtree(cursor_dir)

            logger.debug("run model=%s src=%s", self.model, ctx.cwd.name)
            t0 = _time.monotonic()
            try:
                result = Agent.prompt(
                    prompt,
                    AgentOptions(
                        api_key=api_key,
                        model=self.model,
                        local=LocalAgentOptions(cwd=str(work_dir)),
                    ),
                )
            except CursorAgentError as exc:
                elapsed = _time.monotonic() - t0
                raise RuntimeError(
                    f"cursor agent error ({elapsed:.0f}s): {exc.message} "
                    f"(retryable={exc.is_retryable})"
                ) from exc

            elapsed = _time.monotonic() - t0
            result_obj = {
                "type": "cursor_local_isolated",
                "model": self.model,
                "agent_id": getattr(result, "agent_id", None),
                "run_id": getattr(result, "id", None),
                "status": getattr(result, "status", None),
                "output": getattr(result, "result", "") or "",
            }
            output = result_obj.get("output", "")
            if not output or not output.strip():
                raise RuntimeError(f"empty cursor response ({elapsed:.0f}s)")
            logger.debug("done model=%s %.0fs output=%dB", self.model, elapsed, len(output))
            return AgentResult(raw_output=json.dumps(result_obj, default=str))

    def result_filename(self) -> str:
        return "result.json"

    def extract_text(self, raw_output: str) -> str:
        try:
            data = json.loads(raw_output)
        except json.JSONDecodeError:
            return raw_output.strip()

        if isinstance(data, dict):
            output = data.get("output", "")
            if output and output.strip():
                return output.strip()
        return raw_output.strip()

    def extract_stats(self, raw_output: str) -> RunStats:
        try:
            data = json.loads(raw_output)
        except json.JSONDecodeError:
            return RunStats()

        if not isinstance(data, dict):
            return RunStats()

        usage = data.get("usage", {})
        return RunStats(
            completed=data.get("status") == "finished",
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cached_input_tokens=usage.get("cached_input_tokens"),
            cache_write_tokens=usage.get("cache_write_tokens"),
            reasoning_tokens=usage.get("reasoning_tokens"),
        )

    def load_result(self, run_dir: Path) -> ParsedResult | None:
        path = run_dir / "result.json"
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
        return (run_dir / "result.json").exists()
