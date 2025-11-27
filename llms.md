# RepoCapsule – LLM Guide

This file is meant to give LLMs a compact map of the project so they can focus on the right modules and avoid touching core wiring by accident.

---

## 1. High-level overview

RepoCapsule is a **library-first, configuration-driven ingestion pipeline** that turns repositories and related artifacts into normalized JSONL / Parquet datasets suitable for LLM fine-tuning and analysis.

At a high level:

* You describe a run with `RepocapsuleConfig` (Python or TOML).
* The **builder** turns that config into a `PipelinePlan` and `PipelineRuntime`.
* The **pipeline engine** coordinates sources → decode → chunk → record construction → sinks.
* Optional subsystems (QC, dataset cards, CLI runners, plugins) sit **around** the core rather than reimplementing it.

---

## 2. Core vs non-core

For the purposes of this guide:

* **Core modules** live under `src/repocapsule/core/`.

  * These define configuration models, pipeline planning/runtime, registries, QC, dataset cards, HTTP safety, etc.
  * Other code should generally *use* these rather than duplicating their responsibilities.
* **Non-core**:

  * CLI (`src/repocapsule/cli/`)
  * Top-level package surface (`src/repocapsule/__init__.py`)
  * Source/sink implementations, tests, and manual scripts.

---

## 3. Module map

### 3.1 Core package – `src/repocapsule/core/`

These are treated as the “core” of the system.

* `__init__.py`
  Core package initializer and exports for shared constants/helpers.

* `config.py`
  Configuration models and helpers for defining `RepocapsuleConfig` and related config sections (sources, sinks, QC, chunking, etc.).

* `records.py`
  Construction and normalization of output record dictionaries, including run headers and consistent metadata fields.

* `naming.py`
  Utilities for building safe, normalized output filenames and extensions based on config and repo context.

* `licenses.py`
  Helpers for detecting and normalizing license information from repositories and archives.

* `decode.py`
  Decoding bytes into normalized text using encoding detection and simple heuristics, plus helpers for text normalization.

* `chunk.py`
  Token-aware document/code chunking utilities and the `ChunkPolicy` machinery; yields chunk dicts for downstream processing.

* `convert.py`
  High-level helpers that take file inputs (paths/bytes), run decode + chunking, and produce extractor/record dictionaries.

* `factories.py`
  Factory functions for constructing shared clients and runtime objects (HTTP clients, sources, sinks, repo contexts, output paths). Also defines exceptions like `UnsupportedBinary`.

* `log.py`
  Logging configuration helpers, package logger setup, and temporary level context manager.

* `registries.py`
  Registries for sources, sinks, bytes handlers, and quality scorers; central place to register built-ins and plugins.

* `plugins.py`
  Plugin discovery and registration helpers (entry-point based), wiring external sources/sinks/scorers into the registries.

* `interfaces.py`
  Protocols/typed interfaces shared across the system (sources, sinks, lifecycle hooks, scorers, etc.), plus core type aliases.

* `concurrency.py`
  Abstractions over thread/process executors with bounded submission windows, plus helpers to derive executor settings from `RepocapsuleConfig`.

* `builder.py`
  Orchestrates config → `PipelinePlan`/`PipelineRuntime` construction: builds sources, sinks, bytes handlers, HTTP client, lifecycle hooks, and QC wiring from a `RepocapsuleConfig`.

* `hooks.py`
  Built-in pipeline lifecycle hooks for run summaries/finalizers used by the builder to assemble runtime hooks.

* `pipeline.py`
  Pipeline engine that runs the ingestion loop: coordinates sources, decoding/chunking/record creation, sinks, hooks, and statistics.

* `qc_utils.py`
  Low-level quality-control utilities (similarity hashing, duplicate detection, basic QC heuristics and summaries).

* `qc_controller.py`
  Inline QC controller and related helpers for advisory/inline gating during the main pipeline run (e.g., `InlineQCHook`), using record-level scorers (`score_record`) only.

* `qc_post.py`
  Post-hoc QC lifecycle hook and reusable JSONL driver (`run_qc_over_jsonl`, `iter/collect_qc_rows_from_jsonl`) for scoring or CSV export after the main run completes.

* `dataset_card.py`
  Builders for dataset card fragments and rendering/assembling Hugging Face–style dataset cards from run statistics and metadata.

* `safe_http.py`
  Stdlib-only HTTP client with IP/redirect safeguards and a module-level global client helper for simple use-cases.

* `extras/__init__.py`
  Namespace for optional or higher-level “extras” built on top of core primitives.
  *Summary pending for individual extras modules.*

* `extras/md_kql.py`
  Optional helpers to detect and extract KQL blocks from Markdown content.

* `extras/qc.py`
  Optional quality-control scorers and CSV writers used when QC extras are installed, now thin adapters over the shared QC driver.

> **Note:** Additional core modules under `core/` that are not listed here yet should be added with short summaries as they are introduced or become relevant.

---

### 3.2 Top-level package – `src/repocapsule/`

* `__init__.py`
  Defines the public package surface for `repocapsule`, re-exporting primary configuration types (e.g., `RepocapsuleConfig`) and high-level helpers such as `convert`, `convert_local_dir`, and `convert_github`. Non-exported symbols are considered expert/unstable.

---

### 3.3 CLI – `src/repocapsule/cli/`

* `__init__.py`
  CLI package initializer.
  *Summary pending (likely minimal; may just expose entrypoints or re-export runner helpers).*

* `main.py`
  Command-line entrypoint module invoked by the `repocapsule` console script; responsible for parsing CLI arguments and dispatching to runner helpers.
  *Summary pending.*

* `runner.py`
  High-level orchestration helpers used by the CLI and library:

  * Builds GitHub/local/web-PDF repo profiles.
  * Constructs configs/output paths using core factories.
  * Invokes the builder and pipeline engine to execute runs.
  * Provides convenience wrappers (e.g., `convert_github`, `convert_local_dir`, `convert_web_pdf`).

---

### 3.4 Sources – `src/repocapsule/sources/`

* `__init__.py`
  Source package initializer.
  *Summary pending for individual source modules.*

* `fs.py`
  Local filesystem source that walks repositories with gitignore support and streaming hints.

* `githubio.py`
  GitHub zipball source utilities: parsing specs/URLs, API helpers, download/iterate archive members.

* `sources_webpdf.py`
  Web PDF sources for lists of URLs or in-page PDF links with download/extract logic.

* `pdfio.py`
  PDF reading/extraction helpers used by web-PDF sources.

* `csv_source.py`
  CSV-backed source emitting text rows with configurable column selection.

* `jsonl_source.py`
  JSONL source that streams records from existing JSONL files.

* `parquetio.py`
  Parquet source for ingesting Parquet datasets as records.

* `sqlite_source.py`
  SQLite source for reading text columns from database files.

* `evtxio.py`
  Windows Event Log (EVTX) source for ingesting event records.

---

### 3.5 Sinks – `src/repocapsule/sinks/`

* `__init__.py`
  Sink package initializer.
  *Summary pending for individual sink modules.*

* `sinks.py`
  Built-in sink implementations (JSONL, gzip JSONL, prompt text, no-op) and sink protocol helpers.

* `parquet.py`
  Parquet dataset sink for writing records to Parquet files.

---

### 3.6 Scripts – `scripts/`

Manual, developer-focused scripts for end-to-end testing and experiments (not part of the public API).

* `scripts/manual_test_github.py`
  Manual smoke-test script to run a GitHub → JSONL/Parquet pipeline using hard-coded parameters.

* `scripts/manual_test_github_toml.py`
  Manual test driving GitHub conversion from a TOML config file.

* `scripts/manual_test_web_pdf.py`
  Manual test for web PDF ingestion and conversion through the pipeline.

---

### 3.7 Tests – `tests/`

Pytest-based test suite validating core behavior and non-core wiring.

* `tests/test_builder_runtime_layering.py`
  Tests around separation of config vs runtime (`PipelinePlan`, `PipelineRuntime`) and layering behavior in the builder.

* `tests/test_chunk.py`
  Unit tests for chunking logic (`ChunkPolicy`, `iter_chunk_dicts`, token limits, etc.).

* `tests/test_cli_main.py`
  Tests for the CLI entrypoint (`cli.main`) and argument handling.

* `tests/test_concurrency.py`
  Tests for `concurrency.py` executors, bounded submission, and configuration via `RepocapsuleConfig`.

* `tests/test_config_builder_pipeline.py`
  End-to-end tests tying together config parsing, builder wiring, and pipeline execution.

* `tests/test_convert.py`
  Tests for `convert.py` helpers (decode + chunk → records) and mode/format detection.

* `tests/test_dataset_card.py`
  Tests dataset card generation and rendering behavior.

* `tests/test_decode.py`
  Tests for byte decoding, encoding detection, and normalization rules.

* `tests/test_log_and_naming.py`
  Tests for logging helpers and naming utilities (`naming.py`).

* `tests/test_pipeline_middlewares.py`
  Tests for pipeline middleware / hooks behavior and integration.

* `tests/test_plugins_and_registries.py`
  Tests plugin discovery and registry behavior for sources, sinks, and scorers.

* `tests/test_qc_controller.py`
  Tests inline QC controller behavior and interaction with the pipeline.

* `tests/test_qc_defaults.py`
  Tests default QC configuration and behavior (e.g., default scorers, thresholds).

* `tests/test_records.py`
  Tests record construction, metadata propagation, and header generation.

* `tests/test_runner_finalizers.py`
  Tests that sinks and finalizers run correctly at the end of a pipeline run (run-summary, prompt files, etc.).

* `tests/test_safe_http.py`
  Tests `safe_http.py` HTTP client behavior, redirect/IP safety, and global client helpers.

---

### 3.8 Repository root

Top-level files in the repository.

* `.gitattributes`
  Git attributes configuration (e.g., line endings, linguist hints).

* `.gitignore`
  Git-ignore rules for build artifacts, virtualenvs, etc.

* `README.md`
  Human-facing project overview, usage instructions, and feature list.

* `example_config.toml`
  Example `RepocapsuleConfig` in TOML form used for docs.

* `manual_test_github.toml`
  Example TOML config tailored for manual GitHub conversion tests.

* `pyproject.toml`
  Build configuration, dependencies, and project metadata for packaging.

* `sample.jsonl`
  Sample output JSONL dataset used for manual inspection and/or tests.

* `project_files.md`
  File tree for this repository.

---

### 3.9 Notes for future expansion

* As new modules are added (especially under `src/repocapsule/core/` and `src/repocapsule/sources` / `sinks`), they should be appended here with:

  * One-line purpose summary.
  * How they depend on or extend existing core modules.
* For planned-but-not-yet-implemented modules, add entries like:

  ```markdown
  - `src/repocapsule/core/FOO.py`  
    Summary pending – planned module for …
  ```

  and update them once the implementation lands.
