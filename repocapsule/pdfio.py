# pdfio.py
# SPDX-License-Identifier: MIT
"""
PDF ingestion helpers: extract text and metadata into RepoCapsule records.

This module turns raw PDF bytes into RepoCapsule JSONL-ready records. It uses
`pypdf.PdfReader` for page parsing, collects classic Info metadata plus a small
subset of XMP fields, and attaches that metadata under `meta["pdf_meta"]`.
Two extraction modes are supported:

- "page"  : one record per PDF page (adds `page` and `n_pages` to metadata)
- "chunk" : joins all page text and re-chunks it with the configured policy

Public API
----------
def extract_pdf_records(
    data: bytes,
    *,
    rel_path: str,
    policy: ChunkPolicy | None = None,
    repo_full_name: str | None = None,
    repo_url: str | None = None,
    license_id: str | None = None,
    password: str | None = None,
    mode: str = "page",
) -> list[dict[str, object]]

Behavior
--------
- Decoding: PDFs are not passed through the generic byte→text decoder; instead,
  text is extracted per page via `pypdf`.
- Encryption: if the document is encrypted and a `password` is provided, a
  best-effort `reader.decrypt(password)` is attempted. On failure, the function
  returns an empty list. If no password is given, extraction proceeds and may
  yield empty text for protected pages.
- Metadata: classic fields (title, author, subject, creator, producer,
  creation_date, modification_date) and a compact XMP subset (dc_title,
  dc_creator, dc_subject, xmp_create_date, xmp_modify_date, pdf_producer) are
  captured when available. Date-like values are normalized with a minimal
  ISO-8601 helper that preserves original PDF semantics when parsing is
  ambiguous.
- Record assembly: each output record is created with `records.build_record`,
  which computes token and byte counts and merges core fields:
  `repo_full_name`, `repo_url`, `license_id`, `rel_path`, `chunk_id`, `n_chunks`.
  The `meta` always includes `kind="pdf"` and, in "page" mode, `page` and
  `n_pages`. The entire PDF metadata dictionary is attached as `pdf_meta`
  (or omitted if empty).

Returns
-------
list[dict[str, object]]
    A list of JSONL-ready records. Each item has at least keys: `"text"`
    and `"meta"`.

Example
-------
>>> from repocapsule.pdfio import extract_pdf_records
>>> policy = None  # defaults to ChunkPolicy(mode="doc")
>>> with open("example.pdf", "rb") as fh:
...     data = fh.read()
>>> recs = extract_pdf_records(
...     data,
...     rel_path="docs/example.pdf",
...     repo_full_name="owner/repo",
...     repo_url="https://github.com/owner/repo",
...     license_id="MIT",
...     mode="page",
... )
>>> recs and isinstance(recs[0]["meta"], dict)  # doctest: +ELLIPSIS
True

Notes
-----
- `pypdf` is required; for certain encrypted PDFs (e.g., AES) you may need the
  optional crypto extra per the `pypdf` documentation.
- In "chunk" mode, all page text is concatenated with blank lines and then
  re-chunked via `chunk.chunk_text(..., mode="doc")` using the provided or
  default `ChunkPolicy`.
- Helper functions `_iso8601`, `_first_lang_value`, and `_collect_pdf_metadata`
  are internal utilities for metadata normalization and should be considered
  private to this module.
"""

from __future__ import annotations
from io import BytesIO
from typing import Optional, List, Dict, Any
from datetime import datetime
from pypdf import PdfReader

from .chunk import ChunkPolicy, chunk_text
from .records import build_record

def _iso8601(v: Any) -> str | None:
    """Best-effort ISO-8601 for pypdf date-like fields (datetime or PDF 'D:YYYY...' strings)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    s = str(v)
    # Keep as-is if we can't confidently parse; callers can post-process further if desired.
    if s.startswith("D:"):
        # Minimal normalization: strip leading 'D:'; return raw string for traceability
        return s.replace("D:", "", 1)
    return s

def _first_lang_value(xmp_lang_alt: Any) -> str | None:
    """Pick a human-friendly value from an XMP language alternative dict."""
    if isinstance(xmp_lang_alt, dict):
        return xmp_lang_alt.get("x-default") or next(iter(xmp_lang_alt.values()), None)
    return str(xmp_lang_alt) if xmp_lang_alt else None

def _collect_pdf_metadata(reader: PdfReader) -> Dict[str, Any]:
    """Collect classic Info dict + a small XMP subset; drop empty/None values."""
    out: Dict[str, Any] = {}

    # Classic PDF metadata (DocumentInformation)
    info = getattr(reader, "metadata", None)
    if info:
        for k in ("title", "author", "subject", "creator", "producer"):
            val = getattr(info, k, None)
            if val:
                out[k] = val
        cd = _iso8601(getattr(info, "creation_date", None))
        md = _iso8601(getattr(info, "modification_date", None))
        if cd:
            out["creation_date"] = cd
        if md:
            out["modification_date"] = md

    # XMP subset (if present)
    xmp = getattr(reader, "xmp_metadata", None)
    if xmp:
        xmp_out: Dict[str, Any] = {}
        title = _first_lang_value(getattr(xmp, "dc_title", None))
        if title:
            xmp_out["dc_title"] = title
        creators = getattr(xmp, "dc_creator", None)
        if creators:
            xmp_out["dc_creator"] = list(creators)
        subjects = getattr(xmp, "dc_subject", None)
        if subjects:
            xmp_out["dc_subject"] = list(subjects)
        xmp_cd = _iso8601(getattr(xmp, "xmp_create_date", None))
        xmp_md = _iso8601(getattr(xmp, "xmp_modify_date", None))
        if xmp_cd:
            xmp_out["xmp_create_date"] = xmp_cd
        if xmp_md:
            xmp_out["xmp_modify_date"] = xmp_md
        producer = getattr(xmp, "pdf_producer", None)
        if producer:
            xmp_out["pdf_producer"] = producer

        if xmp_out:
            out["xmp"] = xmp_out

    return out

def extract_pdf_records(
    data: bytes,
    *,
    rel_path: str,
    policy: Optional[ChunkPolicy] = None,
    repo_full_name: Optional[str] = None,
    repo_url: Optional[str] = None,
    license_id: Optional[str] = None,
    password: Optional[str] = None,
    mode: str = "page",  # "page" => 1 record per page; "chunk" => join+chunk
) -> List[Dict[str, object]]:
    """Turn a PDF (bytes) into RepoCapsule JSONL records with metadata."""
    policy = policy or ChunkPolicy(mode="doc")
    reader = PdfReader(BytesIO(data))

    if reader.is_encrypted and password:
        # If AES is used, ensure pypdf's crypto extra is installed (see docs).
        try:
            reader.decrypt(password)
        except Exception:
            # Wrong/no password → skip
            return []

    # Try to collect metadata (robust to absence)
    try:
        pdf_meta = _collect_pdf_metadata(reader)
    except Exception:
        pdf_meta = {}

    # Extract text per page
    pages_text: List[str] = []
    for p in reader.pages:
        try:
            txt = p.extract_text() or ""
        except Exception:
            txt = ""
        pages_text.append(txt)

    records: List[Dict[str, object]] = []
    if mode == "page":
        n = len(pages_text)
        for i, text in enumerate(pages_text, start=1):
            records.append(
                build_record(
                    text=text,
                    rel_path=rel_path,
                    repo_full_name=repo_full_name,
                    repo_url=repo_url,
                    license_id=license_id,
                    chunk_id=i,
                    n_chunks=n,
                    extra_meta={"kind": "pdf", "page": i, "n_pages": n, "pdf_meta": pdf_meta or None},
                )
            )
    else:
        all_text = "\n\n".join(pages_text)
        chunks = chunk_text(all_text, mode="doc", fmt="text", policy=policy)
        n = len(chunks)
        for i, ch in enumerate(chunks, start=1):
            records.append(
                build_record(
                    text=ch["text"],
                    rel_path=rel_path,
                    repo_full_name=repo_full_name,
                    repo_url=repo_url,
                    license_id=license_id,
                    chunk_id=i,
                    n_chunks=n,
                    extra_meta={"kind": "pdf", "pdf_meta": pdf_meta or None},
                )
            )

    return records
