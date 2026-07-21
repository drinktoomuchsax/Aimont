"""Shared helpers for the test suite."""

from __future__ import annotations

import socket


def free_port() -> int:
    """Return a currently-free TCP port on localhost.

    There is an unavoidable TOCTOU window between this returning and the
    caller binding the port; SO_REUSEADDR lets the caller rebind the same
    port immediately after we release it, which is the common case here
    (uvicorn binds a moment later). Good enough for tests.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
