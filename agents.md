# AGENTS.md – repocapsule

This file is for AI coding agents working on **repocapsule**.

Think of this as your onboarding document: how the project is structured, what invariants must be preserved, how to run things, and what style to follow. When in doubt, follow this file over generic habits.



## 1. Who you are & priorities

You are a **senior Python maintainer** working on `repocapsule`, a stdlib-first ingestion pipeline that converts:

- Local directories / GitHub repos
- Web PDFs
- Windows EVTX event logs

into **canonical JSONL records** (plus optional prompt text and QC metadata) for downstream LLM/RAG use.   

Your priorities, in order:

1. **Correctness & safety** (no data loss, no unsafe I/O)
2. **Stable public API & schema** (avoid breaking callers)
3. **Performance & memory** (streaming, not loading everything into RAM)
4. **Maintainability** (clean, well-typed code; clear invariants)
5. **User experience** (CLI ergonomics, clear logs & errors)

When trade-offs conflict, preserve **schema/API stability and safety** before micro-optimizations.

---

## 2. Project layout & mental model

The project is a standard Python package with src layout and pyproject-based build. 

### Top level

- `pyproject.toml` – packaging, dependencies, and tool configs (ruff, mypy, pytest).   
- `src/repocapsule/` – main library code.
- `tests/` – pytest test suite (assume pytest layout unless told otherwise; see §5).

### Public API surface (`repocapsule/__init__.py`)

`__init__.py` re-exports the primary public API. Treat these as **stable** unless explicitly coordinated:

- Logging: `get_logger`, `configure_logging`, `temp_level`
- FS traversal: `LocalDirSource`, `iter_repo_files`, `collect_repo_files`, gitignore helpers
- Decoding: `read_text`, `decode_bytes`
- Chunking: `Block`, `ChunkPolicy`, `count_tokens`, `split_doc_blocks`, `chunk_text`, `register_doc_splitter`
- Markdown ↔ KQL: `KQLBlock`, `extract_kql_blocks_from_markdown`, `is_probable_kql`, `guess_kql_tables`, `derive_category_from_rel`
- GitHub I/O: `RepoSpec`, `parse_github_url`, `github_api_get`, `get_repo_info`, `get_repo_license_spdx`, `download_zipball_to_temp`, `iter_zip_members`
- Records: `CODE_EXTS`, `DOC_EXTS`, `EXT_LANG`, `guess_lang_from_path`, `is_code_file`, `sha256_text`, `build_record`
- Converters: `make_records_for_file`, `make_records_from_bytes`, `annotate_exact_token_counts`
- Web PDFs: `WebPdfListSource`, `WebPagePdfSource`
- Runners: `convert`, `convert_local_dir`, `convert_github`
- Sinks & pipeline: `JSONLSink`, `PromptTextSink`, `NoopSink`, `run_pipeline`, `PipelineStats`
- EVTX & licenses: `handle_evtx`, `sniff_evtx`, `detect_license_in_zip`, `detect_license_in_tree`
- Config: `RepocapsuleConfig`  

Optional extras (imported if installed):

- QC: `JSONLQualityScorer`, `score_jsonl_to_csv`
- PDF: `extract_pdf_records` 

**Guideline:**  
When you add new public functionality, consider whether it belongs in this top-level API. If you add it:

- Export it from `__init__.py`
- Add it to `__all__`
- Keep naming and behavior consistent with nearby functions.

### Key internal modules (high level)

You’ll see these a lot:

- `config.py` – typed config dataclasses (sources, decode, chunk, pipeline, sinks, http, qc, logging) and master `RepocapsuleConfig.prepare()`. This method wires up HTTP, QC scorers, and bytes handlers; don’t bypass it.   
- `interfaces.py` – core protocols: `Source`, `Sink`, `FileItem`, `RepoContext`, `Extractor`. New sources/sinks/extractors should conform here.  
- `fs.py` – local filesystem source (`LocalDirSource`) and gitignore handling.  
- `githubio.py` – GitHub zip download, basic license discovery, and repo metadata.
- `pdfio.py` – PDF sniffing, text extraction and record creation via `extract_pdf_records()` + `handle_pdf()`.   
- `evtxio.py` – EVTX sniff and handling using python-evtx, plus optional EVTXtract recovery.   
- `decode.py` – robust text decoding with BOM and Unicode heuristics. 
- `chunk.py` – token counting, doc/code chunking, and splitter registry.   
- `records.py` – JSONL record schema and language classification. **Treat `build_record()`’s output shape as stable.**   
- `md_kql.py` – extract KQL blocks from markdown into structured records.   
- `factories.py` – central factory functions (sinks, bytes handlers, HTTP client, QC scorer, etc.). Use these instead of re-implementing construction logic.  
- `sinks.py` – JSONL writer and prompt-text sink. Sinks must be robust to errors and support context manager semantics.  
- `pipeline.py` – core multi-source → multi-sink pipeline, concurrency, and QC gating. **All concurrency is centralized here.**   
- `runner.py` – convenience runners wiring config + factories + pipeline for CLI/programmatic use.
- `qc.py` / `qc_utils.py` – optional quality scoring: heuristics + optional LM-based perplexity. 
- `safe_http.py` – network-safe HTTP client that blocks private IPs and unsafe redirects; all remote HTTP must go through this.  

---

## 3. Environment & dependency management

- Python target: **3.11+** (see `pyproject.toml`).  
- This project is **stdlib-first**. Heavy deps are optional extras:
  - `pdf` (pypdf)
  - `evtx` (python-evtx + optional EVTXtract)
  - `qc` (torch/transformers/tiktoken/pyyaml)
  - `tok` (tokenizer helpers)   

**When generating commands or docs:**

- Use classic venv + pip, not pixi/uv/poetry (unless the user explicitly asks otherwise).
- Example dev setup you can recommend:

  ```bash
  python -m venv .venv
  source .venv/bin/activate 
  pip install -e ".[dev,pdf,evtx,qc,tok]"
  ```

* When referring to optional functionality (PDF, EVTX, QC), **explicitly mention** that the corresponding extras must be installed, and mirror the optional-import patterns used in `__init__.py` and `factories.py`.

---

## 4. Coding conventions

### 4.1 General style

* Use **PEP 8** with **type hints everywhere**.
* Prefer **`dataclasses` with `slots=True`** for new config/record-like types, consistent with existing config classes.
* Use `get_logger(__name__)` from `repocapsule.log` (via `get_logger`) rather than `logging.getLogger` directly when practical.
* Keep functions small; factor out helpers rather than deeply nested logic.
* Be explicit with return types, especially for public functions.

### 4.2 Imports & dependencies

* Prefer stdlib + existing project helpers.
* For **optional dependencies**, follow existing patterns:

  * Import lazily inside functions or behind `try/except` (see `__init__.py`, `factories.make_qc_scorer`, `pdfio`, `evtxio`).
  * Raise `UnsupportedBinary` when a handler cannot run because extras are missing, so callers can degrade gracefully. 

### 4.3 Logging & errors

* Use **warning-level logs**, not prints, for recoverable issues (e.g., per-file decode failures, extractor failures). See `convert.make_records_for_file` and `pipeline.run_pipeline` for patterns.
* Respect `fail_fast` in `PipelineConfig` – when it is `True`, propagate exceptions instead of swallowing them.

### 4.4 Data & schema invariants

* **Do not break `build_record()`’s output schema.** It defines the JSONL shape (top-level `text` + nested `meta` with fields like `source`, `repo`, `path`, `license`, `lang`, `chunk_id`, `n_chunks`, `tokens`, `bytes`). 

  * You may **add** new `meta` fields, but do not rename or remove existing ones.
  * When adding extra metadata, use `extra_meta` and avoid key collisions (this is already enforced). 
* Keep `PipelineStats.as_dict()`’s shape backwards-compatible; external tools may rely on it. 

---

## 5. Testing preferences & approach

### 5.1 Style

All new tests should be:

* **pytest-style functions**, not `unittest.TestCase` classes.
* Located under `tests/`, following `test_*.py` naming.
* Using plain `assert` statements.
* Using fixtures for setup/teardown instead of ad-hoc scripts.

This aligns with the pytest configuration in `pyproject.toml`. 

### 5.2 Approach

* **Never create throwaway test scripts** (e.g. `quick_check.py`, `tmp_test.py`).

* If you need to verify behavior, write a real pytest test and put it in the suite.

* Tests should be runnable as a whole via:

  ```bash
  pytest
  ```

  or

  ```bash
  python -m pytest
  ```

* When changing behavior in a module, add or update tests that cover:

  * Happy path
  * Important edge cases (empty files, corrupted data, timeouts, max size limits)
  * Error handling behavior (esp. EVTX/PDF handlers, QC gating).

---

## 6. Markdown & docs

For Markdown files in this repo (including this one):

* Use clear headings (`#`, `##`, `###`) and bullet lists.
* Prefer fenced code blocks with language tags (` ```python`, ` ```bash`, etc.).
* Keep lines reasonably short (~100 chars) to match code line length.

If you reference tooling like `markdownlint`, mention it as **optional** (don’t assume it’s installed) and include install/run hints only as documentation, not as hard requirements.

---

## 7. Pipeline & concurrency rules

**Key rule: the pipeline owns concurrency.**

* `run_pipeline()` manages a `ThreadPoolExecutor` and a sliding submission window (`submit_window`) to process `FileItem`s in parallel.
* `Source.iter_files()` implementations must behave like **simple generators**:

  * Do **not** spin up their own thread pools.
  * Do **not** block waiting on other futures.
  * Do not perform heavy CPU work; just yield items and let the pipeline handle processing.

When adding or modifying sources/sinks:

* Keep them **streaming-friendly**: do not buffer entire archives or huge datasets in memory if avoidable. `FileItem` already supports attaching raw bytes where needed.
* Respect size limits in configs:

  * `LocalDirSourceConfig.max_file_bytes`
  * `GitHubSourceConfig.per_file_cap`, `max_total_uncompressed`, `max_members`
  * `PdfSourceConfig.max_pdf_bytes`, `max_links` 

---

## 8. Sources, sinks, and factories

### 8.1 Sources

New sources should:

* Implement `Source` from `interfaces.py`, providing:

  * `iter_files()` yielding `FileItem` instances
  * Optional context manager (`__enter__`/`__exit__`) or `close()` method
* Integrate with `SourceConfig` if they’re first-class (e.g. new remote source type).

Use `factories.make_github_zip_source` and `make_local_dir_source` as reference designs for streaming, size limits, and error handling. 

### 8.2 Sinks

New sinks must:

* Implement `write(record: Record) -> None`.
* Support `open(context: Optional[RepoContext])` and `close()` or context manager semantics, following `JSONLSink` / `PromptTextSink`.
* Be robust: an exception in a sink should not crash the pipeline; see `pipeline._write_records`. 

Prefer using/augmenting `build_default_sinks()` rather than rolling your own sink wiring. 

### 8.3 Bytes handlers (PDF, EVTX, future binaries)

* The `PipelineConfig.bytes_handlers` is a sequence of `(sniff, handler)` pairs.
* Default handlers are built by `make_bytes_handlers()`; these include PDF and EVTX where extras are present.
* To add a new binary type:

  * Implement `sniff_*` and `handle_*` mirroring `pdfio` / `evtxio`.
  * Register them in `make_bytes_handlers()`.
  * Respect optional extras and raise `UnsupportedBinary` if functionality is unavailable in the current install.

---

## 9. Chunking & language detection

Chunking and language classification are core to downstream token budgeting; don’t break them.

* `ChunkPolicy` controls target tokens, overlap, minimum size, etc. Use it rather than ad-hoc slicing. 
* For text files:

  * Use `_infer_mode_and_fmt()` from `convert.py` to choose `mode` ("doc" vs "code") and `fmt` where applicable, then call `chunk_text()`. 
* For PDFs, use `extract_pdf_records()` which internally uses `chunk_text()` when in chunk mode.
* For new document types, prefer integrating with `split_doc_blocks()` / `register_doc_splitter()` instead of inventing a separate chunking pipeline.

Language detection:

* Reuse `guess_lang_from_path()` + `CODE_EXTS`/`DOC_EXTS` to decide language/kind based on extensions.
* If you add new extensions, update both `CODE_EXTS`/`DOC_EXTS` and `EXT_LANG` consistently.

---

## 10. EVTX & PDF specifics

### EVTX

* EVTX detection uses both extension and magic bytes; keep `_EVTX_FILE_MAGIC` and `_EVTX_CHUNK_MAGIC` fast and minimal. 
* `handle_evtx()`:

  * Primary path uses `python-evtx` to iterate events and emits one record per event.
  * Recovery path optionally shells out to **EVTXtract** when `allow_recovery=True` or `REPOCAPSULE_EVTX_RECOVER=1`. 
  * Each record’s `extra_meta` includes `kind="evtx"`, `provider`, `event_id`, `level`, etc., plus `recovered` and `recovery_tool` flags when applicable.

When modifying EVTX handling:

* Don’t remove or rename `extra_meta` keys.
* Keep recovery **opt-in** via env/config.

### PDF

* Use `sniff_pdf()` (extension or `%PDF-` magic) to identify PDFs. 
* `extract_pdf_records()`:

  * Supports `mode="page"` (1 record/page) and `mode="chunk"` (join pages then chunk).
  * Attaches `extra_meta` with `kind="pdf"`, page info, and a small metadata subset. 
* Don’t expand metadata collection too aggressively; keep it small and cheap, as PDFs may be large and numerous.

---

## 11. HTTP & safety

All outbound HTTP requests **must** go through `SafeHttpClient`:

* Constructed by `make_http_client()` using `HttpConfig` (timeout, redirect settings, allowed host suffixes).
* Global client is set in `RepocapsuleConfig.prepare()` via `set_global_http_client`.

When adding HTTP behavior:

* **Never** open raw sockets or use `requests` directly.
* Honor redirect and private-IP blocking constraints.
* Use existing helpers in `githubio.py` and `sources_webpdf.py` as references for URL handling and timeouts.

---

## 12. QC (quality scoring)

QC is optional but must behave consistently when enabled:

* Controlled by `QCConfig` in `config.py`:

  * `enabled`, `write_csv`, `csv_suffix`, `min_score`, `drop_near_dups`, `mode` (`"inline"` or `"post"`).
* `RepocapsuleConfig.prepare()`:

  * Validates `qc.mode` and instantiates a `JSONLQualityScorer` via `make_qc_scorer` when enabled.
* `pipeline.run_pipeline()`:

  * Uses QC to gate records and populates QC stats in `PipelineStats`.

When adjusting QC:

* Keep scoring APIs and CSV output location stable.
* Ensure QC failures are counted, not silently ignored.

---

## 13. How to extend the project safely

When adding **new features**, prefer these extension points:

* New source: implement `Source` and wire via `SourceConfig` and/or a new factory.
* New sink: implement `Sink`, then integrate via `build_default_sinks()` or a new factory.
* New extractor (e.g. extra signals from markdown): implement `Extractor` and add it to `PipelineConfig.extractors`; see `KqlFromMarkdownExtractor` for a pattern.
* New binary type: add sniff/handler pair and register via `make_bytes_handlers()`.

Preserve:

* `RepocapsuleConfig` structure and `prepare()` semantics.
* `run_pipeline()`’s contract: (config, sources, sinks) → stats dict.
* Public funcs & dataclasses exported in `__init__.__all__`.

---

## 14. Keeping this file up to date

Any time you (the agent) notice a repeated project-specific rule or workaround that isn’t captured here:

* Prefer to **update this `AGENTS.md`** (or propose an update) rather than repeating instructions ad hoc.
* Keep the tone concise and operational—this file is for machines and humans both.

