# convert.py
# SPDX-License-Identifier: MIT
"""
Convert bytes or decoded file text into JSONL-ready records.

This module exposes:
  • `make_records_for_file(...)` - text → records
  • `make_records_from_bytes(...)` - bytes → (bytes handlers | text path) → records
  • `register_bytes_handler(sniff, handler)` - plug in new binary handlers

Processing occurs in this order:
  (1) user extractors (first non-empty wins),
  (2) default format-aware chunking.
Records are built via `records.build_record` and include stable metadata
(`chunk_id/n_chunks`, token and byte counts, path, and repo context).

Public API
----------
def make_records_for_file(
    *,
    text: str,
    rel_path: str,
    policy=None,
    repo_full_name: str | None = None,
    repo_url: str | None = None,
    license_id: str | None = None,
    encoding: str = "utf-8",
    had_replacement: bool = False,
    extractors=None,
) -> list[dict]

Behavior
--------
- Extractors: called in order; the first to return records short-circuits.
- Markdown KQL: when enabled, emits one record per fenced KQL block.
- Chunking: infers doc/code mode from extension and applies `chunk.chunk_text`.

Returns
-------
list[dict]
    JSONL-ready records; each item has `"text"` and `"meta"`.

Example
-------
>>> from repocapsule.convert import make_records_for_file
>>> recs = make_records_for_file(text="print('hi')\\n", rel_path="src/hello.py")
>>> isinstance(recs[0]["meta"], dict)
True

Notes
-----
- Pure, in-memory transformation; extractor exceptions are logged and ignored.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Iterable

import logging

from .chunk import ChunkPolicy, chunk_text
from .records import build_record
from .decode import decode_bytes_robust
from .interfaces import RepoContext, Record

__all__ = [
    "make_records_for_file",
    "make_records_from_bytes",
    "register_bytes_handler",
]

log = logging.getLogger(__name__)

# --- Bytes-handler plugin registry ---
Sniff = Callable[[bytes, str], bool]
BytesHandler = Callable[
    [bytes, str, Optional[RepoContext], Optional[ChunkPolicy]],
    Optional[Iterable[Record]],
]
_BYTES_HANDLERS: List[Tuple[Sniff, BytesHandler]] = []

def register_bytes_handler(sniff: Sniff, handler: BytesHandler) -> None:
    _BYTES_HANDLERS.append((sniff, handler))

# --- Built-in PDF handler (late import to avoid hard coupling) ---
def _sniff_pdf(data: bytes, rel: str) -> bool:
    return rel.lower().endswith(".pdf") or data.startswith(b"%PDF-")

def _handle_pdf(
    data: bytes,
    rel: str,
    ctx: Optional[RepoContext],
    policy: Optional[ChunkPolicy],
) -> Optional[Iterable[Record]]:
    from .pdfio import extract_pdf_records
    return extract_pdf_records(
        data,
        rel_path=rel,
        policy=policy,
        repo_full_name=(ctx.repo_full_name if ctx else None),
        repo_url=(ctx.repo_url if ctx else None),
        license_id=(ctx.license_id if ctx else None),
    )

register_bytes_handler(_sniff_pdf, _handle_pdf)

# ---------------------------------------------------------------------------
# Record creation for a single file 
# ---------------------------------------------------------------------------

# Shape of the callable adapter the pipeline passes into this module
# (it wraps interfaces.Extractor.extract and returns an Iterable[Record])
ExtractorFn = Callable[[str, str], Optional[Iterable[Record]]]

_MD_EXTS = {".md", ".mdx", ".markdown"}
_RST_EXTS = {".rst"}
_DOC_EXTS = _MD_EXTS | _RST_EXTS | {".adoc", ".txt"}


def _infer_mode_and_fmt(rel_path: str) -> Tuple[str, Optional[str]]:
    """Return (mode, fmt) from filename."""
    ext = Path(rel_path).suffix.lower()
    if ext in _MD_EXTS:
        return "doc", "md"
    if ext in _RST_EXTS:
        return "doc", "rst"
    if ext in _DOC_EXTS:
        return "doc", None
    return "code", None


# --- Public entrypoint the pipeline can call ---

def make_records_for_file(
    *,
    text: str,
    rel_path: str,
    policy: Optional[ChunkPolicy] = None,
    repo_full_name: Optional[str] = None,
    repo_url: Optional[str] = None,
    license_id: Optional[str] = None,
    encoding: str = "utf-8",
    had_replacement: bool = False,
    extractors: Optional[Sequence[ExtractorFn]] = None,
) -> List[Dict[str, object]]:
    policy = policy or ChunkPolicy()
    rp = rel_path.replace("\\", "/")

    # 1) Adapter: user-provided extractors (first non-None wins)
    if extractors:
        for ex in extractors:
            try:
                out = ex(text, rp)
            except Exception as e:
                log.warning("Extractor failed for %s: %s", rp, e)
                out = None
            if out:
                return list(out)

    # 2) Default: format-aware chunking
    mode, fmt = _infer_mode_and_fmt(rp)
    chunks = chunk_text(text, mode=mode, fmt=fmt, policy=policy)

    out: List[Dict[str, object]] = []
    n = len(chunks)
    for i, ch in enumerate(chunks, start=1):
        rec = build_record(
            text=ch.get("text", ""),
            rel_path=rp,
            repo_full_name=repo_full_name,
            repo_url=repo_url,
            license_id=license_id,
            encoding=encoding,
            had_replacement=had_replacement,
            chunk_id=i,
            n_chunks=n,
            # prefer chunker-supplied language when present
            lang=ch.get("lang") if isinstance(ch, dict) and "lang" in ch else None,
        )
        out.append(rec)
    return out

def make_records_from_bytes(
    data: bytes,
    rel_path: str,
    *,
    policy: Optional[ChunkPolicy] = None,
    context: Optional[RepoContext] = None,
    extractors: Optional[Sequence[ExtractorFn]] = None,
) -> List[Dict[str, object]]:
    # 1) Try registered byte handlers first (PDF, future binaries, etc.)
    for sniff, handler in _BYTES_HANDLERS:
        try:
            if sniff(data, rel_path):
                recs = handler(data, rel_path, context, policy)
                if recs:
                    return list(recs)
        except Exception as e:
            # Fall back to text path on handler failure
            # (Keep at debug level to avoid noisy logs in normal runs.)
            log.debug("Bytes-handler %r failed for %s: %s",
                      getattr(handler, "__name__", handler), rel_path, e)
            
            # continue to generic decode
    # 2) Generic text path
    text = decode_bytes_robust(data)
    # We don’t currently track real encoding/replacement; preserve existing defaults.
    return make_records_for_file(
        text=text,
        rel_path=rel_path,
        policy=policy,
        repo_full_name=(context.repo_full_name if context else None),
        repo_url=(context.repo_url if context else None),
        license_id=(context.license_id if context else None),
        encoding="utf-8",
        had_replacement=False,
        extractors=extractors,
    )