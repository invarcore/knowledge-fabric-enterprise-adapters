# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Minimal HTTP service for private adapter health and status checks."""

from __future__ import annotations

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from enterprise_adapters.auth import auth_is_enabled, validate_bearer_token
from enterprise_adapters.contracts import AdapterContext

# Health endpoints are public so Kubernetes probes work without credentials.
_PUBLIC_PATHS = {"/healthz", "/readyz"}


class AdapterHTTPServer(ThreadingHTTPServer):
    """HTTP server that carries adapter context."""

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        adapter_context: AdapterContext,
    ) -> None:
        super().__init__(server_address, request_handler_class)
        self.adapter_context = adapter_context


class AdapterRequestHandler(BaseHTTPRequestHandler):
    """Serve simple health and status responses."""

    server_version = "EnterpriseAdaptersHTTP/0.1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
        if self.path in _PUBLIC_PATHS:
            self._write_json(
                200,
                {
                    "status": "ok",
                    "service": self.server.adapter_context.adapter_name,
                    "environment": self.server.adapter_context.environment,
                    "auth_enabled": auth_is_enabled(),
                },
            )
            return

        auth_header = self.headers.get("Authorization", "")
        if not validate_bearer_token(auth_header):
            self._write_json(401, {"status": "unauthorized", "detail": "Valid Bearer token required"})
            return

        if self.path == "/":
            self._write_json(
                200,
                {
                    "status": "ok",
                    "service": self.server.adapter_context.adapter_name,
                    "environment": self.server.adapter_context.environment,
                    "metadata": self.server.adapter_context.metadata,
                },
            )
            return

        self._write_json(404, {"status": "not_found", "path": self.path})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003 - stdlib signature
        return

    def _write_json(self, status_code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(host: str | None = None, port: int | None = None) -> None:
    """Run the private adapter status service."""
    import os

    resolved_host = host or os.environ.get("ADAPTER_HOST", "127.0.0.1")
    resolved_port = port if port is not None else int(os.environ.get("ADAPTER_PORT", "8080"))

    context = AdapterContext(
        adapter_name="enterprise-adapters",
        environment=os.environ.get("ENVIRONMENT", "local"),
        metadata={"mode": "http"},
    )
    server = AdapterHTTPServer((resolved_host, resolved_port), AdapterRequestHandler, context)
    print(
        f"Enterprise adapter HTTP service listening on {resolved_host}:{resolved_port} "
        f"for {asdict(context)}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
