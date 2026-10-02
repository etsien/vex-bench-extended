# VEX-Bench Extended

Extended benchmarking harness for [VEX-Bench](https://github.com/steven1518/vex-bench) (EMNLP 2026) -- evaluating LLM agents on software supply chain vulnerability exploitability assessment across more models and harnesses.

## What's new

The original VEX-Bench evaluates 9 models across 3 harnesses (Claude Code, Codex, OpenCode). This project extends the benchmark to cover:

| Harness | Models | Notes |
|---|---|---|
| **Cursor** (SDK/CLI) | Grok 3, Claude Opus 4.6, Claude Sonnet 4.6, Gemini 2.5 Pro | Cursor-native models |
| **Vertex AI Claude Code** | Claude Opus 4.6, Gemini 2.5 Pro, GPT Luna | Corporate Vertex AI endpoints |
| Claude Code (original) | Claude Opus 4.6, Claude Sonnet 4.6 | Baseline from paper |
| Codex (original) | GPT-5.5, GPT-5.4 mini | Baseline from paper |
| OpenCode (original) | Kimi K2.6, DeepSeek-V4-Pro/Flash | Baseline from paper |

### Prompt adaptations

Different model families respond better to different instruction styles. Three prompt variants share the identical classification schema but differ in framing:

- **default** -- Original VEX-Bench prompt (XML-tagged sections, detailed role/context)
- **concise** -- Shorter preamble for models that work better with direct instructions (Grok, some Gemini configs)
- **chain_of_thought** -- Explicit step-by-step analysis scaffold for models with strong CoT (Claude Opus, GPT-5.5)

Each model's `ModelSpec` declares its preferred variant, but you can override per-run with `--prompt`.

## Setup

### 1. Install

```bash
uv sync
uv run vex-ext list   # see all models, harnesses, combinations
```

### 2. Download benchmark repos

Copy (or symlink) the benchmark tasks and downloaded repos from the upstream VEX-Bench checkout:

```bash
cp /path/to/vex-bench/benchmark/tasks/vex_bench.jsonl benchmark/tasks/
# Either copy or symlink the repos directory:
ln -s /path/to/vex-bench/benchmark/repos benchmark/repos
```

### 3. Build Docker images

```bash
chmod +x docker/build.sh
./docker/build.sh           # all languages + agents
./docker/build.sh python    # just python
```

### 4. Configure credentials

Copy the `.example` files under `env/` and fill in your API keys:

```bash
cp env/claude/claude.env.example env/claude/claude.env
cp env/vertex_ai/vertex.env.example env/vertex_ai/vertex.env
cp env/cursor/cursor.env.example env/cursor/cursor.env
# ... edit each with real credentials
```

## Usage

### Single model run

```bash
# Run Grok via Cursor harness
uv run vex-ext run --model grok-3 --harness cursor --repeats 1

# Parse + metrics
uv run vex-ext parse --model grok-3 --harness cursor --repeats 1
uv run vex-ext metrics --model grok-3 --harness cursor --repeats 1
```

### Full matrix

```bash
# Run all configured combinations (respects matrix.yaml)
uv run vex-ext matrix --repeats 3 --parallel 2

# Run only Cursor-harness models
uv run vex-ext matrix --harnesses cursor --repeats 3

# Run only specific models across all their harnesses
uv run vex-ext matrix --models grok-3 gemini-2.5-pro --repeats 1
```

### List available options

```bash
uv run vex-ext list
```

## Output structure

```
results/<prompt_hash>/<harness>/<model>/
├── <task_id>/run_001/
│   ├── result.json or result.jsonl
│   ├── error.txt
│   └── artifacts/
└── vex_bench/
    ├── parsed.jsonl
    └── metrics.json
```

## Architecture

```
vex-bench-extended/
├── benchmark/tasks/        VEX-Bench task definitions (from upstream)
├── docker/                 Base + agent Docker images
├── env/                    Credential templates per harness
├── matrix.yaml             Default matrix configuration
└── src/
    ├── cli.py              CLI entry point
    ├── models.py           Model registry + matrix definitions
    ├── orchestrator.py     Cross-harness run/parse/metrics driver
    ├── prompts/            Prompt variants (default, concise, CoT)
    ├── harnesses/          Execution backends
    │   ├── base.py         ABC + Docker helpers
    │   ├── claude_code.py  Claude Code CLI
    │   ├── codex.py        Codex CLI
    │   ├── opencode.py     OpenCode CLI
    │   ├── cursor_harness.py   Cursor SDK / CLI
    │   └── vertex_claude_code.py   Vertex AI Claude Code
    ├── evaluate/
    │   ├── result_parser.py    Category extraction (shared with upstream)
    │   ├── pricing.py          Per-model token pricing
    │   └── metrics.py          Classification metrics
    └── utils/
```

## Adding a new model

1. Add a `ModelSpec` entry in `src/models.py` with the model name, provider ID, compatible harnesses, and preferred prompt variant.
2. If pricing is known, add an entry in `src/evaluate/pricing.py`.
3. If the model needs a new harness, implement `BaseHarness` in `src/harnesses/`.
4. Run: `uv run vex-ext run --model your-model --repeats 1`

## Isolation model

Every harness runs benchmark tasks in a clean environment, completely separated from your local development setup. None of your personal Cursor skills, rules, MCP servers, hooks, `~/.claude/` config, or `CLAUDE.md` files are loaded.

| Harness | Isolation mechanism |
|---|---|
| **Cursor** | Local SDK agent with no `setting_sources` (Python default = inline config only). No user rules, skills, MCP servers, or hooks loaded. Source copied to a clean temp dir with any `.cursor/` removed. |
| **Claude Code** | Disposable Docker container per task. Claude Code CLI installed in the image, not invoked from host. Source copied in via `docker cp`, container destroyed after. |
| **Vertex AI Claude Code** | Same Docker isolation as Claude Code, with Vertex AI credentials mounted read-only. |
| **Codex** | Disposable Docker container (same model as Claude Code). |
| **OpenCode** | Disposable Docker container (same model as Claude Code). |

Why not Cursor cloud agents? VEX-Bench removes `.git` history from repo snapshots to prevent agents from finding the source fix PRs and leaking benchmark answers (paper Section 3.4). Cloud agents clone from GitHub and would have full git history, violating this design. Local SDK agents with the pre-prepared snapshots preserve the benchmark's integrity.

## Relation to upstream VEX-Bench

This project is designed to be used alongside the upstream [vex-bench](https://github.com/steven1518/vex-bench) repo. It shares the same benchmark task format, result parsing, and metrics computation so that results are directly comparable. The key additions are:

- **Harness abstraction**: Decouples agent execution from model selection, enabling cross-harness comparison.
- **Model matrix**: Automates running all valid (model, harness) combinations.
- **Prompt variants**: Adapts instruction style per model family while keeping the classification schema identical.
- **Vertex AI support**: Enables benchmarking corporate-hosted models through Claude Code pointed at Vertex endpoints.
- **Cursor SDK support**: Enables benchmarking models available natively through Cursor via isolated cloud agents.
