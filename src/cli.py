"""CLI entry point for vex-bench-extended.

Provides three command groups:
  vex-ext run       -- run individual or matrix experiments
  vex-ext parse     -- parse raw results to parsed.jsonl
  vex-ext metrics   -- compute classification metrics
  vex-ext matrix    -- run the full model x harness matrix
  vex-ext list      -- list available models, harnesses, prompts
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from models import ALL_MODELS, Harness, resolve_matrix
from prompts import PROMPTS


def main() -> None:
    parser = argparse.ArgumentParser(prog="vex-ext")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # --- list ---
    list_p = sub.add_parser("list", help="List available models, harnesses, and prompts")
    list_p.set_defaults(func=_cmd_list)

    # --- run ---
    run_p = sub.add_parser("run", help="Run a single model+harness experiment")
    _add_common_args(run_p)
    run_p.set_defaults(func=_cmd_run)

    # --- parse ---
    parse_p = sub.add_parser("parse", help="Parse raw results into parsed.jsonl")
    _add_common_args(parse_p)
    parse_p.set_defaults(func=_cmd_parse)

    # --- metrics ---
    metrics_p = sub.add_parser("metrics", help="Compute metrics from parsed.jsonl")
    _add_common_args(metrics_p)
    metrics_p.set_defaults(func=_cmd_metrics)

    # --- matrix ---
    matrix_p = sub.add_parser("matrix", help="Run the full model x harness matrix")
    _add_common_args(matrix_p)
    matrix_p.add_argument(
        "--models", nargs="+", default=None,
        help="Filter to specific model names",
    )
    matrix_p.add_argument(
        "--harnesses", nargs="+", default=None,
        help="Filter to specific harness names",
    )
    matrix_p.set_defaults(func=_cmd_matrix)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    args.func(args)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default=None, help="Model name")
    parser.add_argument(
        "--harness", default=None,
        choices=[h.value for h in Harness],
        help="Harness to use",
    )
    parser.add_argument("--benchmark", default="vex_bench", help="Benchmark name")
    parser.add_argument(
        "--prompt", default=None,
        choices=sorted(PROMPTS),
        help="Prompt variant override",
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--repos-dir", default="benchmark/repos")
    parser.add_argument("--output-dir", default="results")
    parser.add_argument("--tasks", nargs="+", default=None, help="Filter to specific task IDs")


def _cmd_list(args: argparse.Namespace) -> None:
    print("\n=== Models ===")
    for spec in ALL_MODELS:
        harness_names = ", ".join(h.value for h in spec.harnesses)
        print(f"  {spec.name:<30} harnesses=[{harness_names}]  prompt={spec.prompt_variant.value}")

    print("\n=== Harnesses ===")
    for h in Harness:
        print(f"  {h.value}")

    print("\n=== Prompts ===")
    for name in sorted(PROMPTS):
        print(f"  {name}")

    print(f"\n=== Matrix ({len(resolve_matrix())} combinations) ===")
    for spec, h in resolve_matrix():
        print(f"  {spec.name} x {h.value}")


def _cmd_run(args: argparse.Namespace) -> None:
    from orchestrator import RunConfig, run_combination
    from models import MODEL_REGISTRY

    if not args.model:
        raise SystemExit("--model is required for 'run'")

    spec = MODEL_REGISTRY.get(args.model)
    if spec is None:
        raise SystemExit(f"Unknown model: {args.model}")

    harness_val = args.harness or spec.harnesses[0].value
    harness = Harness(harness_val)

    config = RunConfig(
        benchmark=args.benchmark,
        prompt_override=args.prompt,
        repeats=args.repeats,
        timeout=args.timeout,
        parallel=args.parallel,
        repos_dir=Path(args.repos_dir),
        output_dir=Path(args.output_dir),
        tasks_filter=args.tasks,
    )
    summary = run_combination(spec, harness, config)
    print(json.dumps(summary, indent=2))


def _cmd_parse(args: argparse.Namespace) -> None:
    from orchestrator import RunConfig, parse_combination
    from models import MODEL_REGISTRY

    if not args.model:
        raise SystemExit("--model is required for 'parse'")

    spec = MODEL_REGISTRY.get(args.model)
    if spec is None:
        raise SystemExit(f"Unknown model: {args.model}")

    harness_val = args.harness or spec.harnesses[0].value
    harness = Harness(harness_val)

    config = RunConfig(
        benchmark=args.benchmark,
        prompt_override=args.prompt,
        repeats=args.repeats,
        repos_dir=Path(args.repos_dir),
        output_dir=Path(args.output_dir),
        tasks_filter=args.tasks,
    )
    path = parse_combination(spec, harness, config)
    print(f"wrote {path}")


def _cmd_metrics(args: argparse.Namespace) -> None:
    from orchestrator import RunConfig
    from evaluate.metrics import compute_and_write
    from models import MODEL_REGISTRY
    from prompts import get_prompt
    import hashlib

    if not args.model:
        raise SystemExit("--model is required for 'metrics'")

    spec = MODEL_REGISTRY.get(args.model)
    if spec is None:
        raise SystemExit(f"Unknown model: {args.model}")

    harness_val = args.harness or spec.harnesses[0].value
    harness = Harness(harness_val)

    prompt_key = args.prompt or spec.prompt_variant.value
    prompt_text = get_prompt(prompt_key)
    p_hash = hashlib.sha256(prompt_text.encode()).hexdigest()[:12]

    output_dir = Path(args.output_dir)
    run_dir = output_dir / p_hash / harness / spec.name
    benchmark_dir = run_dir / args.benchmark

    parsed_path = benchmark_dir / "parsed.jsonl"
    metrics_path = benchmark_dir / "metrics.json"

    out = compute_and_write(
        parsed_path, metrics_path,
        config_meta={"config": {
            "model": spec.name, "harness": harness,
            "prompt_hash": p_hash, "repeats": args.repeats,
        }},
    )
    _print_summary(out)


def _cmd_matrix(args: argparse.Namespace) -> None:
    from orchestrator import RunConfig, orchestrate

    config = RunConfig(
        models=args.models,
        harnesses=args.harnesses,
        benchmark=args.benchmark,
        prompt_override=args.prompt,
        repeats=args.repeats,
        timeout=args.timeout,
        parallel=args.parallel,
        repos_dir=Path(args.repos_dir),
        output_dir=Path(args.output_dir),
        tasks_filter=args.tasks,
    )
    summaries = orchestrate(config)
    print(json.dumps(summaries, indent=2))


def _print_summary(out: dict) -> None:
    binary = out.get("binary", {})
    multi = out.get("multiclass", {})
    print("\n=== Binary ===")
    for k in ("accuracy", "precision", "recall", "f1"):
        v = binary.get(k, {})
        m, s = v.get("mean"), v.get("std")
        if m is not None:
            print(f"  {k:<12} {m:.3f} +/- {s:.3f}")
    print("\n=== Multiclass ===")
    for k in ("accuracy", "macro_f1", "weighted_f1"):
        v = multi.get(k, {})
        m, s = v.get("mean"), v.get("std")
        if m is not None:
            print(f"  {k:<20} {m:.3f} +/- {s:.3f}")
