# OpenCode Docker Harness -- Troubleshooting Notes

Reference for issues encountered while adapting the OpenCode harness from Azure (upstream VEX-Bench) to OpenRouter with fp8 provider pinning.

## Issues and fixes

### 1. "Unexpected server error" on startup

**Symptom:** `opencode run` exits with `UnknownError: Unexpected server error` and an empty server log.

**Causes found:**
- Missing git repo. OpenCode requires `.git` in the working directory. VEX-Bench strips it from repo snapshots. Fix: `git init` in the container startup script.
- Podman volume mount permissions. Podman's rootless UID remapping makes `-v` mounted files unreadable. Fix: use `docker cp` after `docker create` instead of `-v`.

### 2. Custom providers don't register (v1.18.x)

**Symptom:** `ProviderModelNotFoundError: Model not found: openrouter-proxy/...`

**Cause:** [Bug #51285](https://github.com/anomalyco/opencode/issues/51285) -- providers declared in config with `npm` silently fail to materialize on v1.18.x and v2.0.x.

**Fix:** Pin OpenCode to v1.15.5 (`npm install -g opencode-ai@1.15.5`), the version validated in the original paper where custom providers work.

### 3. API key dropped from HTTP requests

**Symptom:** `AI_APICallError: Missing Authentication header` even though `opencode auth list` shows the key.

**Cause:** [Bug #5674](https://github.com/sst/opencode/issues/5674) -- `options.apiKey` in config is parsed but never injected into outgoing headers. Affects `@ai-sdk/openai-compatible` custom providers and built-in providers' env var auth across multiple versions.

**Workaround:** The OpenRouter proxy (`scripts/openrouter_proxy.py`) injects `Authorization: Bearer <real-key>` on every forwarded request regardless of what OpenCode sends. The container's `OPENROUTER_API_KEY=proxy` is a placeholder; the proxy replaces it with the real key from the host environment.

## What upstream does differently

The original VEX-Bench uses Azure-hosted models where the built-in Azure provider properly passes `{env:AZURE_RESOURCE_API_KEY}` through to HTTP headers. The auth bug in #5674 does not affect the Azure built-in provider, only `@ai-sdk/openai-compatible` custom providers and the OpenRouter built-in provider.

Upstream Dockerfiles: `npm install -g opencode-ai@1.15.5` with no additional packages. Config uses the same `npm` + `options.baseURL` + `options.apiKey` pattern we use for the OpenRouter proxy.

Key difference: upstream uses real Docker (not podman) and volume mounts with `:ro`. Podman requires `docker cp` instead.
