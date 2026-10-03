#!/usr/bin/env bash
# Verify the proxy can reach OpenRouter and serve tokens.
# Reads OPENROUTER_API_KEY from env or from env/opencode/opencode.env.
set -euo pipefail

cd "$(dirname "$0")/.."

# Load key from env file if not already exported
if [ -z "${OPENROUTER_API_KEY:-}" ] && [ -f env/opencode/opencode.env ]; then
  eval "$(grep '^OPENROUTER_API_KEY=' env/opencode/opencode.env)"
  export OPENROUTER_API_KEY
fi

if [ -z "${OPENROUTER_API_KEY:-}" ] || [ "$OPENROUTER_API_KEY" = "proxy" ] || [ "$OPENROUTER_API_KEY" = "sk-or-..." ]; then
  echo "Put your real OpenRouter API key in env/opencode/opencode.env" >&2
  exit 1
fi

# Start proxy
kill "$(lsof -ti:8788)" 2>/dev/null || true
python scripts/openrouter_proxy.py &
PID=$!
trap "kill $PID 2>/dev/null" EXIT
sleep 1

echo "=== Proxy -> OpenRouter (non-streaming) ==="
RESP=$(curl -s -w "\n%{http_code}" http://localhost:8788/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer proxy" \
  -d '{
    "model": "deepseek/deepseek-v4-flash",
    "messages": [{"role": "user", "content": "Reply with exactly one word: OK"}],
    "max_tokens": 10,
    "stream": false
  }')

CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | sed '$d')

echo "HTTP $CODE"
if [ "$CODE" = "200" ]; then
  echo "$BODY" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f\"model:  {d.get('model','?')}\")
c = d['choices'][0]['message']['content']
print(f\"reply:  {c}\")
u = d.get('usage', {})
print(f\"tokens: prompt={u.get('prompt_tokens','?')} completion={u.get('completion_tokens','?')} total={u.get('total_tokens','?')}\")
"
  echo ""
  echo "Connection OK."
else
  echo "$BODY" | python3 -m json.tool 2>/dev/null || echo "$BODY"
  echo ""
  echo "FAILED (HTTP $CODE)."
  exit 1
fi
