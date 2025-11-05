# pipeline.py
# SPDX-License-Identifier: MIT
"""
Orchestrates the RepoCapsule ingestion pipeline (Source → Decode → Extract → Chunk → Sinks).

This module provides a single entry point, `run_pipeline`, that streams files from
a `Source`, decodes text robustly, optionally runs one or more `Extractor`s,
builds records according to a `ChunkPolicy`, and writes them to one or more
`Sink`s. It returns basic counters for observability and error tracking.

Pipeline stages 
----------------------------
1) Source: yields FileItem(path, data[, size]) objects.
2) Dispatch/Decode: delegated to `convert.make_records_from_bytes`
   (byte handlers for binaries; robust text decode for everything else).
3) Extract (optional): first non-None extractor result “wins” (performed inside convert).
4) Chunk/Build: performed inside convert via the configured `ChunkPolicy`.
5) Sinks: writes each record; failures are counted but do not abort the run.

Key behaviors
-------------
- Extension filtering: `include_exts` / `exclude_exts` (case-insensitive).
- Hidden paths: skip when `skip_hidden` is True (dot-prefixed segments).
- Size guard: skip files larger than `max_file_bytes` (if provided).
- File-type agnostic: no imports of specific handlers (e.g., pdfio) here.
- Sinks: each must implement `open(context)`, `write(record)`, and `close()`.

Public API
----------
- class PipelineStats:
    files, records, bytes_in, skipped_hidden, skipped_ext,
    skipped_too_large, sink_errors; `as_dict()` for serialization.
- def run_pipeline(
      source: Source,
      *,
      sinks: Sequence[Sink],
      policy: ChunkPolicy | None = None,
      context: RepoContext | None = None,
      extractors: Sequence[Extractor] | None = None,
      include_exts: Sequence[str] | None = None,
      exclude_exts: Sequence[str] | None = None,
      skip_hidden: bool = True,
      max_file_bytes: int | None = None,
  ) -> dict[str, int]

Returns
-------
dict[str, int]
    A dictionary of counters (see `PipelineStats.as_dict()`).

Example
-------
>>> from repocapsule.interfaces import Source, FileItem, Sink, RepoContext
>>> from repocapsule.pipeline import run_pipeline
>>>
>>> class MemSource(Source):
...     def iter_files(self):
...         yield FileItem(path="README.md", data=b"# Title\\n", size=9)
>>>
>>> class DevNullSink(Sink):
...     def open(self, context): pass
...     def write(self, record): pass
...     def close(self): pass
>>>
>>> stats = run_pipeline(
...     MemSource(),
...     sinks=[DevNullSink()],
...     context=RepoContext(repo_full_name="owner/repo", repo_url=None, license_id=None),
... )
>>> sorted(stats.keys())  # doctest: +ELLIPSIS
['bytes_in', 'files', 'records', 'sink_errors', 'skipped_ext', 'skipped_hidden', 'skipped_too_large']
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Callable, Dict, Set, List
from pathlib import Path
import os

from .interfaces import Source, Extractor, Sink, RepoContext, Record
from .convert import make_records_from_bytes
from .chunk import ChunkPolicy
from .log import get_logger

log = get_logger(__name__)


# ------------------------
# Helper: path predicates
# ------------------------

def _is_hidden(rel_path: str) -> bool:
    # Hidden if any segment (excluding drive) starts with '.'
    parts = Path(rel_path).parts
    return any(p.startswith('.') for p in parts if p not in ('.', '..'))


def _normalize_exts(exts: Optional[Sequence[str]]) -> Optional[Set[str]]:
    if not exts:
        return None
    out: Set[str] = set()
    for e in exts:
        e = e.strip().lower()
        if not e:
            continue
        if not e.startswith('.'):
            e = '.' + e
        out.add(e)
    return out


def _should_skip_by_ext(rel_path: str, include: Optional[Set[str]], exclude: Optional[Set[str]]) -> bool:
    # If include is provided, only allow those; otherwise exclude controls
    ext = Path(rel_path).suffix.lower()
    if include is not None and ext not in include:
        return True
    if exclude is not None and ext in exclude:
        return True
    return False

# ------------------------
# Counters
# ------------------------

@dataclass
class PipelineStats:
    files: int = 0
    records: int = 0
    bytes_in: int = 0
    skipped_hidden: int = 0
    skipped_ext: int = 0
    skipped_too_large: int = 0
    sink_errors: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "files": self.files,
            "records": self.records,
            "bytes_in": self.bytes_in,
            "skipped_hidden": self.skipped_hidden,
            "skipped_ext": self.skipped_ext,
            "skipped_too_large": self.skipped_too_large,
            "sink_errors": self.sink_errors,
        }

# ------------------------
# Pipeline
# ------------------------

def run_pipeline(
    source: Source,
    *,
    sinks: Sequence[Sink],
    policy: Optional[ChunkPolicy] = None,
    context: Optional[RepoContext] = None,
    extractors: Optional[Sequence[Extractor]] = None,
    include_exts: Optional[Sequence[str]] = None,
    exclude_exts: Optional[Sequence[str]] = None,
    skip_hidden: bool = True,
    max_file_bytes: Optional[int] = 200 * 1024 * 1024,
) -> Dict[str, int]:
    stats = PipelineStats()
    include = _normalize_exts(include_exts)
    exclude = _normalize_exts(exclude_exts)

    # Open sinks (best effort)
    open_sinks: List[Sink] = []
    for s in sinks:
        try:
            s.open(context)
            open_sinks.append(s)
        except Exception as e:
            log.warning("Sink %s failed to open: %s", getattr(s, "__class__", type(s)).__name__, e)
            stats.sink_errors += 1

    # Adapter: Extractor -> callable(text, rel_path) expected by convert layer
    fn_extractors: Optional[List[Callable[[str, str], Optional[Iterable[Record]]]]] = None
    if extractors:
        fn_extractors = []
        for ex in extractors:
            def _wrap(text: str, rel_path: str, _ex: Extractor = ex, _ctx: Optional[RepoContext] = context):
                try:
                    res = _ex.extract(text=text, path=rel_path, context=_ctx)
                    if res is None:
                        return None
                    return list(res)
                except Exception as e:
                    log.warning("Extractor %r failed for %s: %s", getattr(_ex, "name", _ex), rel_path, e)
                    return None
            fn_extractors.append(_wrap)

    try:
        for item in source.iter_files():
            rel = item.path.replace("\\", "/")
            size = item.size if item.size is not None else (
                len(item.data) if isinstance(item.data, (bytes, bytearray)) else 0
            )

            if skip_hidden and _is_hidden(rel):
                stats.skipped_hidden += 1
                continue
            if _should_skip_by_ext(rel, include, exclude):
                stats.skipped_ext += 1
                continue
            if max_file_bytes is not None and size and size > max_file_bytes:
                stats.skipped_too_large += 1
                continue

            stats.files += 1
            stats.bytes_in += (size or 0)

            try:
                recs = make_records_from_bytes(
                    item.data, rel,
                    policy=policy,
                    context=context,
                    extractors=fn_extractors
                )
            except Exception as e:
                log.warning("Failed to convert %s: %s", rel, e)
                continue

            if not recs:
                continue

            for r in recs:
                for s in open_sinks:
                    try:
                        s.write(r)
                    except Exception as e:
                        log.warning("Sink %s failed to write record for %s: %s",
                                    getattr(s, "__class__", type(s)).__name__, rel, e)
                        stats.sink_errors += 1
                stats.records += 1
    finally:
        for s in open_sinks:
            try:
                s.close()
            except Exception as e:
                log.warning("Sink %s failed to close: %s", getattr(s, "__class__", type(s)).__name__, e)
                stats.sink_errors += 1

    return stats.as_dict()

__all__ = ['run_pipeline', 'PipelineStats']