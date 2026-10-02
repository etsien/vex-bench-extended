"""Prompt templates for VEX-Bench evaluation.

The DEFAULT prompt is the original VEX-Bench prompt from the paper.
Additional variants adapt the framing for model families that respond
better to different instruction styles while keeping the classification
schema identical.
"""

from __future__ import annotations

from models import PromptVariant

# ---------------------------------------------------------------------------
# Shared classification schema (identical across all variants)
# ---------------------------------------------------------------------------
CLASSIFICATION_BLOCK = """
<CLASSIFICATION_CATEGORIES>
The 12 categories are listed below in STRICT LOGICAL PRECEDENCE ORDER. When evaluating, walk through the list from top to bottom and select the FIRST category that applies. Do not skip ahead.

1. "false_positive"
 - The CVE-to-package mapping is wrong (named package is not what the CVE applies to, or the CVE is withdrawn/malformed). Use only with concrete evidence of mismatch.

2. "code_not_present"
 - The vulnerable package/module is absent from the repository: not declared in any manifest, not in any lockfile, no vendored copy.

3. "code_not_reachable"
 - The vulnerable code is present (manifest, lockfile, or vendored copy) in the codebase but is never executed at runtime, e.g., never imported, referenced, or called from first-party source.
 - Only applicable when code IS present AND call-chain/reachability analysis confirms no execution path leads to it.

4. "requires_configuration"
 - Exploitation requires a specific configuration option, feature flag, or setting that is currently disabled by default in this repository.

5. "requires_dependency"
 - Exploitation requires an additional dependency (library, plugin, module) that this repository does not declare.

6. "requires_environment"
 - Exploitation requires a specific runtime environment (OS, architecture, kernel version, hardware feature) that this repository does not establish or rely on.

7. "compiler_protected"
 - Compile-time hardening configured in this repository's build (stack canaries, PIE/ASLR-required builds, sanitizer instrumentation, etc.) prevents the exploit primitive.

8. "runtime_protected"
 - Runtime mechanisms set up by this repository's own code (sandboxing, in-process isolation, seccomp filters) prevent exploitation.

9. "perimeter_protected"
 - Network, authentication, or perimeter controls shipped in this repository (auth middleware, allowlists, network policies in deployment manifests) block the attack surface.

10. "mitigating_control_protected"
 - Other in-repo mitigations not covered by 7-9 (input validation, output encoding, custom guards) reduce risk to negligible.

11. "uncertain"
 - Investigation cannot establish presence, reachability, or mitigation status with the available evidence. Use as a true fallback, not a hedge.

12. "vulnerable"
 - All of:
 - an affected version of the vulnerable package is present,
 - the vulnerable surface is imported and called from first-party (non-test) source,
 - no mitigation from categories 4-10 applies.
</CLASSIFICATION_CATEGORIES>
""".strip()

DECISION_RULES_BLOCK = """
<DECISION_RULES>
1. A CVE is classified as "vulnerable" if and only if ALL of the following hold:
 - The vulnerable code is PRESENT in the container/codebase.
 - The vulnerable code is USED or CALLED by the application.
 - The vulnerable code is REACHABLE from an attack surface (user input, network input, file processing, IPC, etc.).
 - No effective mitigations or protections are in place.

2. If ANY of the above conditions fails, select the SINGLE most appropriate non-vulnerable category by walking the precedence list from top (1) to bottom (11) and choosing the first matching category. For example:
 - If the vulnerable code is not present -> "code_not_present" (do NOT also consider "code_not_reachable" or environment factors).
 - If the code is present but unreachable -> "code_not_reachable" (do NOT fall through to "requires_environment").
 - If a required dependency is missing -> "requires_dependency".
 - If the vulnerable code is prevented by a default or clearly set configuration -> "requires_configuration".

3. Use "uncertain" only when the investigation genuinely lacks the evidence needed to reach any conclusion.
</DECISION_RULES>
""".strip()

OUTPUT_FORMAT_BLOCK = """
<OUTPUT_FORMAT>
Output a single JSON object on stdout -- no surrounding prose, no markdown fences, no comments.

Schema:
{
 "category": "<one of the 12 category names, exact snake_case>",
 "reasoning": "<evidence-based explanation; multi-line strings are fine>"
}

Reasoning must be grounded in concrete evidence -- cite file paths, manifest entries, version numbers, function names, or advisory fragments. Be specific; avoid vague claims like "the code looks safe". Newlines inside the reasoning string must be escaped (`\\n`) to keep the object valid JSON.
</OUTPUT_FORMAT>
""".strip()

EXAMPLE_BLOCK = """
<EXAMPLE>
{"category": "code_not_reachable", "reasoning": "GHSA-44wm-f244-xhp3 describes a vulnerability in PIL.ImageMath.eval for Pillow < 10.3.0. pyproject.toml pins Pillow to ^9.5, and poetry.lock records 9.5.0 as the resolved version -- within the affected range, so the vulnerable code is in scope.\\n\\nSearching first-party source (`rg \\"ImageMath\\" src/`) returns no matches. The repository imports PIL.Image only, and the only call sites are `Image.open()` and `Image.thumbnail()` in src/img/loader.py:14-37. Neither reaches ImageMath.eval, so the vulnerable function is unreachable."}
</EXAMPLE>
""".strip()

# ---------------------------------------------------------------------------
# Prompt: DEFAULT (original VEX-Bench)
# ---------------------------------------------------------------------------
PROMPT_DEFAULT = f"""
<ROLE>
You are a security analyst. Decide whether the given CVE actually affects current codebase, and classify the result into one of 12 fixed categories.
</ROLE>

<CONTEXT>
The working directory is a source tree. There is no running container, no running process, and no deployment context: only files you can read and search, plus any public CVE/advisory information you look up.

Treat this as a static analysis task. Do not assume runtime behavior you cannot back with evidence visible in the source tree.
</CONTEXT>

<CVE>
{{cve_id}}
</CVE>

{CLASSIFICATION_BLOCK}

{DECISION_RULES_BLOCK}

{OUTPUT_FORMAT_BLOCK}

{EXAMPLE_BLOCK}
""".strip()

# ---------------------------------------------------------------------------
# Prompt: CONCISE -- shorter preamble, same schema.
# Better for models (Grok, some Gemini configs) that perform worse with
# very long system-level instructions and benefit from directness.
# ---------------------------------------------------------------------------
PROMPT_CONCISE = f"""
You are a security analyst performing static vulnerability exploitability assessment.

Given CVE **{{cve_id}}** and the source tree in the working directory, determine whether this codebase is actually affected. You have access to the filesystem and public CVE/advisory information.

Walk the precedence list below top-to-bottom and pick the FIRST matching category.

{CLASSIFICATION_BLOCK}

{DECISION_RULES_BLOCK}

{OUTPUT_FORMAT_BLOCK}

{EXAMPLE_BLOCK}
""".strip()

# ---------------------------------------------------------------------------
# Prompt: CHAIN_OF_THOUGHT -- explicit step-by-step reasoning scaffold.
# For models with strong CoT capabilities (Claude Opus, GPT-5.5) that
# benefit from structured decomposition of the analysis.
# ---------------------------------------------------------------------------
PROMPT_COT = f"""
<ROLE>
You are a security analyst. Decide whether the given CVE actually affects the current codebase. Work through the analysis step by step before committing to a classification.
</ROLE>

<CONTEXT>
The working directory is a source tree. There is no running container, no running process, and no deployment context: only files you can read and search, plus any public CVE/advisory information you look up.

Treat this as a static analysis task. Do not assume runtime behavior you cannot back with evidence visible in the source tree.
</CONTEXT>

<CVE>
{{cve_id}}
</CVE>

<ANALYSIS_STEPS>
Before classifying, work through these steps in order. Use your tools to gather evidence at each step.

Step 1 - Understand the vulnerability: Look up the CVE to identify the affected package, affected versions, vulnerable function(s), and exploit preconditions.

Step 2 - Check dependency presence: Inspect manifests, lockfiles, and vendored code. Is the vulnerable package (at an affected version) in this project's dependency graph?

Step 3 - Trace reachability: If the package is present, search for imports and call sites of the vulnerable function(s) in first-party (non-test) source. Follow the call chain.

Step 4 - Check mitigations: If the code is reachable, look for configuration gates, environment constraints, or protective controls that prevent exploitation.

Step 5 - Classify: Walk the precedence list below and select the FIRST matching category.
</ANALYSIS_STEPS>

{CLASSIFICATION_BLOCK}

{DECISION_RULES_BLOCK}

{OUTPUT_FORMAT_BLOCK}

{EXAMPLE_BLOCK}
""".strip()


PROMPTS: dict[str, str] = {
    "default": PROMPT_DEFAULT,
    "concise": PROMPT_CONCISE,
    "chain_of_thought": PROMPT_COT,
}

VARIANT_TO_PROMPT: dict[PromptVariant, str] = {
    PromptVariant.DEFAULT: "default",
    PromptVariant.CONCISE: "concise",
    PromptVariant.COT: "chain_of_thought",
}


def get_prompt(variant: PromptVariant | str) -> str:
    if isinstance(variant, PromptVariant):
        key = VARIANT_TO_PROMPT[variant]
    else:
        key = variant
    if key not in PROMPTS:
        raise ValueError(f"Unknown prompt variant: {key!r}. Available: {sorted(PROMPTS)}")
    return PROMPTS[key]
