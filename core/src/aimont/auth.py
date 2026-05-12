"""Token codec for "join by token string" deployments.

A AimontToken bundles everything a daemon needs to connect to an
upstream aggregator into a single opaque string that IT can distribute
via email / Slack / employee handbook. The employee's only action is:

    aimont join <token>

This PR ships the simple form: **base64url-encoded JSON**. There is no
cryptographic signature yet — the token is not tamper-proof, and anyone
with the token can mint a connection. For public/untrusted deployments,
the dashboard side is still expected to verify the `auth_secret` against
its own allowlist.

A future PR will upgrade to signed JWTs. The `decode_token` signature
already accepts an optional `verify_key` parameter so the call sites
don't change. That parameter is ignored in this PR.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from pydantic import BaseModel, ValidationError


class AimontToken(BaseModel):
    """A self-contained connection bundle.

    upstream_url / auth_secret are the two things a daemon actually needs
    to start pushing. display_name_hint lets IT nudge employees toward a
    consistent naming scheme without hard-coding it. issuer is purely
    informational at this stage — no signature verification is performed.
    """

    upstream_url: str
    auth_secret: str
    display_name_hint: str | None = None
    issuer: str | None = None


class TokenDecodeError(ValueError):
    """Raised when an input string cannot be decoded into a AimontToken."""


def encode_token(token: AimontToken) -> str:
    """Encode a AimontToken as a compact base64url string.

    The output contains no padding ('=') and is URL-safe so it can be
    embedded in a CLI argument, an env var, or a deep link.
    """
    payload = token.model_dump(exclude_none=True)
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=")
    return encoded.decode("ascii")


def decode_token(encoded: str, *, verify_key: Any = None) -> AimontToken:
    """Decode a base64url-encoded AimontToken.

    verify_key is accepted but ignored in this PR. A future signed-JWT
    PR will use it; keeping the parameter here avoids a breaking API
    change later.
    """
    del verify_key  # reserved for future JWT verification.

    if not encoded:
        raise TokenDecodeError("token is empty")

    # Accept with or without base64 padding so users can paste either form.
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as e:
        raise TokenDecodeError(f"invalid base64 payload: {e}") from e

    # urlsafe_b64decode is lenient and can yield arbitrary bytes from
    # near-miss inputs; JSON parsing then raises UnicodeDecodeError (not
    # json.JSONDecodeError). Catch both so callers see a single exception
    # type regardless of where the bad input bails.
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise TokenDecodeError(f"token payload is not valid JSON: {e}") from e

    if not isinstance(payload, dict):
        raise TokenDecodeError("token payload must be a JSON object")

    try:
        return AimontToken.model_validate(payload)
    except ValidationError as e:
        raise TokenDecodeError(f"token missing required fields: {e}") from e
