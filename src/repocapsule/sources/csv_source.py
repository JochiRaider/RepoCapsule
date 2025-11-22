# csv_source.py
# SPDX-License-Identifier: MIT

from __future__ import annotations

import csv
import gzip
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence, Dict, Any

from ..core.interfaces import Source, FileItem, RepoContext
from ..core.log import get_logger

__all__ = ["CSVTextSource"]

log = get_logger(__name__)


@dataclass
class CSVTextSource(Source):
    paths: Sequence[Path]
    context: Optional[RepoContext] = None
    text_column: str = "text"
    delimiter: Optional[str] = None
    encoding: str = "utf-8"
    has_header: bool = True
    text_column_index: int = 0

    def iter_files(self) -> Iterable[FileItem]:
        for path in self.paths:
            is_gz = "".join(path.suffixes[-2:]).lower() in {".csv.gz", ".tsv.gz"}
            opener = _open_csv_gz if is_gz else _open_csv
            try:
                with opener(path, encoding=self.encoding) as fp:
                    dialect_delim = self._resolve_delimiter(path)
                    if self.has_header:
                        reader = csv.DictReader(fp, delimiter=dialect_delim)
                        for lineno, row in enumerate(reader, start=2):
                            yield from self._row_to_fileitems(row=row, path=path, lineno=lineno)
                    else:
                        reader = csv.reader(fp, delimiter=dialect_delim)
                        for lineno, row in enumerate(reader, start=1):
                            yield from self._row_to_fileitems_no_header(row=row, path=path, lineno=lineno)
            except FileNotFoundError:
                log.warning("CSV file not found: %s", path)
            except Exception as exc:
                log.warning("Failed to read CSV file %s: %s", path, exc)

    def _resolve_delimiter(self, path: Path) -> str:
        if self.delimiter is not None:
            return self.delimiter
        suffixes = "".join(path.suffixes).lower()
        if ".tsv" in suffixes:
            return "\t"
        return ","

    def _row_to_fileitems(self, *, row: Dict[str, Any], path: Path, lineno: int) -> Iterable[FileItem]:
        text = _extract_text_from_row_with_header(row, self.text_column)
        if text is None:
            return
        rel = _derive_rel_path(path, lineno, row)
        data = text.encode("utf-8")
        yield FileItem(path=rel, data=data, size=len(data))

    def _row_to_fileitems_no_header(self, *, row: Sequence[str], path: Path, lineno: int) -> Iterable[FileItem]:
        idx = self.text_column_index
        if not (0 <= idx < len(row)):
            return
        text = (row[idx] or "").strip()
        if not text:
            return
        rel = f"{path.name}:#{lineno}"
        data = text.encode("utf-8")
        yield FileItem(path=rel, data=data, size=len(data))


def _extract_text_from_row_with_header(row: Dict[str, Any], text_column: str) -> Optional[str]:
    val = row.get(text_column)
    if isinstance(val, str):
        text = val.strip()
        return text or None
    return None


def _derive_rel_path(path: Path, lineno: int, row: Dict[str, Any]) -> str:
    for key in ("path", "filepath", "file_path", "id"):
        val = row.get(key)
        if isinstance(val, str) and val:
            return val
    return f"{path.name}:#{lineno}"


def _open_csv(path: Path, *, encoding: str):
    # newline="" ensures correct handling of embedded newlines/quoting for csv module.
    return open(path, "r", encoding=encoding, newline="")


def _open_csv_gz(path: Path, *, encoding: str):
    return gzip.open(path, "rt", encoding=encoding)
