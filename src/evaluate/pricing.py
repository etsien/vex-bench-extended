"""Per-model token pricing.

Extended from upstream VEX-Bench to cover Cursor-native and
Vertex AI-hosted models.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from harnesses.base import RunStats

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1M tokens for each disjoint billing bucket."""

    input: float
    cached_input: float
    cache_write: float
    output: float


PRICING: dict[str, ModelPrice] = {
    # Original VEX-Bench models
    "gpt-5.5": ModelPrice(input=5.00, cached_input=0.50, cache_write=5.00, output=30.00),
    "gpt-5.4-mini": ModelPrice(input=0.75, cached_input=0.075, cache_write=0.75, output=4.50),
    # OpenRouter rates (includes provider margin)
    "kimi-k2.6": ModelPrice(input=0.58, cached_input=0.10, cache_write=0.58, output=3.40),
    "deepseek-v4-pro": ModelPrice(
        input=0.87, cached_input=0.087, cache_write=0.87, output=1.74,
    ),
    "deepseek-v4-flash": ModelPrice(
        input=0.068, cached_input=0.007, cache_write=0.068, output=0.168,
    ),
    "minimax-m2.7": ModelPrice(
        input=0.24, cached_input=0.024, cache_write=0.24, output=0.96,
    ),
    "glm-5.1": ModelPrice(
        input=0.965, cached_input=0.179, cache_write=0.965, output=3.032,
    ),
    # Claude models (direct API)
    "claude-opus-4-6": ModelPrice(
        input=5.00, cached_input=0.50, cache_write=6.25, output=25.00,
    ),
    "claude-sonnet-4-6": ModelPrice(
        input=3.00, cached_input=0.30, cache_write=3.75, output=15.00,
    ),
    # Cursor-native models (pricing may vary by Cursor plan)
    "grok-3": ModelPrice(input=3.00, cached_input=0.30, cache_write=3.00, output=15.00),
    "gemini-2.5-pro": ModelPrice(
        input=1.25, cached_input=0.3125, cache_write=1.25, output=10.00,
    ),
    # Vertex AI hosted (same Claude pricing, routed through proxy)
    "vertex-claude-opus-4-6": ModelPrice(
        input=5.00, cached_input=0.50, cache_write=6.25, output=25.00,
    ),
}


def cost_usd(stats: RunStats, model: str) -> float | None:
    price = PRICING.get(model)
    if price is None:
        logger.debug("no pricing for model %r", model)
        return None
    if all(
        b is None
        for b in (
            stats.input_tokens,
            stats.cached_input_tokens,
            stats.cache_write_tokens,
            stats.output_tokens,
            stats.reasoning_tokens,
        )
    ):
        return None
    return (
        (stats.input_tokens or 0) * price.input
        + (stats.cached_input_tokens or 0) * price.cached_input
        + (stats.cache_write_tokens or 0) * price.cache_write
        + ((stats.output_tokens or 0) + (stats.reasoning_tokens or 0)) * price.output
    ) / 1_000_000
