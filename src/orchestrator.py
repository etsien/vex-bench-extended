"""Cross-harness, cross-model benchmark orchestrator.

Resolves the full (model, harness, prompt) matrix from a YAML config
or CLI flags, then drives the run -> parse -> metrics pipeline for
each combination. Supports:
  - Parallel execution across combinations
  - Resume from cached results
  - Selective re-runs by model/harness/task filter
  - Consolidated cross-model comparison reports
"""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from harnesses import HARNESS_REGISTRY, BaseHarness, RunContext, RunStats
from evaluate.pricing import cost_usd
from evaluate.result_parser import normalize_ground_truth, parse_category, to_binary
from models import Harness, ModelSpec, PromptVariant, resolve_matrix
from prompts import get_prompt

logger = logging.getLogger(__name__)

PROMPT_HASH_LEN = 12
BENCHMARKS: dict[str, Path] = {
    "vex_bench": Path("benchmark/tasks/vex_bench.jsonl"),
}


def _prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:PROMPT_HASH_LEN]


@dataclass
class RunConfig:
    """Full specification for a benchmark experiment."""

    models: list[str] | None = None
    harnesses: list[str] | None = None
    benchmark: str = "vex_bench"
    prompt_override: str | None = None
    repeats: int = 1
    timeout: int = 600
    parallel: int = 1
    repos_dir: Path = Path("benchmark/repos")
    output_dir: Path = Path("results")
    tasks_filter: list[str] | None = None


def load_tasks(benchmark: str) -> list[dict]:
    path = BENCHMARKS.get(benchmark)
    if path is None:
        raise ValueError(f"Unknown benchmark: {benchmark!r}")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def make_harness(spec: ModelSpec, harness: Harness) -> BaseHarness:
    factory = HARNESS_REGISTRY.get(harness)
    if factory is None:
        raise ValueError(f"No harness implementation for {harness.value}")
    return factory(model=spec.provider_id, extra=spec.extra)


def _run_one_task(
    task: dict,
    rep: int,
    *,
    harness_inst: BaseHarness,
    prompt_text: str,
    run_dir: Path,
    repos_dir: Path,
    timeout: int,
) -> None:
    task_id = task["task_id"]
    run_id = f"run_{rep:03d}"
    tag = f"[{task_id}/{run_id}]"

    language = task.get("metadata", {}).get("language")
    if not language:
        logger.warning("%s SKIP: missing language", tag)
        return

    repo_url = task["repo_url"]
    owner_repo = repo_url.rstrip("/").split("github.com/")[-1]
    owner, repo = owner_repo.split("/", 1)
    src_cwd = repos_dir / owner / repo / task["commit_sha"]
    if not src_cwd.exists():
        logger.warning("%s SKIP: source dir not found: %s", tag, src_cwd)
        return

    run_dir_per_rep = run_dir / task_id / run_id
    error_path = run_dir_per_rep / "error.txt"

    cached = harness_inst.load_result(run_dir_per_rep)
    if cached is not None:
        error_path.unlink(missing_ok=True)
        logger.info("%s CACHE: %s", tag, cached.path)
        return
    if harness_inst.has_result(run_dir_per_rep):
        logger.info("%s RE-RUN: cached result unparseable", tag)

    run_dir_per_rep.mkdir(parents=True, exist_ok=True)
    prompt = prompt_text.replace("{cve_id}", task["cve_id"])
    logger.info("%s RUN: src=%s lang=%s", tag, src_cwd, language)

    ctx = RunContext(
        cwd=src_cwd,
        language=language,
        timeout=timeout,
        artifacts_dir=run_dir_per_rep / "artifacts",
    )
    try:
        result = harness_inst.run(prompt, ctx)
    except RuntimeError as exc:
        error_path.write_text(f"{exc}\n", encoding="utf-8")
        logger.warning("%s ERROR: %s", tag, exc)
        return

    error_path.unlink(missing_ok=True)
    result_path = run_dir_per_rep / harness_inst.result_filename()
    result_path.write_text(result.raw_output, encoding="utf-8")
    harness_inst.save_report(run_dir_per_rep)


def run_combination(
    spec: ModelSpec,
    harness: Harness,
    config: RunConfig,
) -> dict:
    """Run all tasks for one (model, harness) combination."""
    prompt_key = config.prompt_override or spec.prompt_variant.value
    prompt_text = get_prompt(prompt_key)
    p_hash = _prompt_hash(prompt_text)

    run_dir = config.output_dir / p_hash / harness.value / spec.name
    run_dir.mkdir(parents=True, exist_ok=True)

    prompt_file = config.output_dir / p_hash / "prompt.txt"
    if not prompt_file.exists():
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(prompt_text, encoding="utf-8")

    harness_inst = make_harness(spec, harness)
    tasks = load_tasks(config.benchmark)

    if config.tasks_filter:
        tasks = [t for t in tasks if t["task_id"] in config.tasks_filter]

    logger.info(
        "=== %s / %s === tasks=%d repeats=%d prompt=%s",
        spec.name, harness.value, len(tasks), config.repeats, prompt_key,
    )

    jobs = [(task, rep) for task in tasks for rep in range(1, config.repeats + 1)]

    with ThreadPoolExecutor(max_workers=config.parallel) as ex:
        futs = {
            ex.submit(
                _run_one_task, t, r,
                harness_inst=harness_inst,
                prompt_text=prompt_text,
                run_dir=run_dir,
                repos_dir=config.repos_dir,
                timeout=config.timeout,
            ): (t["task_id"], r)
            for t, r in jobs
        }
        for i, fut in enumerate(as_completed(futs), 1):
            tid, rep = futs[fut]
            try:
                fut.result()
            except Exception:
                logger.exception("[%s/run_%03d] unexpected failure", tid, rep)
            if i % 10 == 0:
                logger.info("progress %d/%d", i, len(jobs))

    return {
        "model": spec.name,
        "harness": harness.value,
        "prompt": prompt_key,
        "prompt_hash": p_hash,
        "tasks": len(tasks),
        "repeats": config.repeats,
    }


def parse_combination(
    spec: ModelSpec,
    harness: Harness,
    config: RunConfig,
) -> Path:
    """Parse raw results into parsed.jsonl for one combination."""
    prompt_key = config.prompt_override or spec.prompt_variant.value
    prompt_text = get_prompt(prompt_key)
    p_hash = _prompt_hash(prompt_text)

    run_dir = config.output_dir / p_hash / harness.value / spec.name
    benchmark_dir = run_dir / config.benchmark
    benchmark_dir.mkdir(parents=True, exist_ok=True)

    harness_inst = make_harness(spec, harness)
    tasks = load_tasks(config.benchmark)

    if config.tasks_filter:
        tasks = [t for t in tasks if t["task_id"] in config.tasks_filter]

    rows: list[dict] = []
    for task in tasks:
        for rep in range(1, config.repeats + 1):
            rows.append(_parse_run(harness_inst, task, run_dir, rep, spec.name))

    parsed_path = benchmark_dir / "parsed.jsonl"
    with parsed_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    logger.info("wrote %d rows to %s", len(rows), parsed_path)
    return parsed_path


def _parse_run(
    harness_inst: BaseHarness,
    task: dict,
    run_dir: Path,
    rep: int,
    model_name: str,
) -> dict:
    task_id = task["task_id"]
    run_id = f"run_{rep:03d}"
    gt_raw = task["ground_truth"]

    row = {
        "task_id": task_id,
        "run_id": run_id,
        "cve_id": task["cve_id"],
        "ground_truth": normalize_ground_truth(gt_raw) if gt_raw else None,
        "ground_truth_category": task.get("ground_truth_category"),
        "predicted": None,
        "predicted_category": None,
        "reasoning": None,
        "status": "ok",
        **RunStats().model_dump(),
        "cost_usd": None,
    }

    run_dir_per_rep = run_dir / task_id / run_id
    parsed = harness_inst.load_result(run_dir_per_rep)
    if parsed is None:
        status = "no_dir" if not (run_dir / task_id).exists() else "no_result"
        if harness_inst.has_result(run_dir_per_rep):
            status = "parse_fail"
        return {**row, "status": status}

    row.update(parsed.stats.model_dump())
    row["cost_usd"] = cost_usd(parsed.stats, model_name)

    return {
        **row,
        "predicted": to_binary(parsed.category),
        "predicted_category": parsed.category,
        "reasoning": parsed.reasoning,
    }


def orchestrate(config: RunConfig) -> list[dict]:
    """Run the full matrix: resolve combinations, execute, parse, return summaries."""
    matrix = resolve_matrix(
        models=config.models,
        harnesses=config.harnesses,
    )
    if not matrix:
        logger.error("no valid (model, harness) combinations found")
        return []

    logger.info("resolved %d (model, harness) combinations", len(matrix))
    for spec, harness in matrix:
        logger.info("  %s x %s (prompt: %s)", spec.name, harness.value, spec.prompt_variant.value)

    summaries: list[dict] = []
    for spec, harness in matrix:
        summary = run_combination(spec, harness, config)
        summaries.append(summary)

    for spec, harness in matrix:
        parse_combination(spec, harness, config)

    return summaries
