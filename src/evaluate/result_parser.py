"""Parse agent output into structured categories.

Identical to upstream VEX-Bench result_parser -- kept in sync so that
results from the original benchmark and this extended version are
directly comparable.
"""

import json
import re

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)

CATEGORY_ORDER: list[str] = [
    "false_positive",
    "code_not_present",
    "code_not_reachable",
    "requires_configuration",
    "requires_dependency",
    "requires_environment",
    "compiler_protected",
    "runtime_protected",
    "perimeter_protected",
    "mitigating_control_protected",
    "uncertain",
    "vulnerable",
]
VALID_CATEGORIES: frozenset[str] = frozenset(CATEGORY_ORDER)
BINARY_LABELS: list[str] = ["not_exploitable", "exploitable"]


def parse_category(text: str) -> tuple[str | None, str | None]:
    return _parse_json(text)


def has_valid_category(text: str) -> bool:
    return parse_category(text)[0] is not None


def _parse_json(text: str) -> tuple[str | None, str | None]:
    obj = _extract_json_object(text)
    if not isinstance(obj, dict):
        return None, None
    cat = obj.get("category")
    if not isinstance(cat, str) or cat.lower() not in VALID_CATEGORIES:
        return None, None
    reasoning = obj.get("reasoning")
    return cat.lower(), reasoning if isinstance(reasoning, str) and reasoning.strip() else None


def _extract_json_object(text: str) -> dict | None:
    stripped = text.strip()
    fence = _FENCE_RE.match(stripped)
    if fence:
        stripped = fence.group(1).strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    depth = 0
    start = -1
    for i, ch in enumerate(stripped):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(stripped[start : i + 1])
                except json.JSONDecodeError:
                    start = -1
    return None


def to_binary(category: str | None) -> str | None:
    if category is None:
        return None
    if category == "vulnerable":
        return "exploitable"
    if category in VALID_CATEGORIES:
        return "not_exploitable"
    return None


def normalize_ground_truth(raw: str) -> str:
    cleaned = raw.strip().lower().replace(" ", "_")
    if cleaned == "exploitable":
        return "exploitable"
    return "not_exploitable"
