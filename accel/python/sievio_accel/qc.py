"""QC accelerators backed by the native Rust extension."""
from __future__ import annotations

import random
import threading
from collections.abc import Sequence

from ._native import qc as _qc_native

__all__ = [
    "simhash64",
    "minhash_signature_for_text",
    "minhash_signature_with_coeffs",
]

_PRIME32 = 4294967311
_MINHASH_SEED = 0x5EED5EED
_MINHASH_MAX_PERMS = 8192
_MINHASH_RNG = random.Random(_MINHASH_SEED)
_MINHASH_COEFS: list[tuple[int, int]] = []
_MINHASH_LOCK = threading.Lock()


def _minhash_coeffs(n_perm: int) -> list[tuple[int, int]]:
    """Return deterministic MinHash coefficients for a given permutation count."""
    if n_perm < 0:
        raise ValueError(f"n_perm must be non-negative; got {n_perm!r}.")
    if n_perm > _MINHASH_MAX_PERMS:
        raise ValueError(
            f"n_perm must be <= {_MINHASH_MAX_PERMS}; got {n_perm!r}."
        )
    if n_perm == 0:
        return []
    if len(_MINHASH_COEFS) < n_perm:
        # Guard RNG/cache mutation so concurrent callers can't interleave coefficients.
        with _MINHASH_LOCK:
            if len(_MINHASH_COEFS) < n_perm:
                for _ in range(len(_MINHASH_COEFS), n_perm):
                    a = _MINHASH_RNG.randrange(1, _PRIME32 - 1)
                    b = _MINHASH_RNG.randrange(0, _PRIME32 - 1)
                    _MINHASH_COEFS.append((a, b))
    return _MINHASH_COEFS[:n_perm]


def simhash64(text: str, *, max_tokens: int | None = None) -> int:
    """Compute a 64-bit Simhash fingerprint using the native extension."""
    return int(_qc_native.simhash64(text, max_tokens))


def minhash_signature_with_coeffs(
    text: str,
    *,
    k: int,
    n_perm: int,
    max_shingles: int | None = None,
    coeffs: Sequence[tuple[int, int]],
) -> tuple[int, ...]:
    """Compute a MinHash signature using explicit coefficients."""
    if len(coeffs) != n_perm:
        raise ValueError(
            f"coeffs length {len(coeffs)!r} does not match n_perm={n_perm!r}."
        )
    sig = _qc_native.minhash_signature_with_coeffs(
        text,
        k,
        list(coeffs),
        max_shingles,
    )
    return tuple(int(val) for val in sig)


def minhash_signature_for_text(
    text: str,
    *,
    k: int,
    n_perm: int,
    max_shingles: int | None = None,
) -> tuple[int, ...]:
    """Build a deterministic MinHash signature for text."""
    coeffs = _minhash_coeffs(n_perm)
    return minhash_signature_with_coeffs(
        text,
        k=k,
        n_perm=n_perm,
        max_shingles=max_shingles,
        coeffs=coeffs,
    )
