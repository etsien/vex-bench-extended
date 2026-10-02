from __future__ import annotations

from collections.abc import Callable

from harnesses.base import AgentResult, BaseHarness, ParsedResult, RunContext, RunStats
from harnesses.claude_code import ClaudeCodeHarness
from harnesses.codex import CodexHarness
from harnesses.cursor_harness import CursorHarness
from harnesses.opencode import OpenCodeHarness
from harnesses.vertex_claude_code import VertexClaudeCodeHarness
from models import Harness

HARNESS_REGISTRY: dict[Harness, Callable[..., BaseHarness]] = {
    Harness.CLAUDE_CODE: ClaudeCodeHarness,
    Harness.CODEX: CodexHarness,
    Harness.OPENCODE: OpenCodeHarness,
    Harness.CURSOR: CursorHarness,
    Harness.VERTEX_CLAUDE_CODE: VertexClaudeCodeHarness,
}

__all__ = [
    "HARNESS_REGISTRY",
    "AgentResult",
    "BaseHarness",
    "ParsedResult",
    "RunContext",
    "RunStats",
]
