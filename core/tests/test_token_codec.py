"""Tests for the AimontToken encode/decode codec (PR 4)."""

from __future__ import annotations

import base64
import json

import pytest

from aimont.auth import (
    AimontToken,
    TokenDecodeError,
    decode_token,
    encode_token,
)


def test_roundtrip_minimal():
    original = AimontToken(
        upstream_url="wss://aimont.example.com/ingest",
        auth_secret="secret-xyz",
    )
    encoded = encode_token(original)
    decoded = decode_token(encoded)
    assert decoded.upstream_url == original.upstream_url
    assert decoded.auth_secret == original.auth_secret
    assert decoded.display_name_hint is None
    assert decoded.issuer is None


def test_roundtrip_with_optional_fields():
    original = AimontToken(
        upstream_url="wss://example/ingest",
        auth_secret="s",
        display_name_hint="zhang-mbp",
        issuer="Acme Corp",
    )
    decoded = decode_token(encode_token(original))
    assert decoded == original


def test_encoded_is_url_safe_and_unpadded():
    bundle = AimontToken(
        upstream_url="wss://example/ingest" + "x" * 20,  # ensures length not div by 3
        auth_secret="s",
    )
    encoded = encode_token(bundle)
    assert "=" not in encoded
    assert "+" not in encoded
    assert "/" not in encoded
    # Decodable without user intervention.
    decode_token(encoded)


def test_decode_accepts_padded_input():
    """Users may paste tokens copied from tools that re-add '=' padding."""
    bundle = AimontToken(upstream_url="wss://x", auth_secret="s")
    encoded = encode_token(bundle)
    padded = encoded + "==="
    decode_token(padded)  # must not raise


def test_decode_empty_raises():
    with pytest.raises(TokenDecodeError):
        decode_token("")


def test_decode_invalid_base64_raises():
    with pytest.raises(TokenDecodeError):
        decode_token("not*valid*base64!")


def test_decode_non_json_raises():
    garbage = base64.urlsafe_b64encode(b"not json").rstrip(b"=").decode()
    with pytest.raises(TokenDecodeError):
        decode_token(garbage)


def test_decode_non_object_json_raises():
    arr = base64.urlsafe_b64encode(b"[1,2,3]").rstrip(b"=").decode()
    with pytest.raises(TokenDecodeError):
        decode_token(arr)


def test_decode_missing_required_fields_raises():
    payload = (
        base64.urlsafe_b64encode(json.dumps({"upstream_url": "wss://x"}).encode())
        .rstrip(b"=")
        .decode()
    )
    with pytest.raises(TokenDecodeError):
        decode_token(payload)


def test_verify_key_parameter_is_accepted_but_ignored():
    """verify_key is reserved for the future JWT PR; today it's a no-op."""
    bundle = AimontToken(upstream_url="wss://x", auth_secret="s")
    encoded = encode_token(bundle)
    # Whatever we pass, it must not affect decode behavior yet.
    decoded = decode_token(encoded, verify_key="anything-goes")
    assert decoded == bundle
