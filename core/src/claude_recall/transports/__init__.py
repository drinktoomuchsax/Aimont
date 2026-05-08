"""Transport registry: pluggable output channels for state frames."""

from __future__ import annotations

from typing import Type

from claude_recall.transports.base import BaseTransport

_REGISTRY: dict[str, Type[BaseTransport]] = {}


def register_transport(name: str):
    def decorator(cls: Type[BaseTransport]):
        _REGISTRY[name] = cls
        return cls
    return decorator


def get_transport_class(name: str) -> Type[BaseTransport]:
    _load_builtins()
    if name not in _REGISTRY:
        raise ValueError(f"Unknown transport: {name!r}. Available: {list(_REGISTRY.keys())}")
    return _REGISTRY[name]


def list_transports() -> list[str]:
    _load_builtins()
    return list(_REGISTRY.keys())


_loaded = False


def _load_builtins():
    global _loaded
    if _loaded:
        return
    _loaded = True
    import claude_recall.transports.websocket  # noqa: F401
    import claude_recall.transports.terminal  # noqa: F401
