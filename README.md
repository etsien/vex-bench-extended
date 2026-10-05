# VEX-Bench Extended

Extended benchmarking harness for [VEX-Bench](https://github.com/steven1518/vex-bench) (EMNLP 2026) -- evaluating LLM agents on software supply chain vulnerability exploitability assessment across additional models and harnesses.

## Model-harness matrix

The original VEX-Bench evaluates 9 models across 3 harnesses. This project adds Cursor SDK and Vertex AI harnesses and extends the OpenCode harness to support OpenRouter-hosted models with fp8 provider pinning.

| Harness | Models | Provider |
|---|---|---|
| **Cursor** (SDK) | Grok 3, Claude Opus 4.6, Claude Sonnet 4.6, Gemini 2.5 Pro | Cursor-native |
| **Vertex AI Claude Code** | Claude Opus 4.6 | Corporate Vertex AI |
| **Codex** | GPT-5.5, GPT-5.4 mini | OpenAI API |
| **OpenCode** | DeepSeek-V4-Pro, DeepSeek-V4-Flash, Kimi K2.6, MiniMax M2.7, GLM 5.1 | OpenRouter (fp8 pinned) |

## Quick start

### 1. Clone and install

```bash
git clone <this-repo>
cd vex-bench-extended
uv sync
```

### 2. Download repo snapshots

The benchmark task definitions and repo snapshots are pulled from upstream VEX-Bench:

```bash
bash setup.sh
```

This clones upstream VEX-Bench, copies the task definitions into `benchmark/tasks/`, downloads the repo snapshots, and links them into `benchmark/repos/`. Re-run it to pick up new tasks from upstream.

### 3. Build Docker images

```bash
bash docker/build.sh          # all languages + agents
bash docker/build.sh python   # just python
```

### 4. Configure credentials

Copy each `.example` file under `env/` and fill in real keys:

```bash
# OpenCode (OpenRouter via proxy)
cp env/opencode/opencode.env.example env/opencode/opencode.env
cp env/opencode/opencode.json.example env/opencode/opencode.json
# Edit opencode.env: set OPENROUTER_API_KEY=sk-or-...

# Claude Code
cp env/claude/claude.env.example env/claude/claude.env

# Codex
cp env/codex/codex.env.example env/codex/codex.env

# Cursor SDK
cp env/cursor/cursor.env.example env/cursor/cursor.env

# Vertex AI
cp env/vertex_ai/vertex.env.example env/vertex_ai/vertex.env
```

### 5. Preflight: test every model before a full batch

Before committing to a long batch job, run a single-task test for every
model in the target harness. This catches registration errors, broken
credentials, symlink issues, and Docker image problems up front.

```bash
# Pick any lightweight task for the dry run
TASK=fastapi-fastapi-febf6b6-CVE-2024-47874

# Test all models for a given harness (one task, one repeat each)
for model in $(uv run vex-ext list 2>/dev/null | awk -v h=cursor '
    /^ / && index($0, "harnesses=[") {
        split($0, a, "harnesses=\\["); split(a[2], b, "\\]");
        if (index(b[1], h)) print $1
    }'); do
  echo "--- preflight: $model ---"
  uv run vex-ext run --model "$model" --repeats 1 --tasks "$TASK" --timeout 120
done
```

Replace `cursor` with whichever harness you plan to batch (`codex`,
`opencode`, `vertex_claude_code`). Every model should complete or print a
clear `FAIL`/`SKIP` -- any `CRASH` or stack trace means the harness config
needs fixing before you start the real run.

### 6. Run

```bash
# List all models, harnesses, and matrix combinations
uv run vex-ext list

# Single model, single task (quick sanity check)
uv run vex-ext run \
  --model deepseek-v4-flash --harness opencode \
  --repeats 1 --tasks fastapi-fastapi-febf6b6-CVE-2024-47874

# Run one harness group with parallelism
uv run vex-ext matrix --harnesses opencode --repeats 3 --timeout 600 --parallel 2
uv run vex-ext matrix --harnesses cursor --repeats 3 --timeout 600 --parallel 5
uv run vex-ext matrix --harnesses vertex_claude_code --repeats 3 --timeout 600 --parallel 3
uv run vex-ext matrix --harnesses codex --repeats 3 --timeout 600 --parallel 3

# Run everything (sequential across harnesses)
uv run vex-ext matrix --repeats 3 --timeout 600 --parallel 2
```

Completed runs are cached -- you can interrupt and restart without losing progress. The `--parallel` flag controls concurrent tasks within each harness group. Keep it at 2 for OpenCode (proxy throughput) and go higher for Cursor (no containers).

## OpenCode harness

The OpenCode models run through a local proxy that pins each model to a specific fp8-quantized provider on OpenRouter for reproducibility. The proxy must be running before starting OpenCode runs.

### Architecture

```
Host                                  Docker container (--network host)
+---------------------------+         +-------------------------------+
| openrouter_proxy.py       |<--------| opencode run --model ...      |
| localhost:8788             |         |                               |
| - injects Authorization   |         | config: opencode.json         |
| - pins fp8 providers      |         |   baseURL -> localhost:8788   |
+----------+----------------+         +-------------------------------+
           |
           v
    openrouter.ai/api/v1
```

### Running OpenCode models

The proxy starts automatically when the harness runs. It reads `OPENROUTER_API_KEY` from `env/opencode/opencode.env` (or from your shell environment).

```bash
# Verify the proxy and OpenRouter connection
bash scripts/check-proxy.sh

# Single-task smoke test
bash scripts/test-opencode.sh

# Full batch
uv run vex-ext matrix --harnesses opencode --repeats 3 --timeout 600
```

### Troubleshooting

See [docs/opencode-troubleshooting.md](docs/opencode-troubleshooting.md) for known issues with OpenCode version pinning (v1.15.5 required), podman compatibility (`docker cp` instead of volume mounts), and the API key passthrough bug.

## Usage reference

### Commands

| Command | Description |
|---|---|
| `vex-ext list` | List all models, harnesses, prompt variants, and matrix combinations |
| `vex-ext run` | Run a single model+harness combination |
| `vex-ext parse` | Parse raw results into `parsed.jsonl` |
| `vex-ext metrics` | Compute classification metrics from parsed results |
| `vex-ext matrix` | Run the full model x harness matrix |

### Common flags

| Flag | Default | Description |
|---|---|---|
| `--model` | required | Model name (from `vex-ext list`) |
| `--harness` | model default | Harness to use |
| `--repeats` | 1 | Number of repeated runs per task |
| `--timeout` | 600 | Per-task timeout in seconds |
| `--parallel` | 1 | Concurrent task executions |
| `--tasks` | all | Filter to specific task IDs |
| `--prompt` | model default | Prompt variant override (`default`, `concise`, `chain_of_thought`) |

### Prompt variants

Three prompt variants share the identical classification schema but differ in framing:

- **default** -- Original VEX-Bench prompt (XML-tagged sections, detailed role/context)
- **concise** -- Shorter preamble for models that work better with direct instructions
- **chain_of_thought** -- Explicit step-by-step analysis scaffold

Each model declares a preferred variant in `src/models.py`. Override per-run with `--prompt`.

## Output structure

```
results/<prompt_hash>/<harness>/<model>/
  <task_id>/run_001/
    result.json          raw agent output
    error.txt            present only on failure
    artifacts/           harness-specific debug artifacts
  vex_bench/
    parsed.jsonl         structured predictions + ground truth
    metrics.json         classification metrics
```

## Project structure

```
vex-bench-extended/
  benchmark/
    tasks/                    task definitions (pulled from upstream by setup.sh)
    repos/                    repo snapshots (downloaded by setup.sh)
  docker/
    base.*.Dockerfile         base images per language
    *.opencode.Dockerfile     OpenCode agent images
    *.claude.Dockerfile       Claude Code agent images
    *.codex.Dockerfile        Codex agent images
    build.sh                  image build script
  env/
    opencode/                 OpenCode/OpenRouter config + env
    claude/                   Claude Code credentials
    codex/                    Codex credentials
    cursor/                   Cursor SDK credentials
    vertex_ai/                Vertex AI credentials
  docs/
    opencode-troubleshooting.md   known issues and fixes for OpenCode harness
  scripts/
    openrouter_proxy.py       fp8 provider-pinning proxy for OpenRouter
    test-opencode.sh          end-to-end smoke test
  src/
    cli.py                    CLI entry point (vex-ext)
    models.py                 model registry + matrix definitions
    orchestrator.py           run/parse/metrics pipeline driver
    prompts/                  prompt variants
    harnesses/                execution backends per agent
    evaluate/                 result parsing, pricing, metrics
  matrix.yaml                 default matrix configuration
  setup.sh                    one-time setup (clones upstream, downloads repos)
```

## Adding a new model

1. Add a `ModelSpec` in `src/models.py` with name, provider ID, compatible harnesses, and preferred prompt variant.
2. If pricing is known, add an entry in `src/evaluate/pricing.py`.
3. If the model needs a new harness, implement `BaseHarness` in `src/harnesses/`.
4. Test: `uv run vex-ext run --model your-model --repeats 1 --tasks <any-task-id>`

## Isolation model

Every harness runs benchmark tasks in a clean environment, separated from local development config.

| Harness | Isolation |
|---|---|
| **Cursor** | Local SDK agent with no `setting_sources`. No user rules, skills, MCP servers, or hooks. Source copied to a clean temp dir with `.cursor/` removed. |
| **Vertex AI Claude Code** | Disposable Docker container per task. Source and GCP credentials injected via `docker cp`, container destroyed after. |
| **Codex** | Disposable Docker container. |
| **OpenCode** | Disposable Docker container. Config injected via `docker cp`. |

VEX-Bench removes `.git` history from repo snapshots to prevent agents from finding source fix PRs and leaking benchmark answers (paper Section 3.4). Cloud agents that clone from GitHub are not used for this reason.

## Relation to upstream VEX-Bench

This project uses the same benchmark task format, result parsing, and metrics as upstream [vex-bench](https://github.com/steven1518/vex-bench) so results are directly comparable. Key additions:

- **Harness abstraction** decouples agent execution from model selection
- **Model matrix** automates all valid (model, harness) combinations
- **Prompt variants** adapt instruction style per model family
- **Vertex AI support** enables corporate-hosted model benchmarking
- **Cursor SDK support** enables benchmarking Cursor-native models
- **OpenRouter proxy** enables reproducible fp8-quantized open-weight model benchmarking
