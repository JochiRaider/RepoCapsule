# runner.py
# SPDX-License-Identifier: MIT
"""
Entry points for converting repositories to JSONL (and optional prompt text).

This module wires Sources and Sinks into the single-pass pipeline that powers
RepoCapsule. It supports local directories and GitHub URLs and writes output
incrementally so large repositories are handled efficiently.

Public functions
----------------
- convert_repo_to_jsonl(root_dir, jsonl_path, ...)
- convert_repo_to_jsonl_autoname(root_dir, out_dir, ...)
- convert_github_url_to_jsonl(url, jsonl_path, ...)
- convert_github_url_to_both(url, jsonl_path, prompt_txt_path, ...)
- convert_github_url_to_jsonl_autoname(url, out_dir, ...)

Behavior
--------
- Uses streamed GitHub zipballs to avoid high memory use.
- Runs the pipeline once per repository and writes records as they are ready.
- Accepts ChunkPolicy, file-extension filters, and **extractors** (e.g., a
  `KqlFromMarkdownExtractor`) for specialized parsing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable,Optional, Sequence, Dict, Any
import os
import json

from .log import get_logger
from .chunk import ChunkPolicy
from .pipeline import run_pipeline
from .interfaces import RepoContext, FileItem, Extractor
from .fs import iter_repo_files
from .githubio import (
    RepoSpec,
    parse_github_url,
    get_repo_info,
    get_repo_license_spdx,
    download_zipball_to_temp,
    build_output_basename,
    iter_zip_members,
)

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Source adapters
# ---------------------------------------------------------------------------
class _LocalDirSource:
    """Source for a local repo directory using fs.iter_repo_files."""
    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
    def iter_files(self) -> Iterable[FileItem]:
        for path in iter_repo_files(self.root):
            try:
                data = path.read_bytes()
            except Exception:
                continue
            rel = str(path.relative_to(self.root)).replace("\\", "/")
            yield FileItem(path=rel, data=data, size=len(data))

class _GitHubZipSource:
    """Source over a downloaded GitHub zipball."""
    def __init__(self, zip_path: str, *, per_file_cap: int | None = None):
        self.zip_path = zip_path
        self.per_file_cap = per_file_cap
    def iter_files(self) -> Iterable[FileItem]:
        for rel_path, data in iter_zip_members(
            self.zip_path,
            max_bytes_per_file=self.per_file_cap,
        ):
            yield FileItem(path=rel_path, data=data, size=len(data))

# ---------------------------------------------------------------------------
# Sinks
# ---------------------------------------------------------------------------

class _JSONLSink:
    def __init__(self, out_path: str | os.PathLike[str]):
        self._path = str(out_path)
        self._fp = None
    def open(self, context: Optional[RepoContext] = None) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._fp = open(self._path, "w", encoding="utf-8", newline="\n")
    def write(self, record: Dict[str, Any]) -> None:
        self._fp.write(json.dumps(record, ensure_ascii=False))
        self._fp.write("\n")
    def close(self) -> None:
        if self._fp:
            self._fp.close()
            self._fp = None

class _PromptTextSink:
    """Streamed prompt-text writer."""
    def __init__(self, out_path: str | os.PathLike[str]):
        self._path = str(out_path)
        self._fp = None
    def open(self, context: Optional[RepoContext] = None) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._fp = open(self._path, "w", encoding="utf-8", newline="")
    def write(self, record: Dict[str, Any]) -> None:
        meta = record.get("meta", {})
        rel = meta.get("path", "unknown")
        cid = meta.get("chunk_id", "?")
        n = meta.get("n_chunks", "?")
        lang = meta.get("lang", "?")
        self._fp.write(f"### {rel} [{cid}/{n}] (lang={lang})\n\n")
        text = record.get("text", "")
        self._fp.write(text if isinstance(text, str) else str(text))
        if not str(text).endswith("\n"):
            self._fp.write("\n")
        self._fp.write("\n")
    def close(self) -> None:
        if self._fp:
            self._fp.close()
            self._fp = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
 
def _make_ctx_for_local(root_dir: str | Path) -> RepoContext:
    root = Path(root_dir)
    # For local repos we don’t have a URL or SPDX id; keep them None.
    return RepoContext(
        repo_full_name=root.name,
        repo_url=None,
        license_id=None,
    )


def _make_ctx_for_github(spec: RepoSpec) -> RepoContext:
    license_id = None
    try:
        license_id = get_repo_license_spdx(spec)
    except Exception as e:
        log.info("License lookup failed for %s: %s", spec, e)
    return RepoContext(
        repo_full_name=f"{spec.owner}/{spec.repo}",
        repo_url=f"https://github.com/{spec.owner}/{spec.repo}",
        license_id=license_id,
    )

# ---------------------------------------------------------------------------
# Public entry points 
# ---------------------------------------------------------------------------

def convert_repo_to_jsonl(
    root_dir: str | Path,
    jsonl_path: str | Path,
    *,
    policy: Optional[ChunkPolicy] = None,
    include_exts: Optional[Sequence[str]] = None,
    exclude_exts: Optional[Sequence[str]] = None,
    skip_hidden: bool = True,
    max_file_bytes: Optional[int] = None,
    extractors: Optional[Sequence[Extractor]] = None,
) -> str:
    """Convert a local repository directory into JSONL records."""
    src = _LocalDirSource(root_dir)
    ctx = _make_ctx_for_local(root_dir)
    stats = run_pipeline(
        source=src,
        sinks=[_JSONLSink(jsonl_path)],
        policy=policy,
        context=ctx,
        extractors=extractors,
        include_exts=include_exts,
        exclude_exts=exclude_exts,
        skip_hidden=skip_hidden,
        max_file_bytes=max_file_bytes,
    )
    log.info("Local convert complete: %s", stats)
    return str(jsonl_path)


def convert_repo_to_jsonl_autoname(
    root_dir: str | Path,
    out_dir: str | Path,
    *,
    policy: Optional[ChunkPolicy] = None,
    include_exts: Optional[Sequence[str]] = None,
    exclude_exts: Optional[Sequence[str]] = None,
    skip_hidden: bool = True,
    max_file_bytes: Optional[int] = None,
    extractors: Optional[Sequence[Extractor]] = None,
) -> str:
    """Like convert_repo_to_jsonl but picks a filename based on the folder name."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = Path(root_dir).name
    jsonl_path = out_dir / f"{base}.jsonl"
    return convert_repo_to_jsonl(
        root_dir,
        jsonl_path,
        policy=policy,
        include_exts=include_exts,
        exclude_exts=exclude_exts,
        skip_hidden=skip_hidden,
        max_file_bytes=max_file_bytes,
        extractors=extractors,
    )

def convert_github_url_to_jsonl(
    url: str,
    jsonl_path: str | Path,
    *,
    policy: Optional[ChunkPolicy] = None,
    include_exts: Optional[Sequence[str]] = None,
    exclude_exts: Optional[Sequence[str]] = None,
    skip_hidden: bool = True,
    max_file_bytes: Optional[int] = None,
    extractors: Optional[Sequence[Extractor]] = None,
) -> str:
    """Convert a GitHub repo (by URL) to JSONL."""
    spec = parse_github_url(url)
    if not spec:
        raise ValueError(f"Unrecognized GitHub URL: {url}")

    # Resolve repo metadata (best-effort)
    try:
        _ = get_repo_info(spec)
    except Exception as e:
        log.info("GitHub repo info lookup failed for %s: %s", url, e)

    # Download and stream zip
    zip_path = download_zipball_to_temp(spec)
    src = _GitHubZipSource(zip_path, per_file_cap=max_file_bytes)
    ctx = _make_ctx_for_github(spec)

    stats = run_pipeline(
        source=src,
        sinks=[_JSONLSink(jsonl_path)],
        policy=policy,
        context=ctx,
        extractors=extractors,
        include_exts=include_exts,
        exclude_exts=exclude_exts,
        skip_hidden=skip_hidden,
        max_file_bytes=max_file_bytes,
    )
    log.info("GitHub convert complete: %s", stats)
    return str(jsonl_path)


def convert_github_url_to_both(
    url: str,
    jsonl_path: str | Path,
    prompt_txt_path: str | Path,
    *,
    policy: Optional[ChunkPolicy] = None,
    include_exts: Optional[Sequence[str]] = None,
    exclude_exts: Optional[Sequence[str]] = None,
    skip_hidden: bool = True,
    max_file_bytes: Optional[int] = None,
    extractors: Optional[Sequence[Extractor]] = None,
) -> tuple[str, str]:
    """Convert a GitHub repo to both JSONL and a concatenated prompt text file in one pass."""
    spec = parse_github_url(url)
    if not spec:
        raise ValueError(f"Unrecognized GitHub URL: {url}")

    try:
        _ = get_repo_info(spec)
    except Exception as e:
        log.info("GitHub repo info lookup failed for %s: %s", url, e)

    zip_path = download_zipball_to_temp(spec)
    src = _GitHubZipSource(zip_path, per_file_cap=max_file_bytes)
    ctx = _make_ctx_for_github(spec)

    stats = run_pipeline(
        source=src,
        sinks=[_JSONLSink(jsonl_path), _PromptTextSink(prompt_txt_path)],
        policy=policy,
        context=ctx,
        extractors=extractors,
        include_exts=include_exts,
        exclude_exts=exclude_exts,
        skip_hidden=skip_hidden,
        max_file_bytes=max_file_bytes,
    )
    log.info("GitHub convert (both) complete: %s", stats)
    return str(jsonl_path), str(prompt_txt_path)


def convert_github_url_to_jsonl_autoname(
    url: str,
    out_dir: str | Path,
    *,
    policy: Optional[ChunkPolicy] = None,
    include_exts: Optional[Sequence[str]] = None,
    exclude_exts: Optional[Sequence[str]] = None,
    skip_hidden: bool = True,
    max_file_bytes: Optional[int] = None,
    extractors: Optional[Sequence[Extractor]] = None,
) -> str:
    """Like convert_github_url_to_jsonl but auto-generates the output filename."""
    spec = parse_github_url(url)
    if not spec:
        raise ValueError(f"Unrecognized GitHub URL: {url}")

    license_id = None
    try:
        license_id = get_repo_license_spdx(spec)
    except Exception as e:
        log.info("License lookup failed for %s: %s", url, e)

    base = build_output_basename(spec, license_spdx=license_id)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / f"{base}.jsonl"
    return convert_github_url_to_jsonl(
        url,
        jsonl_path,
        policy=policy,
        include_exts=include_exts,
        exclude_exts=exclude_exts,
        skip_hidden=skip_hidden,
        max_file_bytes=max_file_bytes,
        extractors=extractors,
    )


__all__ = [
    "convert_repo_to_jsonl",
    "convert_repo_to_jsonl_autoname",
    "convert_github_url_to_jsonl",
    "convert_github_url_to_both",
    "convert_github_url_to_jsonl_autoname",
]
