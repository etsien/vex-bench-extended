#!/usr/bin/env python3
"""Lightweight proxy that injects OpenRouter provider routing into requests.

Ensures deterministic fp8 provider pinning for benchmark reproducibility.
Runs on the host; Docker containers reach it via --network host.

Usage:
    OPENROUTER_API_KEY=sk-or-... python scripts/openrouter_proxy.py
    # or: OPENROUTER_API_KEY=sk-or-... python scripts/openrouter_proxy.py --port 8788

The proxy:
  1. Receives OpenAI-compatible requests from OpenCode (inside Docker)
  2. Injects the `provider` field based on the model name
  3. Forwards to https://openrouter.ai/api/v1
  4. Streams the response back unmodified
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import os
import ssl
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

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
        "only": ["baidu/fp8"],
        "allow_fallbacks": False,
    },
    "deepseek/deepseek-v4-flash": {
        "only": ["streamlake/fp8"],
        "allow_fallbacks": False,
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
        "only": ["baidu/fp8"],
        "allow_fallbacks": False,
    },
}


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
        content_len = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_len) if content_len else b""

        body = None
        model = None
        if raw_body:
            try:
                body = json.loads(raw_body)
                model = body.get("model")
            except json.JSONDecodeError:
                pass

        if body and model and model in PROVIDER_ROUTING:
            if "provider" not in body:
                body["provider"] = PROVIDER_ROUTING[model]
                log.info("pinned %s -> %s", model, body["provider"]["only"])
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
        conn = http.client.HTTPSConnection(UPSTREAM, context=ctx)
        try:
            conn.request("POST", upstream_path, body=raw_body, headers=headers)
            resp = conn.getresponse()

            self.send_response(resp.status)
            for key, val in resp.getheaders():
                if key.lower() not in ("transfer-encoding", "connection"):
                    self.send_header(key, val)
            self.send_header("Connection", "close")
            self.end_headers()

            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except Exception:
            log.exception("upstream request failed")
            self.send_error(502, "upstream error")
        finally:
            conn.close()

    def do_GET(self):
        upstream_path = UPSTREAM_PREFIX + self.path
        headers = {"Authorization": f"Bearer {self.api_key}"}

        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(UPSTREAM, context=ctx)
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
        except Exception:
            log.exception("upstream request failed")
            self.send_error(502, "upstream error")
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

    server = HTTPServer((args.bind, args.port), ProxyHandler)
    log.info("listening on %s:%d", args.bind, args.port)
    log.info("provider pinning for %d models", len(PROVIDER_ROUTING))
    for model, routing in PROVIDER_ROUTING.items():
        log.info("  %s -> %s", model, routing["only"])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    server.server_close()


if __name__ == "__main__":
    main()
