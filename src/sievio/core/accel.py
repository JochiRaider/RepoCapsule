"""Optional accelerator loader for Sievio."""
from __future__ import annotations

import importlib
import os
from typing import Any

_ACCEL_ENV = "SIEVIO_ACCEL"
_ACCEL_CACHE: dict[str, Any | None] = {}
_DISABLE_VALUES = {"0", "false", "off", "no", "disable", "disabled"}
_REQUIRE_VALUES = {"require", "required", "force"}
_PREFER_VALUES = {"1", "true", "on", "yes", "auto", "prefer"}


def accel_mode() -> str:
    """Return the effective accel mode: 'off', 'auto', or 'require'."""
    raw = os.getenv(_ACCEL_ENV, "").strip().lower()
    if not raw:
        return "auto"
    if raw in _DISABLE_VALUES:
        return "off"
    if raw in _REQUIRE_VALUES:
        return "require"
    if raw in _PREFER_VALUES:
        return "auto"
    return "auto"


def accel_required() -> bool:
    """Return True when accelerators are required by environment."""
    return accel_mode() == "require"


def load_accel(domain: str) -> Any | None:
    """Load a domain accel module, returning None on absence unless required."""
    mode = accel_mode()
    if mode == "off":
        return None
    cached = _ACCEL_CACHE.get(domain)
    if cached is not None or domain in _ACCEL_CACHE:
        return cached
    try:
        module = importlib.import_module(f"sievio_accel.{domain}")
    except Exception as exc:
        if mode == "require":
            raise RuntimeError(
                f"sievio accel required for domain '{domain}' but could not be loaded."
            ) from exc
        _ACCEL_CACHE[domain] = None
        return None
    _ACCEL_CACHE[domain] = module
    return module


def _reset_accel_cache_for_tests() -> None:
    _ACCEL_CACHE.clear()
