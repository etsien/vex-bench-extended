#!/usr/bin/env python3
"""Lightweight proxy that injects OpenRouter provider routing into requests.

Ensures deterministic fp8 provider pinning for benchmark reproducibility.
Runs on the host; Docker containers reach it via --network host.

Usage:
    OPENROUTER_API_KEY=sk-or-... python scripts/openrouter_proxy.py
    # or: OPENROUTER_API_KEY=sk-or-... python scripts/openrouter_proxy.py --port 8788
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import os
import ssl
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s proxy: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

UPSTREAM = "openrouter.ai"
UPSTREAM_PREFIX = "/api/v1"

PROVIDER_ROUTING: dict[str, dict] = {
    "deepseek/deepseek-v4-pro": {
        "only": ["streamlake/fp8"],
        "allow_fallbacks": False,
    },
    "deepseek/deepseek-v4-flash": {
        "order": ["streamlake/fp8", "deepinfra"],
        "allow_fallbacks": True,
    },
    "moonshotai/kimi-k2.6": {
        "only": ["streamlake/fp8"],
        "allow_fallbacks": False,
    },
    "minimax/minimax-m2.7": {
        "only": ["gmicloud/fp8"],
        "allow_fallbacks": False,
    },
    "z-ai/glm-5.1": {
        "only": ["streamlake/fp8"],
        "allow_fallbacks": False,
    },
}

_request_count = 0
_error_count = 0
_billing_errors = 0
_lock = threading.Lock()


def _extract_error(resp_bytes: bytes) -> str:
    """Pull a human-readable message from an error response body."""
    try:
        text = resp_bytes.decode("utf-8", errors="replace")
        # Non-streaming JSON error
        data = json.loads(text)
        err = data.get("error", {})
        if isinstance(err, dict):
            return err.get("message", "") or err.get("code", "") or str(err)
        return str(err)
    except (json.JSONDecodeError, ValueError):
        snippet = resp_bytes[:300].decode("utf-8", errors="replace")
        return snippet


def get_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        env_file = os.path.join(
            os.path.dirname(__file__), "..", "env", "opencode", "opencode.env"
        )
        if os.path.exists(env_file):
            for line in open(env_file):
                line = line.strip()
                if line.startswith("OPENROUTER_API_KEY=") and not line.startswith("#"):
                    key = line.split("=", 1)[1].strip().strip("'\"")
                    break
    if not key:
        sys.exit("OPENROUTER_API_KEY not set (env var or env/opencode/opencode.env)")
    return key


class ProxyHandler(BaseHTTPRequestHandler):
    api_key: str = ""

    def do_POST(self):
        global _request_count, _error_count
        t0 = time.monotonic()
        content_len = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_len) if content_len else b""

        body = None
        model = None
        stream = False
        if raw_body:
            try:
                body = json.loads(raw_body)
                model = body.get("model")
                stream = body.get("stream", False)
            except json.JSONDecodeError:
                pass

        pinned = False
        if body and model and model in PROVIDER_ROUTING:
            if "provider" not in body:
                body["provider"] = PROVIDER_ROUTING[model]
                pinned = True
                raw_body = json.dumps(body).encode()

        upstream_path = UPSTREAM_PREFIX + self.path
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        for name in ("Accept", "HTTP-Referer", "X-Title"):
            val = self.headers.get(name)
            if val:
                headers[name] = val

        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(UPSTREAM, timeout=60, context=ctx)
        try:
            conn.request("POST", upstream_path, body=raw_body, headers=headers)
            resp = conn.getresponse()

            self.send_response(resp.status)
            for key, val in resp.getheaders():
                if key.lower() not in ("transfer-encoding", "connection"):
                    self.send_header(key, val)
            self.send_header("Connection", "close")
            self.end_headers()

            resp_body_parts = []
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                resp_body_parts.append(chunk)
            resp_bytes = b"".join(resp_body_parts)

            elapsed = time.monotonic() - t0
            with _lock:
                _request_count += 1
                cnt = _request_count
            pin_tag = ""
            if pinned:
                providers = body["provider"].get("only") or body["provider"].get("order") or []
                pin_tag = f" pin={providers[0]}" if providers else ""
            log.info(
                "#%d POST %s model=%s status=%d bytes=%d %.1fs%s",
                cnt, self.path, model or "?", resp.status, len(resp_bytes), elapsed, pin_tag,
            )
            if resp.status >= 400:
                with _lock:
                    _error_count += 1
                error_detail = _extract_error(resp_bytes)
                log.error("#%d upstream %d for %s: %s", cnt, resp.status, model, error_detail)
                if resp.status in (402, 429):
                    log.error(
                        "#%d BILLING/RATE LIMIT: %s (total errors so far: %d)",
                        cnt, error_detail, _error_count,
                    )

        except Exception as exc:
            elapsed = time.monotonic() - t0
            with _lock:
                _request_count += 1
                _error_count += 1
                cnt = _request_count
            log.error("#%d POST %s model=%s FAILED after %.1fs: %s", cnt, self.path, model or "?", elapsed, exc)
            try:
                self.send_error(502, "upstream error")
            except Exception:
                pass
        finally:
            conn.close()

    def do_GET(self):
        upstream_path = UPSTREAM_PREFIX + self.path
        headers = {"Authorization": f"Bearer {self.api_key}"}

        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(UPSTREAM, timeout=60, context=ctx)
        try:
            conn.request("GET", upstream_path, headers=headers)
            resp = conn.getresponse()
            body = resp.read()

            self.send_response(resp.status)
            for key, val in resp.getheaders():
                if key.lower() not in ("transfer-encoding", "connection"):
                    self.send_header(key, val)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            log.info("GET %s status=%d bytes=%d", self.path, resp.status, len(body))
        except Exception as exc:
            log.error("GET %s FAILED: %s", self.path, exc)
            try:
                self.send_error(502, "upstream error")
            except Exception:
                pass
        finally:
            conn.close()

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="OpenRouter provider-pinning proxy")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()

    ProxyHandler.api_key = get_api_key()

    server = ThreadedHTTPServer((args.bind, args.port), ProxyHandler)
    log.info("listening on %s:%d", args.bind, args.port)
    log.info("provider pinning for %d models", len(PROVIDER_ROUTING))
    for model, routing in PROVIDER_ROUTING.items():
        providers = routing.get("only") or routing.get("order") or []
        log.info("  %s -> %s", model, providers)
    log.info("key: %s... (%d chars)", ProxyHandler.api_key[:4], len(ProxyHandler.api_key))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down (requests=%d errors=%d)", _request_count, _error_count)
    server.server_close()


if __name__ == "__main__":
    main()
