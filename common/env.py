"""Typed access to environment variables (12-factor config)."""

from __future__ import annotations

import os
from collections.abc import Callable


def env[T](name: str, default: T, cast: Callable[[str], T] | None = None) -> T:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    if cast is not None:
        return cast(raw)
    return raw  # type: ignore[return-value]


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]
