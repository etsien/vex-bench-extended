#!/usr/bin/env bash
# End-to-end test of the OpenCode harness with the fp8 proxy.
# Requires OPENROUTER_API_KEY in the environment.
set -euo pipefail

cd "$(dirname "$0")/.."
PROJ="$(pwd)"
IMAGE="vex-bench-python-opencode:latest"

if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  echo "OPENROUTER_API_KEY not set. Export it and re-run."
  exit 1
fi

echo "=== Rebuilding $IMAGE ==="
docker build -t "$IMAGE" -f docker/python.opencode.Dockerfile .

echo ""
echo "=== Starting proxy on :8788 ==="
kill "$(lsof -ti:8788)" 2>/dev/null || true
sleep 0.3
python scripts/openrouter_proxy.py &
PROXY_PID=$!
sleep 1
echo "proxy pid=$PROXY_PID"

cleanup() { kill "$PROXY_PID" 2>/dev/null || true; }
trap cleanup EXIT

echo ""
echo "=== Creating container ==="
CID=$(docker create --workdir /work --network host \
  --env-file "$PROJ/env/opencode/opencode.env" \
  --entrypoint sh "$IMAGE" -lc '
cd /work
git init -q
git config user.email "bench@vex-bench.local"
git config user.name "VEX-Bench"
echo "test" > README.md && git add -A && git commit -q -m "init"
mkdir -p /root/.local/share/opencode/log /root/.cache/opencode
timeout 60s opencode run --print-logs \
  --model openrouter-proxy/deepseek/deepseek-v4-flash \
  --thinking --dangerously-skip-permissions "say hello in one word" \
  > /tmp/opencode-stdout.txt 2> /tmp/opencode-stderr.log
RC=$?
if [ $RC -ne 0 ]; then
  echo "=== opencode exited $RC ===" >&2
  cat /tmp/opencode-stderr.log >&2
  cat /root/.local/share/opencode/log/*.log 2>/dev/null >&2 || true
  tail -30 /tmp/opencode-stdout.txt >&2
  exit $RC
fi
echo "=== success ==="
cat /tmp/opencode-stdout.txt
')

echo "container=$CID"
echo "Copying config into container..."
docker cp "$PROJ/env/opencode/opencode.json" "$CID:/root/.config/opencode/opencode.json"

echo ""
echo "=== Starting container ==="
docker start -a "$CID" 2>&1 || true
docker rm -f "$CID" >/dev/null 2>&1

echo ""
echo "=== Done ==="
