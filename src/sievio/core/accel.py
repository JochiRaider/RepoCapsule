"""Optional accelerator loader for Sievio."""
from __future__ import annotations

import importlib
import logging
import os
from typing import Any

_ACCEL_ENV = "SIEVIO_ACCEL"
_ACCEL_CACHE: dict[str, Any | None] = {}
_ACCEL_FAILURES: dict[str, BaseException] = {}
_DISABLE_VALUES = {"0", "false", "off", "no", "disable", "disabled"}
_REQUIRE_VALUES = {"require", "required", "force"}
_PREFER_VALUES = {"1", "true", "on", "yes", "auto", "prefer"}
_LOG = logging.getLogger(__name__)


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
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing in {"sievio_accel", f"sievio_accel.{domain}"}:
            if mode == "require":
                raise RuntimeError(
                    f"sievio accel required for domain '{domain}' but is not installed."
                ) from exc
            _ACCEL_CACHE[domain] = None
            return None
        if mode == "require":
            _ACCEL_FAILURES.setdefault(domain, exc)
            raise RuntimeError(
                f"sievio accel required for domain '{domain}' but could not be loaded."
            ) from exc
        if domain not in _ACCEL_FAILURES:
            _ACCEL_FAILURES[domain] = exc
            _LOG.warning(
                "sievio accel for domain '%s' failed to import; falling back to pure-Python.",
                domain,
            )
        _ACCEL_CACHE[domain] = None
        return None
    except ImportError as exc:
        if mode == "require":
            _ACCEL_FAILURES.setdefault(domain, exc)
            raise RuntimeError(
                f"sievio accel required for domain '{domain}' but could not be loaded."
            ) from exc
        if domain not in _ACCEL_FAILURES:
            _ACCEL_FAILURES[domain] = exc
            _LOG.warning(
                "sievio accel for domain '%s' failed to import; falling back to pure-Python.",
                domain,
            )
        _ACCEL_CACHE[domain] = None
        return None
    except Exception as exc:
        if mode == "require":
            _ACCEL_FAILURES.setdefault(domain, exc)
            raise RuntimeError(
                f"sievio accel required for domain '{domain}' but could not be loaded."
            ) from exc
        if domain not in _ACCEL_FAILURES:
            _ACCEL_FAILURES[domain] = exc
            _LOG.warning(
                "sievio accel for domain '%s' failed to import; falling back to pure-Python.",
                domain,
            )
        _ACCEL_CACHE[domain] = None
        return None
    _ACCEL_CACHE[domain] = module
    return module


def get_accel_failure(domain: str) -> BaseException | None:
    """Return a cached accel import failure for diagnostics."""
    return _ACCEL_FAILURES.get(domain)


def _reset_accel_cache_for_tests() -> None:
    _ACCEL_CACHE.clear()
    _ACCEL_FAILURES.clear()
