"""repocapsule.chunk

Chunking utilities for repository ingestion.

Goals
-----
- **Stdlib-only** and fast.
- Preserve structure: keep Markdown code fences, tables, and lists intact.
- Two primary strategies:
  * `chunk_by_paragraphs` for prose/Markdown/docs (structure-aware).
  * `chunk_by_lines` for code (line-aware, deterministic).
- Provide a light-weight **token estimate** so callers can target sizes
  without requiring any external tokenizer.

Rationale
---------
* Fixed-size chunking by characters is simple but often harms retrieval
  by slicing through sentences or fenced code blocks. Keeping Markdown
  structure and code fences together tends to improve recall.  
* We approximate token counts using a symbols-aware heuristic; real
  tokenizers (e.g., BPE/WordPiece/SentencePiece) can vary by model,
  but chars-per-token ≈ **4.0 for prose** and **~3.2–3.6 for code** is a
  reasonable rule-of-thumb. See the cited docs for details.

Public API
----------
- `approx_token_count(text: str, kind: str = "doc") -> int`
- `chunk_by_paragraphs(text: str, target_tokens=800, overlap_tokens=100, min_tokens=200) -> list[Chunk]`
- `chunk_by_lines(text: str, target_tokens=800, overlap_lines=0) -> list[Chunk]`
- `chunk_text(text: str, policy: ChunkPolicy) -> list[Chunk]`

"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional
import math
import re

__all__ = [
    "Chunk",
    "ChunkPolicy",
    "approx_token_count",
    "chunk_by_paragraphs",
    "chunk_by_lines",
    "chunk_text",
]

# -------------------
# Small data classes
# -------------------

@dataclass
class Chunk:
    text: str
    start: int  # start char index within original text
    end: int    # end char index (exclusive)
    est_tokens: int


@dataclass
class ChunkPolicy:
    mode: str = "auto"      # one of {"auto", "doc", "code"}
    target_tokens: int = 800
    overlap_tokens: int = 100  # used by paragraph chunking
    min_tokens: int = 200


# ----------------------
# Token estimation utils
# ----------------------

_PUNCT = set("()[]{}<>=:+-*/%,.;$#@\\|`~^")
_WORDISH = re.compile(r"[A-Za-z0-9_]")


def _char_token_ratio(kind: str, text: str) -> float:
    """Return an estimated *chars per token* ratio.

    Heuristic: code has more symbols and shorter identifiers, so fewer
    chars per token. Clamp into a reasonable band.
    """
    n = len(text)
    if n == 0:
        return 4.0
    sym = sum(1 for ch in text if ch in _PUNCT)
    digits = sum(1 for ch in text if ch.isdigit())
    spaces = text.count(" ") + text.count("\n") + text.count("\t")
    sym_density = (sym + digits) / max(1, n)
    base = 3.2 if kind == "code" else 4.0
    # More symbols → lower chars/token; more spaces → slightly higher
    ratio = base - 0.8 * sym_density + 0.2 * (spaces / max(1, n))
    return max(2.8, min(4.6, ratio))


def approx_token_count(text: str, kind: str = "doc") -> int:
    """Estimate tokens from character length using a symbols-aware ratio.

    This is intentionally approximate. For exact counts, call-sites can
    plug in a model tokenizer later.
    """
    if not text:
        return 0
    ratio = _char_token_ratio(kind, text)
    return int(math.ceil(len(text) / ratio))


# -----------------------
# Prose / Markdown parser
# -----------------------

_FENCE_OPEN = re.compile(r"^\s*([`~]{3,})([A-Za-z0-9_+-]*)\s*$")
_FENCE_CLOSE = re.compile(r"^\s*([`~]{3,})\s*$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_LIST = re.compile(r"^\s{0,3}(?:[-*+]|\d+\.)\s+\S")
_TABLE_ROW = re.compile(r"\|.*\|")
_HR = re.compile(r"^\s{0,3}(?:-\s?){3,}$|^\s{0,3}(?:\*\s?){3,}$|^\s{0,3}(?:_\s?){3,}$")
_INDENTED_CODE = re.compile(r"^(?:\t| {4,})\S")


def _split_markdown_blocks(text: str) -> list[tuple[str, int, int]]:
    """Split Markdown-ish text into structural blocks.

    Returns a list of (block_text, start, end) where start/end are char
    offsets in the original text. Keeps code fences and tables intact.
    """
    blocks: list[tuple[str, int, int]] = []
    i = 0
    lines = text.splitlines(keepends=True)
    pos = 0
    in_fence = False
    fence_char = ""
    fence_len = 0
    buf: list[str] = []
    buf_start = 0

    def flush():
        nonlocal buf, buf_start
        if buf:
            bt = "".join(buf)
            blocks.append((bt, buf_start, buf_start + len(bt)))
            buf = []

    while i < len(lines):
        line = lines[i]
        # Handle fenced code blocks
        if not in_fence:
            m = _FENCE_OPEN.match(line)
            if m:
                flush()
                in_fence = True
                fence_char = m.group(1)[0]
                fence_len = len(m.group(1))
                fence_start = pos
                fence_buf = [line]
                i += 1
                pos += len(line)
                # consume until matching closing fence of >= same length and same char
                while i < len(lines):
                    fence_buf.append(lines[i])
                    m2 = _FENCE_CLOSE.match(lines[i])
                    pos += len(lines[i])
                    i += 1
                    if m2 and m2.group(1)[0] == fence_char and len(m2.group(1)) >= fence_len:
                        break
                blocks.append(("".join(fence_buf), fence_start, pos))
                in_fence = False
                continue

        # Table block (consecutive rows with '|')
        if _TABLE_ROW.search(line):
            flush()
            tstart = pos
            tbuf = [line]
            i += 1
            pos += len(line)
            while i < len(lines) and _TABLE_ROW.search(lines[i]):
                tbuf.append(lines[i])
                pos += len(lines[i])
                i += 1
            blocks.append(("".join(tbuf), tstart, pos))
            continue

        # Indented code
        if _INDENTED_CODE.match(line):
            flush()
            cstart = pos
            cbuf = [line]
            i += 1
            pos += len(line)
            while i < len(lines) and (lines[i].strip() == "" or _INDENTED_CODE.match(lines[i])):
                cbuf.append(lines[i])
                pos += len(lines[i])
                i += 1
            blocks.append(("".join(cbuf), cstart, pos))
            continue

        # Headings, hr, lists each form their own block; otherwise paragraphs
        if _HEADING.match(line) or _HR.match(line) or _LIST.match(line):
            flush()
            blocks.append((line, pos, pos + len(line)))
            i += 1
            pos += len(line)
            continue

        # Paragraphs: accumulate until blank line
        if not buf:
            buf_start = pos
        buf.append(line)
        i += 1
        pos += len(line)
        if line.strip() == "":
            flush()

    flush()
    return blocks


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=(?:[\"'\)]*?[A-Z0-9]))")


def _split_paragraph_into_sentences(p: str) -> list[str]:
    p = p.strip()
    if not p:
        return []
    parts = _SENT_SPLIT.split(p)
    return [s.strip() for s in parts if s.strip()]


# ---------------------
# Chunking: paragraphs
# ---------------------

def chunk_by_paragraphs(
    text: str,
    *,
    target_tokens: int = 800,
    overlap_tokens: int = 100,
    min_tokens: int = 200,
) -> List[Chunk]:
    """Greedy structural packer for prose/Markdown.

    - Keeps fenced code blocks, tables, lists intact.
    - Packs blocks into chunks up to ~`target_tokens`.
    - If a single block is too large, split by sentences (paragraphs)
      or by lines (for fenced/indented code), preserving local context.
    - Applies character-based overlap (~overlap_tokens) between chunks.
    """
    blocks = _split_markdown_blocks(text)
    chunks: list[Chunk] = []
    cur_text: list[str] = []
    cur_start = 0
    cur_len = 0

    def cur_tokens(kind: str = "doc") -> int:
        return approx_token_count("".join(cur_text), kind)

    def flush_chunk(kind: str = "doc") -> None:
        nonlocal cur_text, cur_start, cur_len
        if not cur_text:
            return
        chunk_text = "".join(cur_text)
        est = approx_token_count(chunk_text, kind)
        chunks.append(Chunk(chunk_text, cur_start, cur_start + len(chunk_text), est))
        # prepare overlap: keep tail chars approximating overlap_tokens
        if overlap_tokens > 0:
            ratio = _char_token_ratio("doc", chunk_text)
            tail_chars = int(overlap_tokens * ratio)
            overlap_tail = chunk_text[-tail_chars:]
            cur_text = [overlap_tail]
            cur_start = chunks[-1].end - len(overlap_tail)
            cur_len = len(overlap_tail)
        else:
            cur_text = []
            cur_len = 0

    for btxt, bstart, bend in blocks:
        b_tokens = approx_token_count(btxt, "doc")
        # If empty block (e.g., stray blank lines), skip
        if not btxt.strip():
            continue
        # If the block alone would overshoot badly, split it.
        if b_tokens > max(target_tokens, 2 * target_tokens):
            # Attempt sentence-level split; if it's mostly code-like, split by lines.
            if "```" in btxt or btxt.startswith("    "):
                # Split code block by lines but rewrap fence if needed
                lines = btxt.splitlines(keepends=True)
                # Detect fence prefix/suffix
                fence_prefix = fence_suffix = ""
                if lines and lines[0].lstrip().startswith("```") or lines[0].lstrip().startswith("~~~"):
                    fence_prefix = lines[0]
                    # drop first
                    lines = lines[1:]
                    if lines and (lines[-1].strip().startswith("```") or lines[-1].strip().startswith("~~~")):
                        fence_suffix = lines[-1]
                        lines = lines[:-1]
                pack: list[str] = []
                pack_chars = 0
                for ln in lines:
                    pack.append(ln)
                    pack_chars += len(ln)
                    est = approx_token_count("".join(pack), "code")
                    if est >= target_tokens:
                        sub = "".join(pack)
                        if fence_prefix:
                            sub = fence_prefix + sub + (fence_suffix or fence_prefix.replace("\n", ""))
                        cur_text.append(sub)
                        flush_chunk("code")
                        pack, pack_chars = [], 0
                if pack:
                    sub = "".join(pack)
                    if fence_prefix:
                        sub = fence_prefix + sub + (fence_suffix or fence_prefix.replace("\n", ""))
                    cur_text.append(sub)
                    # don't flush yet; allow following blocks to pack in
                continue
            else:
                sentences = _split_paragraph_into_sentences(btxt)
                if not sentences:
                    # fallback: hard split by chars
                    step_chars = int(target_tokens * _char_token_ratio("doc", btxt))
                    for i in range(0, len(btxt), step_chars):
                        cur_text.append(btxt[i : i + step_chars])
                        flush_chunk("doc")
                    continue
                pack: list[str] = []
                for s in sentences:
                    candidate = (" ".join(pack + [s])).strip()
                    if approx_token_count(candidate, "doc") > target_tokens and pack:
                        cur_text.append(" ".join(pack).strip() + "\n")
                        flush_chunk("doc")
                        pack = [s]
                    else:
                        pack.append(s)
                if pack:
                    cur_text.append(" ".join(pack).strip() + "\n")
                continue

        # Otherwise, try to add block to current chunk; flush if it would exceed.
        candidate = "".join(cur_text + [btxt])
        if approx_token_count(candidate, "doc") > target_tokens and approx_token_count("".join(cur_text), "doc") >= min_tokens:
            flush_chunk("doc")
            cur_text = [btxt]
            cur_start = bstart
            cur_len = len(btxt)
        else:
            if not cur_text:
                cur_start = bstart
            cur_text.append(btxt)
            cur_len += len(btxt)

    # Final flush
    flush_chunk("doc")
    # If nothing flushed (tiny file), create a single chunk
    if not chunks and text:
        chunks.append(Chunk(text, 0, len(text), approx_token_count(text, "doc")))
    return chunks


# -----------------
# Chunking: by lines
# -----------------

def chunk_by_lines(
    text: str,
    *,
    target_tokens: int = 800,
    overlap_lines: int = 0,
) -> List[Chunk]:
    """Chunk code deterministically by lines.

    - Accumulates lines until ~`target_tokens` using a code token heuristic.
    - Optional line overlap to carry a small tail of context forward.
    """
    lines = text.splitlines(keepends=True)
    chunks: list[Chunk] = []
    start = 0
    buf: list[str] = []
    buf_start = 0

    def flush() -> None:
        nonlocal buf, buf_start, start
        if not buf:
            return
        s = "".join(buf)
        est = approx_token_count(s, "code")
        chunks.append(Chunk(s, buf_start, buf_start + len(s), est))
        if overlap_lines > 0:
            tail = lines[max(0, start - overlap_lines): start]
            tail_text = "".join(tail)
            buf = [tail_text]
            buf_start = chunks[-1].end - len(tail_text)
        else:
            buf = []

    i = 0
    while i < len(lines):
        if not buf:
            buf_start = sum(len(x) for x in lines[:i])
        buf.append(lines[i])
        start = i + 1
        if approx_token_count("".join(buf), "code") >= target_tokens:
            flush()
        i += 1

    flush()
    if not chunks and text:
        chunks.append(Chunk(text, 0, len(text), approx_token_count(text, "code")))
    return chunks


# -------------------------
# High-level policy wrapper
# -------------------------

def _looks_like_code(text: str) -> bool:
    # Quick heuristic: lots of braces/semicolons or many short lines
    if not text:
        return False
    punct = sum(1 for ch in text if ch in _PUNCT)
    short_lines = sum(1 for ln in text.splitlines() if len(ln) <= 60)
    total_lines = max(1, len(text.splitlines()))
    return punct / max(1, len(text)) > 0.06 or (short_lines / total_lines > 0.7 and total_lines > 6)


def chunk_text(text: str, policy: ChunkPolicy) -> List[Chunk]:
    mode = policy.mode
    if mode == "auto":
        mode = "code" if _looks_like_code(text) else "doc"
    if mode == "code":
        return chunk_by_lines(text, target_tokens=policy.target_tokens)
    else:
        return chunk_by_paragraphs(
            text,
            target_tokens=policy.target_tokens,
            overlap_tokens=policy.overlap_tokens,
            min_tokens=policy.min_tokens,
        )
