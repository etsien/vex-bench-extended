#!/usr/bin/env bash
set -euo pipefail

# Setup script for vex-bench-extended.
# Clones upstream vex-bench, copies task data, and downloads repo snapshots.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UPSTREAM_DIR="${SCRIPT_DIR}/.upstream-vex-bench"
TASKS_DIR="${SCRIPT_DIR}/benchmark/tasks"
REPOS_DIR="${SCRIPT_DIR}/benchmark/repos"

echo "=== Step 1: Clone upstream vex-bench ==="
if [ -d "$UPSTREAM_DIR" ]; then
    echo "  already cloned at $UPSTREAM_DIR"
    cd "$UPSTREAM_DIR" && git pull --ff-only 2>/dev/null || true
else
    git clone https://github.com/steven1518/vex-bench.git "$UPSTREAM_DIR"
fi

echo ""
echo "=== Step 2: Copy benchmark task file ==="
mkdir -p "$TASKS_DIR"
cp "$UPSTREAM_DIR/benchmark/tasks/vex_bench.jsonl" "$TASKS_DIR/vex_bench.jsonl"
TASK_COUNT=$(wc -l < "$TASKS_DIR/vex_bench.jsonl")
echo "  copied vex_bench.jsonl ($TASK_COUNT tasks)"

echo ""
echo "=== Step 3: Download repo snapshots ==="
echo "  This downloads ~75 repository snapshots at pinned commits."
echo "  It may take several minutes depending on your connection."
cd "$UPSTREAM_DIR"

# Install upstream deps and run the download command
if command -v uv &>/dev/null; then
    uv sync
    uv run vex-bench download
else
    echo "  ERROR: uv not found. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

echo ""
echo "=== Step 4: Link repo snapshots ==="
if [ -L "$REPOS_DIR" ]; then
    echo "  symlink already exists: $REPOS_DIR -> $(readlink "$REPOS_DIR")"
elif [ -d "$REPOS_DIR" ] && [ "$(ls -A "$REPOS_DIR" 2>/dev/null)" ]; then
    echo "  repos dir already populated at $REPOS_DIR"
else
    rmdir "$REPOS_DIR" 2>/dev/null || true
    ln -s "$UPSTREAM_DIR/benchmark/repos" "$REPOS_DIR"
    echo "  linked $REPOS_DIR -> $UPSTREAM_DIR/benchmark/repos"
fi

echo ""
echo "=== Step 5: Install vex-bench-extended ==="
cd "$SCRIPT_DIR"
uv sync
echo "  done"

echo ""
echo "=== Setup complete ==="
echo ""
echo "  Tasks:  $TASKS_DIR/vex_bench.jsonl ($TASK_COUNT tasks)"
echo "  Repos:  $REPOS_DIR"
echo ""
echo "Next steps:"
echo "  1. Configure credentials in env/ (see env/*/*.example)"
echo "  2. Build Docker images:  ./docker/build.sh"
echo "  3. Run:  uv run vex-ext list"
echo "  4. Test: uv run vex-ext run --model <model> --repeats 1"
