# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Bearer-token authentication helpers for the private adapter HTTP service.

Set ADAPTER_API_KEY to a non-empty secret to enable auth.
If the variable is not set or is empty, auth is disabled (dev mode only).

Example:
    export ADAPTER_API_KEY="$(openssl rand -hex 32)"
"""

from __future__ import annotations

import os
import hmac


def _expected_token() -> str:
    return os.environ.get("ADAPTER_API_KEY", "").strip()


def auth_is_enabled() -> bool:
    """Return True when a non-empty API key is configured."""
    return bool(_expected_token())


def validate_bearer_token(authorization_header: str) -> bool:
    """Return True when the Authorization header carries the correct bearer token.

    Uses a constant-time comparison to prevent timing attacks.
    """
    expected = _expected_token()
    if not expected:
        return True  # auth disabled — dev mode
    if not authorization_header.lower().startswith("bearer "):
        return False
    provided = authorization_header[len("bearer "):].strip()
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
