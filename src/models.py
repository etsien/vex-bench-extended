"""Model and harness matrix definitions.

Centralises every model x harness combination the benchmark can exercise.
Each ModelSpec carries the provider-specific identifiers, which harnesses
can run it, and the prompt variant best suited to the model family.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Harness(str, Enum):
    CODEX = "codex"
    OPENCODE = "opencode"
    CURSOR = "cursor"
    VERTEX_CLAUDE_CODE = "vertex_claude_code"


class PromptVariant(str, Enum):
    DEFAULT = "default"
    CONCISE = "concise"
    COT = "chain_of_thought"


@dataclass(frozen=True)
class ModelSpec:
    name: str
    provider_id: str
    harnesses: tuple[Harness, ...]
    prompt_variant: PromptVariant = PromptVariant.DEFAULT
    effort: str | None = None
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Cursor-native models (run via Cursor SDK / CLI agent)
# ---------------------------------------------------------------------------
CURSOR_MODELS: list[ModelSpec] = [
    ModelSpec(
        name="claude-opus-4-6",
        provider_id="claude-opus-4-6",
        harnesses=(Harness.CURSOR,),
        effort="high",
    ),
    ModelSpec(
        name="claude-sonnet-4-6",
        provider_id="claude-sonnet-4-6",
        harnesses=(Harness.CURSOR,),
        effort="high",
    ),
    ModelSpec(
        name="grok-4.6",
        provider_id="grok-4.6",
        harnesses=(Harness.CURSOR,),
    ),
    ModelSpec(
        name="composer-2.5",
        provider_id="composer-2.5",
        harnesses=(Harness.CURSOR,),
    ),
]

# ---------------------------------------------------------------------------
# Vertex AI hosted models (run via Claude Code pointed at Vertex proxy)
#
# Claude Code CLI validates model names against the Claude family, so only
# Claude models can be routed through the vertex_claude_code harness.
# Gemini is covered by the Cursor SDK harness above.
# ---------------------------------------------------------------------------
VERTEX_MODELS: list[ModelSpec] = [
    ModelSpec(
        name="vertex-claude-opus-4-6",
        provider_id="claude-opus-4-6",
        harnesses=(Harness.VERTEX_CLAUDE_CODE,),
        effort="high",
    ),
    ModelSpec(
        name="vertex-claude-sonnet-4-6",
        provider_id="claude-sonnet-4-6",
        harnesses=(Harness.VERTEX_CLAUDE_CODE,),
        effort="high",
    ),
    ModelSpec(
        name="vertex-claude-opus-5",
        provider_id="claude-opus-5",
        harnesses=(Harness.VERTEX_CLAUDE_CODE,),
        effort="high",
    ),
    ModelSpec(
        name="vertex-claude-sonnet-5",
        provider_id="claude-sonnet-5",
        harnesses=(Harness.VERTEX_CLAUDE_CODE,),
        effort="high",
    ),
]

# ---------------------------------------------------------------------------
# Original VEX-Bench models (retained for comparison baselines)
# ---------------------------------------------------------------------------
ORIGINAL_MODELS: list[ModelSpec] = [
    ModelSpec(
        name="gpt-5.5",
        provider_id="gpt-5.5",
        harnesses=(Harness.CODEX,),
    ),
    ModelSpec(
        name="gpt-5.4-mini",
        provider_id="gpt-5.4-mini",
        harnesses=(Harness.CODEX,),
    ),
    ModelSpec(
        name="kimi-k2.6",
        provider_id="kimi-k2.6",
        harnesses=(Harness.OPENCODE,),
    ),
    ModelSpec(
        name="deepseek-v4-pro",
        provider_id="deepseek-v4-pro",
        harnesses=(Harness.OPENCODE,),
    ),
    ModelSpec(
        name="deepseek-v4-flash",
        provider_id="deepseek-v4-flash",
        harnesses=(Harness.OPENCODE,),
    ),
    ModelSpec(
        name="minimax-m2.7",
        provider_id="minimax-m2.7",
        harnesses=(Harness.OPENCODE,),
    ),
    ModelSpec(
        name="glm-5.1",
        provider_id="glm-5.1",
        harnesses=(Harness.OPENCODE,),
    ),
]

ALL_MODELS: list[ModelSpec] = CURSOR_MODELS + VERTEX_MODELS + ORIGINAL_MODELS

MODEL_REGISTRY: dict[str, ModelSpec] = {m.name: m for m in ALL_MODELS}


def models_for_harness(harness: Harness) -> list[ModelSpec]:
    return [m for m in ALL_MODELS if harness in m.harnesses]


def resolve_matrix(
    models: list[str] | None = None,
    harnesses: list[str] | None = None,
) -> list[tuple[ModelSpec, Harness]]:
    """Return every valid (model, harness) pair matching the filters."""
    pairs: list[tuple[ModelSpec, Harness]] = []
    for spec in ALL_MODELS:
        if models and spec.name not in models:
            continue
        for h in spec.harnesses:
            if harnesses and h.value not in harnesses:
                continue
            pairs.append((spec, h))
    return pairs
