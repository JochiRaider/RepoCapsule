# decode.py
# SPDX-License-Identifier: MIT
"""Helpers to decode bytes into normalized text with simple heuristics."""

from __future__ import annotations

import os
import re
import unicodedata as _ud
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Callable, Literal, Protocol

from .log import get_logger

__all__ = [
    "decode_bytes",            # public API
    "read_decoded_text",       # returns DecodedText
    "read_text",               # returns str
]


@dataclass(slots=True, frozen=True)
class DecodeProvenance:
    """Provenance for decode and post-processing transformations."""

    decode_replacements: bool = False
    mojibake_repaired: bool = False
    controls_stripped: int = 0
    newlines_normalized: bool = False
    unicode_normalized: bool = False

    @property
    def lossy(self) -> bool:
        """Return True when decoding or postprocess removed/replaced characters."""
        return self.decode_replacements or self.controls_stripped > 0

    @property
    def changed(self) -> bool:
        """Return True when any transformation modified the decoded text."""
        return (
            self.decode_replacements
            or self.mojibake_repaired
            or self.controls_stripped > 0
            or self.newlines_normalized
            or self.unicode_normalized
        )


@dataclass(slots=True, frozen=True)
class DecodedText:
    """Decoded text content with encoding metadata and provenance flags."""

    text: str
    encoding: str
    had_replacement: bool
    provenance: DecodeProvenance = DecodeProvenance()

log = get_logger(__name__)

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


def _detect_bom(data: bytes) -> str | None:
    """Return encoding implied by a BOM if present."""
    for sig, enc in sorted(_BOMS, key=lambda item: len(item[0]), reverse=True):
        if data.startswith(sig):
            return enc
    return None


def _guess_utf16_endian_from_nuls(sample: bytes) -> str | None:
    """Infer UTF-16 endianness from NUL distribution in a byte sample.

    Heuristic: in ASCII-heavy UTF-16 text, one byte of each 2-byte unit is
    NUL. Count NULs at even versus odd offsets and pick the side that has
    significantly more NUL bytes.

    Args:
        sample (bytes): Leading bytes from a file.

    Returns:
        str | None: "utf-16-le" or "utf-16-be" when confident, otherwise None.
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


def _normalize_newlines(s: str) -> tuple[str, bool]:
    """Normalize CRLF and CR-only sequences to LF."""
    normalized = s.replace("\r\n", "\n").replace("\r", "\n")
    return normalized, normalized != s


def _strip_unsafe_controls(s: str) -> tuple[str, int]:
    """Strip control and zero-width characters while keeping TAB and LF."""
    filtered = "".join(
        ch for ch in s
        if (ch == "\n" or ch == "\t" or _ud.category(ch)[0] != "C") and ord(ch) not in _ZERO_WIDTH
    )
    return filtered, max(0, len(s) - len(filtered))


NormalizeForm = Literal["NFC", "NFD", "NFKC", "NFKD"]


class DecodeFunc(Protocol):
    """Protocol for byte-to-text decoding helpers."""

    def __call__(
        self,
        data: bytes,
        *,
        normalize: NormalizeForm | None = "NFC",
        strip_controls: bool = True,
        fix_mojibake: bool = True,
    ) -> DecodedText:
        ...


def _unicode_normalize(s: str, *, form: NormalizeForm = "NFC") -> tuple[str, bool]:
    """Apply Unicode normalization, falling back to the original string."""
    try:
        normalized = _ud.normalize(form, s)
    except Exception:
        return s, False
    return normalized, normalized != s


# ----------------------
# Mojibake repair helpers
# ----------------------

# Quick check for typical UTF-8-as-cp1252 sequences (e.g., 'Ã©', 'â€™', 'Â').
_MOJI_REGEX = re.compile(r"[\u00C0-\u00FF][\u0080-\u00FF]|Ã.|â.|Â|Â\s|\ufffd")


def _mojibake_score(s: str) -> int:
    """Count likely mojibake sequences in the string."""
    return len(_MOJI_REGEX.findall(s))


def _maybe_repair_cp1252_utf8(text_cp1252: str) -> str:
    """Repair text likely mis-decoded as cp1252 when it was UTF-8.

    The repair is accepted only if it reduces the detected mojibake noise.

    Args:
        text_cp1252 (str): Text decoded as cp1252.

    Returns:
        str: Repaired or original text, whichever looks cleaner.
    """
    if _mojibake_score(text_cp1252) == 0:
        return text_cp1252
    try:
        raw = text_cp1252.encode("cp1252", errors="strict")
        fixed = raw.decode("utf-8", errors="strict")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text_cp1252
    # Accept the repair only if it *reduces* the mojibake noise.
    fixed_score = _mojibake_score(fixed)
    orig_score = max(1, _mojibake_score(text_cp1252))
    return fixed if fixed_score * 3 < orig_score else text_cp1252


# ------------------------
# Core decoding entrypoints
# ------------------------

def decode_bytes(
    data: bytes,
    *,
    normalize: NormalizeForm | None = "NFC",
    strip_controls: bool = True,
    fix_mojibake: bool = True,
) -> DecodedText:
    """Decode bytes into normalized text with heuristics and repairs.

    Strategy:
      1) Honor BOMs for UTF-8/16/32; utf-8-sig strips the BOM.
      2) Try UTF-8 strictly; if it fails, guess UTF-16 endianness by NULs.
      3) Try cp1252 strictly, else latin-1 with optional mojibake fix.
      4) Normalize newlines, strip controls, and apply Unicode normalization.

    Args:
        data (bytes): Raw bytes to decode.
        normalize (str | None): Unicode normalization form or None to skip.
        strip_controls (bool): Whether to remove control and zero-width chars.
        fix_mojibake (bool): Whether to attempt cp1252/UTF-8 mojibake repair.

    Returns:
        DecodedText: Text plus encoding and provenance metadata. The
        ``had_replacement`` flag indicates replacement characters were
        introduced during decoding (not post-processing).
    """
    if not data:
        return DecodedText("", "utf-8", False, DecodeProvenance())

    def _finalize(
        text: str,
        encoding: str,
        *,
        decode_replacements: bool,
        mojibake_repaired: bool,
    ) -> DecodedText:
        processed, post = _postprocess(text, normalize=normalize, strip_controls=strip_controls)
        provenance = DecodeProvenance(
            decode_replacements=decode_replacements,
            mojibake_repaired=mojibake_repaired,
            controls_stripped=post.controls_stripped,
            newlines_normalized=post.newlines_normalized,
            unicode_normalized=post.unicode_normalized,
        )
        return DecodedText(processed, encoding, decode_replacements, provenance)

    # 1) BOM-driven decode
    enc = _detect_bom(data)
    if enc:
        try:
            # For utf-8-sig, BOM is automatically stripped.
            text = data.decode(enc, errors="strict")
            return _finalize(text, enc, decode_replacements=False, mojibake_repaired=False)
        except UnicodeDecodeError:
            pass  # fall through

    # 2) UTF-8 first
    try:
        text = data.decode("utf-8", errors="strict")
        return _finalize(text, "utf-8", decode_replacements=False, mojibake_repaired=False)
    except UnicodeDecodeError:
        pass

    # 2b) Heuristic UTF-16 guess (no BOM)
    guess = _guess_utf16_endian_from_nuls(data[:4096])
    if guess:
        try:
            text = data.decode(guess, errors="strict")
            return _finalize(text, guess, decode_replacements=False, mojibake_repaired=False)
        except UnicodeDecodeError:
            pass

    # 3) cp1252 fallback with optional mojibake repair
    try:
        text1252 = data.decode("cp1252", errors="strict")
        enc_used = "cp1252"
        decode_replacements = False
    except UnicodeDecodeError:
        # As a last resort, latin-1 (will never fail)
        text1252 = data.decode("latin-1", errors="replace")
        enc_used = "latin-1"
        decode_replacements = "\ufffd" in text1252

    mojibake_repaired = False
    if fix_mojibake:
        repaired = _maybe_repair_cp1252_utf8(text1252)
        mojibake_repaired = repaired != text1252
        text1252 = repaired

    return _finalize(
        text1252,
        enc_used,
        decode_replacements=decode_replacements,
        mojibake_repaired=mojibake_repaired,
    )


@dataclass(slots=True, frozen=True)
class PostprocessResult:
    """Track post-processing changes applied to decoded text."""

    newlines_normalized: bool = False
    controls_stripped: int = 0
    unicode_normalized: bool = False


def _postprocess(
    s: str,
    *,
    normalize: NormalizeForm | None,
    strip_controls: bool,
) -> tuple[str, PostprocessResult]:
    """Normalize newlines, strip controls, and apply Unicode normalization."""
    s, newlines_normalized = _normalize_newlines(s)
    if strip_controls:
        s, controls_stripped = _strip_unsafe_controls(s)
    else:
        controls_stripped = 0
    if normalize:
        s, unicode_normalized = _unicode_normalize(s, form=normalize)
    else:
        unicode_normalized = False
    return s, PostprocessResult(
        newlines_normalized=newlines_normalized,
        controls_stripped=controls_stripped,
        unicode_normalized=unicode_normalized,
    )


def read_decoded_text(
    path: str | bytes | Path,
    *,
    max_bytes: int | None = None,
    normalize: NormalizeForm | None = "NFC",
    strip_controls: bool = True,
    fix_mojibake: bool = True,
    opener: Callable[[], IO[bytes]] | None = None,
    decoder: DecodeFunc = decode_bytes,
) -> DecodedText | None:
    """Read file bytes and decode them, returning a structured result or None."""
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    p = Path(path)
    try:
        if opener is not None:
            with closing(opener()) as f:
                data = f.read(max_bytes) if max_bytes is not None else f.read()
        else:
            with p.open("rb") as f:
                data = f.read(max_bytes) if max_bytes is not None else f.read()
    except (OSError, ValueError) as e:
        log.warning("read_decoded_text: failed to read %s: %s", p, e)
        return None
    return decoder(
        data,
        normalize=normalize,
        strip_controls=strip_controls,
        fix_mojibake=fix_mojibake,
    )


def read_text(
    path: str | bytes | Path,
    *,
    max_bytes: int | None = None,
    normalize: NormalizeForm | None = "NFC",
    strip_controls: bool = True,
    fix_mojibake: bool = True,
    opener: Callable[[], IO[bytes]] | None = None,
    decoder: DecodeFunc = decode_bytes,
) -> str:
    """Read file bytes and decode them to a text string.

    Args:
        path (str | bytes | Path): Path to the file to read.
        max_bytes (int | None): Optional cap on bytes read for sampling.
        normalize (str | None): Unicode normalization form such as "NFC".
        strip_controls (bool): Remove control and zero-width characters.
        fix_mojibake (bool): Attempt to repair UTF-8-as-cp1252 mojibake.
        opener (Callable[[], IO[bytes]] | None): Optional stream opener for
            policy-controlled file access. When set, ``path`` is used only
            for logging.
        decoder (DecodeFunc): Decoder function to turn bytes into text.

    Returns:
        str: Decoded text content; empty on read failure.
    """
    dec = read_decoded_text(
        path,
        max_bytes=max_bytes,
        normalize=normalize,
        strip_controls=strip_controls,
        fix_mojibake=fix_mojibake,
        opener=opener,
        decoder=decoder,
    )
    return dec.text if dec is not None else ""
