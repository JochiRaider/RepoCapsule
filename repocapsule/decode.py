# decode.py
# SPDX-License-Identifier: MIT
"""
Robust byte→text decoding utilities for RepoCapsule.

This module provides pragmatic, dependency-light functions for turning raw
bytes (or files on disk) into normalized Unicode text. The strategy favors
deterministic behavior and speed over exhaustive charset detection and is
tailored for source-code and documentation corpora.

Strategy
--------
1) Honor BOMs for UTF-8/UTF-16/UTF-32 when present.
2) Try UTF-8 (strict). On failure, heuristically probe UTF-16 endianness by
   inspecting the distribution of NUL bytes in an initial sample.
3) Fall back to cp1252 (strict); optionally repair common UTF-8-as-cp1252
   mojibake sequences. As a last resort, decode with latin-1.
4) Post-process text by normalizing newlines to LF, stripping zero-width and
   control characters (keeps TAB and LF), and applying Unicode normalization
   (NFC by default).

Public API
----------
- decode_bytes_robust(
      data: bytes,
      *,
      normalize: str | None = "NFC",
      strip_controls: bool = True,
      fix_mojibake: bool = True,
  ) -> str
    Decode an in-memory byte string using the strategy above and return clean
    Unicode text.

- read_text_robust(
      path: str | bytes | pathlib.Path,
      *,
      max_bytes: int | None = None,
      normalize: str | None = "NFC",
      strip_controls: bool = True,
      fix_mojibake: bool = True,
  ) -> str
    Read a file as bytes (optionally capped by `max_bytes`) and decode with
    `decode_bytes_robust`. I/O errors are logged and yield an empty string.

Behavior notes
--------------
- Zero-width characters such as U+200B/ZERO WIDTH SPACE and U+FEFF (when not a
  BOM) are removed when `strip_controls=True`.
- Newlines are normalized: CRLF and CR → LF.
- UTF-16 without a BOM is guessed from the even/odd NUL distribution in an
  ASCII-heavy sample; the heuristic is conservative.
- Mojibake repair attempts to reverse the common “UTF-8 bytes decoded as
  cp1252” pattern and is only accepted if it measurably reduces noise.

Examples
--------
Decode raw bytes:

>>> from repocapsule.decode import decode_bytes_robust
>>> decode_bytes_robust(b'hello\\r\\nworld')
'hello\\nworld'

Read and decode a file with defaults:

>>> from repocapsule.decode import read_text_robust
>>> text = read_text_robust("README.md")
>>> isinstance(text, str)
True

Sample only the first 64 KiB (useful for huge files):

>>> preview = read_text_robust("big.log", max_bytes=65536)

Caveats
-------
- This is not a full charset detector; it intentionally avoids external
  dependencies like `chardet/charset_normalizer`.
- Binary data may produce meaningless output; typical callers should filter
  file types upstream.
- Unicode normalization defaults to NFC; pass `normalize=None` to disable
  or a different form (e.g., "NFKC") to change behavior.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
import logging
import re
import unicodedata as _ud

__all__ = [
    "decode_bytes_robust",
    "read_text_robust",
]

log = logging.getLogger(__name__)

# -----------------------------------------
# Encoding helpers: BOM + UTF-16/32 heuristics
# -----------------------------------------

_BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\xEF\xBB\xBF", "utf-8-sig"),
    (b"\xFE\xFF", "utf-16-be"),
    (b"\xFF\xFE", "utf-16-le"),
    (b"\x00\x00\xFE\xFF", "utf-32-be"),
    (b"\xFF\xFE\x00\x00", "utf-32-le"),
)


def _detect_bom(data: bytes) -> Optional[str]:
    for sig, enc in _BOMS:
        if data.startswith(sig):
            return enc
    return None


def _guess_utf16_endian_from_nuls(sample: bytes) -> Optional[str]:
    """Return 'utf-16-le' or 'utf-16-be' if NUL distribution strongly hints it.

    Heuristic: in ASCII-heavy UTF-16 text, one byte of each 2-byte unit is NUL.
    Count NULs at even vs odd offsets in a window; prefer the side with more
    NULs if significantly higher.
    """
    if not sample:
        return None
    even_nuls = sum(1 for i in range(0, len(sample), 2) if sample[i] == 0)
    odd_nuls = sum(1 for i in range(1, len(sample), 2) if sample[i] == 0)
    total_nuls = even_nuls + odd_nuls
    if total_nuls < max(4, len(sample) // 64):  # need a few to be confident
        return None
    if even_nuls > odd_nuls * 2:
        return "utf-16-be"  # 00 xx 00 xx ...
    if odd_nuls > even_nuls * 2:
        return "utf-16-le"  # xx 00 xx 00 ...
    return None


# ----------------------
# Unicode cleanup helpers
# ----------------------

_ZERO_WIDTH = {
    0x200B,  # ZERO WIDTH SPACE
    0x200C,  # ZERO WIDTH NON-JOINER
    0x200D,  # ZERO WIDTH JOINER
    0x2060,  # WORD JOINER (replacement for ZWNBSP)
    0xFEFF,  # ZERO WIDTH NO-BREAK SPACE (BOM when leading)
}


def _normalize_newlines(s: str) -> str:
    # Normalize CRLF and CR-only to LF
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s


def _strip_unsafe_controls(s: str) -> str:
    # Keep TAB, LF, and (already normalized) no CR; drop other C0/C1 controls.
    return "".join(
        ch for ch in s
        if (ch == "\n" or ch == "\t" or _ud.category(ch)[0] != "C") and ord(ch) not in _ZERO_WIDTH
    )


def _unicode_normalize(s: str, *, form: str = "NFC") -> str:
    try:
        return _ud.normalize(form, s)
    except Exception:
        return s


# ----------------------
# Mojibake repair helpers
# ----------------------

# Quick check for typical UTF-8-as-cp1252 sequences (e.g., 'Ã©', 'â€™', 'Â').
_MOJI_REGEX = re.compile(r"[\u00C0-\u00FF][\u0080-\u00FF]|Ã.|â.|Â|Â\s|\ufffd")


def _mojibake_score(s: str) -> int:
    return len(_MOJI_REGEX.findall(s))


def _maybe_repair_cp1252_utf8(text_cp1252: str) -> str:
    """Attempt to repair a string that likely came from UTF-8 bytes
    mis-decoded as cp1252. If repair improves the mojibake score, return it.
    """
    try:
        raw = text_cp1252.encode("cp1252", errors="ignore")
        fixed = raw.decode("utf-8", errors="strict")
    except Exception:
        return text_cp1252
    # Accept the repair only if it *reduces* the mojibake noise.
    return fixed if _mojibake_score(fixed) * 3 < max(1, _mojibake_score(text_cp1252)) else text_cp1252


# ------------------------
# Core decoding entrypoints
# ------------------------

def decode_bytes_robust(
    data: bytes,
    *,
    normalize: Optional[str] = "NFC",
    strip_controls: bool = True,
    fix_mojibake: bool = True,
) -> str:
    """Decode bytes into text using pragmatic fallbacks.

    Strategy:
      1) Honor BOM for UTF-8/16/32 when present.
      2) Try UTF-8 (strict). If it fails, probe UTF-16 by NUL pattern.
      3) Fallback to cp1252; optionally repair common UTF‑8-as-cp1252 mojibake.
      4) Normalize newlines, strip zero-width & control chars (optional),
         and apply Unicode normalization (NFC by default).
    """
    if not data:
        return ""

    # 1) BOM-driven decode
    enc = _detect_bom(data)
    if enc:
        try:
            # For utf-8-sig, BOM is automatically stripped.
            text = data.decode(enc, errors="strict")
            return _postprocess(text, normalize=normalize, strip_controls=strip_controls)
        except Exception:
            pass  # fall through

    # 2) UTF-8 first
    try:
        text = data.decode("utf-8", errors="strict")
        return _postprocess(text, normalize=normalize, strip_controls=strip_controls)
    except UnicodeDecodeError:
        pass

    # 2b) Heuristic UTF-16 guess (no BOM)
    guess = _guess_utf16_endian_from_nuls(data[:4096])
    if guess:
        try:
            text = data.decode(guess, errors="strict")
            return _postprocess(text, normalize=normalize, strip_controls=strip_controls)
        except Exception:
            pass

    # 3) cp1252 fallback (always succeeds), with optional mojibake repair
    try:
        text1252 = data.decode("cp1252", errors="strict")
    except Exception:
        # As a last resort, latin-1
        text1252 = data.decode("latin-1", errors="replace")

    if fix_mojibake:
        text1252 = _maybe_repair_cp1252_utf8(text1252)

    return _postprocess(text1252, normalize=normalize, strip_controls=strip_controls)


def _postprocess(s: str, *, normalize: Optional[str], strip_controls: bool) -> str:
    s = _normalize_newlines(s)
    if strip_controls:
        s = _strip_unsafe_controls(s)
    if normalize:
        s = _unicode_normalize(s, form=normalize)
    return s


def read_text_robust(
    path: str | bytes | Path,
    *,
    max_bytes: Optional[int] = None,
    normalize: Optional[str] = "NFC",
    strip_controls: bool = True,
    fix_mojibake: bool = True,
) -> str:
    """Read file as bytes and decode robustly.

    Args:
        path: file path
        max_bytes: if provided, read at most this many bytes (useful for sampling)
        normalize: Unicode normalization form (e.g., 'NFC', 'NFKC') or None
        strip_controls: remove zero-width and control chars except TAB/LF
        fix_mojibake: attempt to repair common UTF-8-as-cp1252 mojibake
    """
    p = Path(path)
    try:
        with p.open("rb") as f:
            data = f.read(max_bytes) if max_bytes else f.read()
    except OSError as e:
        log.warning("read_text_robust: failed to read %s: %s", p, e)
        return ""
    return decode_bytes_robust(
        data,
        normalize=normalize,
        strip_controls=strip_controls,
        fix_mojibake=fix_mojibake,
    )
