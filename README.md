# RepoCapsule
Library-first, config-driven pipeline that turns repositories, logs, and other text/structured sources into normalized JSONL and Parquet datasets for LLM fine-tuning and analysis.

Define a pipeline once in Python or TOML (`RepocapsuleConfig`), run it via a small set of helpers (`convert_local_dir`, `convert_github`, `convert`), and get a stable record schema and dataset card fragments. Requires Python 3.11+.

## Key features
- **Sources (ingest):** Local directories, GitHub zipballs, web PDFs (page scrape or URL list), CSV/TSV, and SQLite tables/queries. Optional bytes handlers (PDF/EVTX/Parquet) activate when extras are installed. All are configured via `SourceConfig` and declarative `[[sources.specs]]` entries (see `core/config.py` and `example_config.toml`), and implemented under `src/repocapsule/sources`.
- **Processing (decode → chunk → extract → records):** Safe decoding with Unicode normalization and mojibake repair (`core/decode.py`), document/code-aware chunking with token targets and overlap (`ChunkPolicy` in `core/chunk.py`), optional extractors (e.g., Markdown→KQL in `core/extras/md_kql.py`), and record-building in `core/convert.py` / `core/records.py`. Repo-level metadata flows via `RepoContext` and `RunMetadata`.
- **Sinks (outputs):** JSONL (plain or gzipped) plus grouped prompt-text, and a Parquet dataset sink (via the `[parquet]` extra) implemented in `src/repocapsule/sinks`. Sinks are configured through `SinkConfig` and `[[sinks.specs]]` (e.g., `default_jsonl_prompt`, `parquet_dataset`) and can participate in run finalization.
- **Quality control (optional):** Inline/advisory/post QC modes (`QCConfig` / `QCMode` in `core/config.py`) with heuristics, near-duplicate detection, CSV export, and customizable scorers (`QualityScorer` in `core/interfaces.py`, implemented in `core/extras/qc.py`). QC uses utilities from `core/qc_utils.py` and wiring in `core/qc_controller.py` and `cli/runner.py`.
- **Dataset cards:** Each run can emit Hugging Face–style dataset-card fragments (`*.card.json`) alongside the primary JSONL, then merge them into a final Markdown card using helpers in `dataset_card.py` controlled by `[dataset_card]` in the config.
- **Extensibility:** Registries (`core/registries.py`) and entry-point plugins (`repocapsule.plugins` via `core/plugins.py`) allow new sources, sinks, bytes handlers, and QC scorers to be registered without modifying the core pipeline. New features should plug into the registries and builder, not bypass them.

## Installation
From PyPI (when published):
```sh
pip install repocapsule
```
Optional extras (see `pyproject.toml`):
- `pip install "repocapsule[tok]"` – exact token counts via tiktoken.
- `pip install "repocapsule[pdf]"` – PDF ingestion/handling.
- `pip install "repocapsule[evtx]"` – Windows EVTX ingestion.
- `pip install "repocapsule[parquet]"` – Parquet sink and Parquet-as-source handler.
- `pip install "repocapsule[qc]"` – QC scorer (Torch/Transformers/tiktoken/pyyaml).

Development install from a clone:
```sh
pip install -e ".[dev,tok,pdf,evtx,parquet,qc]"
```

## Quickstart (library)
Minimal local directory → JSONL (with optional prompt text and a dataset-card fragment):
```python
from repocapsule import convert_local_dir

stats = convert_local_dir(
    root_dir="path/to/repo",
    out_jsonl="out/repo.jsonl",
    out_prompt="out/repo.prompt.txt",  # optional
)
print(stats)  # {'files': ..., 'records': ..., 'qc': {...}, ...}
```
This writes a JSONL file plus an optional prompt-text file, and (with default settings) a `*.card.json` sidecar next to the JSONL describing the run.

GitHub repo → JSONL:
```python
from repocapsule import convert_github

stats = convert_github(
    url="https://github.com/owner/repo",
    out_jsonl="out/repo.jsonl",
    out_prompt="out/repo.prompt.txt",  # optional
)
```
The helper infers repo metadata (owner/repo, default branch, license where possible) and injects it into `RepoContext` so it flows into record metadata and dataset-card fragments.

TOML-driven run:
```python
from repocapsule import load_config_from_path, convert

cfg = load_config_from_path("example_config.toml")
stats = convert(cfg)
```
`convert(...)` also accepts an already-built `PipelineEngine` if you need to reuse runtime wiring. In all cases the return value is a `dict` derived from `PipelineStats.as_dict()` (files/bytes/records, per-extension counts, QC summary).

## Concepts & architecture
### Configuration (`RepocapsuleConfig`)
`RepocapsuleConfig` in `core/config.py` is the single source of truth for how a run is wired. Its major sections map directly to the TOML layout:
- `sources`: defaults (`[sources.local]`, `[sources.github]`, `[sources.pdf]`, `[sources.csv]`, `[sources.sqlite]`) plus declarative `[[sources.specs]]` entries (e.g., `local_dir`, `github_zip`, `web_pdf_list`, `web_page_pdf`, `csv_text`, `sqlite`).
- `decode`: Unicode normalization, control stripping, mojibake repair, and optional per-file byte caps (`DecodeConfig`).
- `chunk`: tokenizer selection (`tokenizer_name`), language metadata attachment, and the `ChunkPolicy` used for chunk sizes/overlap/semantic splitting.
- `pipeline`: concurrency and processing behavior (`max_workers`, `submit_window`, `executor_kind`, `fail_fast`) plus optional extractors/file extractors/bytes handlers (wired via registries or Python, not TOML).
- `sinks`: defaults for output location and compression plus `[[sinks.specs]]` entries that instantiate sinks and determine the primary JSONL and prompt paths.
- `http`: settings used to build a `SafeHttpClient` shared across remote-capable sources.
- `qc`: `QCConfig` and `QCHeuristics` for scoring, dedup, mode selection, and post-QC overrides.
- `logging`: `LoggingConfig` for level/propagate/format/logger_name, applied before pipelines are built.
- `metadata`: `RunMetadata` (primary JSONL, prompt path, repo URL, arbitrary `extra` fields) stored in run summaries and dataset-card fragments.
- `dataset_card`: `DatasetCardConfig` describing split name, license, task categories/ids, tags, and the enable flag.
Configs are composable in Python or loaded from TOML (`load_config_from_path`, `RepocapsuleConfig.from_toml`). `example_config.toml` is the canonical reference for all fields.

### Sources
In plain language, a `Source` enumerates files or rows and yields `FileItem` objects (repository-relative `path`, raw `data` bytes, `size`, `origin_path`, and a `streamable` hint). Built-ins (wired via `SourceRegistry` in `core/registries.py` and factories in `core/factories.py`) include:
- `local_dir` (`sources.fs.LocalDirSource`): gitignore-aware, hidden-file filtering, size caps, prefix reads.
- `github_zip` (`sources.githubio.GitHubZipSource`): zipball download with size/member/compression limits and include/exclude extension filters.
- `web_pdf_list` / `web_page_pdf` (`sources.sources_webpdf`): fetch PDFs directly or scrape a page for PDF links; concurrency controlled by `PdfSourceConfig` + `HttpConfig`.
- `csv_text` (`sources.csv_source.CSVTextSource`): CSV/TSV (optionally gzipped) rows → `FileItem` using a chosen text column.
- `sqlite` (`sources.sqlite_source.SQLiteSource`): table/query streaming with optional download, batch sizing, and column selection.
Optional bytes handlers (via `BytesHandlerRegistry` and plugins) cover PDF (`sources.pdfio`), EVTX (`sources.evtxio`), and Parquet (`sources.parquetio`) when the corresponding extras are installed.

Declarative `[[sources.specs]]` blocks in TOML map directly to these kinds and are expanded by `default_source_registry()`. To add a new source, implement the `Source` protocol, create a `SourceFactory` that knows how to turn a `SourceSpec` into one or more concrete sources, register it in a registry (either by calling `default_source_registry().register(...)` in core code or via a `repocapsule.plugins` entry point), and wire it through `sources.specs`.

### Decode & chunk
The decode step takes `FileItem.data` bytes and turns them into normalized text. `DecodeConfig` controls Unicode normalization, newline normalization, stripping of unsafe control characters, mojibake repair, and optional soft caps on bytes passed to the decoder. The logic lives in `core/decode.py` (`decode_bytes` / `read_text`).

The chunk step takes decoded text and produces token-aware spans suitable for LLM consumption. `ChunkConfig` controls tokenizer selection (`tokenizer_name`, using `tiktoken` when `[tok]` is installed) and whether language metadata is attached. `ChunkPolicy` in `core/chunk.py` drives doc/code chunking behavior:
- `mode`: `"doc"` vs `"code"`; doc mode uses Markdown/rST-aware splitters, code mode uses line-based splitting.
- `target_tokens`, `overlap_tokens`, `min_tokens`: control average chunk size and overlap between chunks.
- `semantic_doc` and `semantic_tokens_per_block`: enable paragraph/sentence-aware refinement for large text blocks.

Under the hood, doc chunks are built from block splitters (`split_doc_blocks` / `_split_markdown_blocks` / `_split_rst_blocks`) and code chunks from `_split_code_lines`. Token counts fall back to fast heuristics when `tiktoken` is absent (`count_tokens`).

### Extractors & records
`Extractor`, `FileExtractor`, and `StreamingExtractor` protocols (in `core/interfaces.py`) let you add records derived from decoded text or file streams (for example, extracting KQL blocks from Markdown via `core/extras/md_kql.py`). `DefaultExtractor` (in `core/convert.py`) is the standard file-level extractor that decodes, chunks, and builds records for each `FileItem`.

Records are plain dicts with a `"text"` field and a `"meta"` mapping built by `build_record` in `core/records.py`. `RecordMeta` documents the canonical keys (source, repo, path, license, lang, chunk_id/n_chunks, encoding, sha256, token/byte counts, file sizes, etc.), with QC-specific enrichments captured via `QC_META_FIELDS`. `RepoContext.as_meta_seed()` seeds repo-level metadata (e.g., `repo_url`, `repo_full_name`, `license`) into every record’s meta.

`core/records.py` also contains summary metadata types (`RunSummaryMeta`, `QCSummaryMeta`), helpers like `ensure_meta_dict`, and `is_summary_record` for detecting footer records such as run summaries.

### Sinks
`Sink` implementations (via factories/registries):
- `JSONLSink` / `GzipJSONLSink` – streaming JSONL writers.
- `PromptTextSink` – grouped prompt text per file/chunk.
- `ParquetDatasetSink` (`sinks/parquet.py`) – file or partitioned dataset with configurable text/meta fields, partition columns, row group size, compression (requires `[parquet]` extra).
`[[sinks.specs]]` in TOML map to the `default_jsonl_prompt` and `parquet_dataset` factories in `core/factories.py`; `sinks.output_dir` / `sinks.jsonl_basename` provide defaults when explicit paths are not set. Sinks may implement `finalize(records)` to consume run summaries and other “footer” records after the main pipeline completes.

### Pipeline engine
- `build_pipeline_plan(config)` (in `core/builder.py`) validates config, loads plugins, builds sources/sinks/bytes handlers/QC hooks, attaches run-header records, resolves HTTP/QC/executor wiring, and returns an immutable `PipelinePlan`.
- `PipelineEngine` (in `core/pipeline.py`) iterates sources, decodes and chunks files, applies optional file/record middleware (including inline QC), dispatches to sinks, and collects `PipelineStats` (files/bytes/records/by_ext/QC summary).
- High-level entry points: `convert(config_or_engine)` and convenience wrappers `convert_local_dir` / `convert_github` (in `cli/runner.py`) build profiles and run the engine. These are thin orchestration layers; new functionality should plug into the builder/registries rather than creating ad-hoc pipelines.

### Concurrency
- `pipeline.executor_kind`: `"thread"`, `"process"`, or `"auto"` (default). `pipeline.max_workers` / `submit_window` tune pool size and submission window.
- `resolve_pipeline_executor_config` (in `core/concurrency.py`) inspects the configured sources and bytes handlers (for example, PDF/EVTX-heavy pipelines) and chooses thread vs process executors when `executor_kind="auto"`. Text/code-only workloads default to threads; heavy binary handlers and sources bias toward processes. `fail_fast` controls whether worker failures abort the run.
- QC post-processing uses `resolve_qc_executor_config`; `qc.parallel_post` enables process-based scoring when available.

### Logging
`LoggingConfig.apply()` (in `core/config.py`) configures the package logger via `core/log.py` (level, propagation, format, logger_name). Integrate with host logging by setting `propagate=True` or a custom logger name and wiring it into your application’s logging tree.

### HTTP & remote safety
`SafeHttpClient` (`core/safe_http.py`) is the hardened HTTP client used by GitHub/PDF/SQLite download helpers: DNS resolution with private/loopback blocking, redirect whitelisting (`allowed_redirect_suffixes`), and timeout/redirect limits. `HttpConfig.build_client()` builds/reuses the client and can optionally install it as a global for simple scripts; library code should prefer explicit clients.

### Plugin system
`core/plugins.py` loads entry points under the `repocapsule.plugins` group. A plugin receives `SourceRegistry`, `SinkRegistry`, `BytesHandlerRegistry`, and `QualityScorerRegistry` instances and can register new kinds (sources/sinks/bytes handlers/QC scorers). Plugins are loaded automatically by `build_pipeline_plan(load_plugins=True)`.

## Quality control (QC)
- `QCConfig` (`core/config.py`) controls whether QC is enabled, the mode (`inline`, `advisory`, `post`, `off` via `QCMode`), score thresholds (`min_score`), near-duplicate handling (`drop_near_dups`), error behavior (`fail_on_error`), CSV emission (`write_csv`, `csv_suffix`), and post-QC concurrency overrides (`parallel_post`, `post_executor_kind`, `post_max_workers`, `post_submit_window`). `QCHeuristics` tunes target token bands, repetition window, code weights, and simhash/minhash knobs.
- **Inline/advisory:** For `mode="inline"` or `"advisory"`, `build_pipeline_plan` wires an `InlineQCController` (`core/qc_controller.py`) into the pipeline. It wraps a `QualityScorer` (typically `JSONLQualityScorer` from `core/extras/qc.py` when `[qc]` extras are installed), updates `QCSummaryTracker` in `PipelineStats`, attaches QC metadata to each record’s `meta`, and optionally drops records that fail QC gates (inline only).
- **Post-QC:** For `mode="post"`, `cli/runner.run_engine` can rescore the primary JSONL after extraction, using either an existing scorer (`QCConfig.scorer`) or one built via `make_qc_scorer`. It supports sequential or process-based scoring (`qc.parallel_post`), merges QC summaries into run summaries, and optionally writes a QC CSV ({primary_jsonl}_quality.csv).
- **CLI:** `pyproject.toml` declares a `repocapsule-qc` entry point, but there is no dedicated CLI module in this repository; use the programmatic API (`JSONLQualityScorer`, `score_jsonl_to_csv`, `QCConfig`) for now.

Rules of thumb for QC contributions:
- Implement new scorers by conforming to `QualityScorer` and, if you want them discoverable via config/registries, by adding a `QualityScorerFactory` and registering it with `quality_scorer_registry` (see `core/extras/qc.py`).
- Do not hard-code scorer instances into configs or pipelines; keep `qc.scorer` unset in declarative TOML and rely on registries/plugins instead.
- Prefer enriching QC heuristics by extending `QCHeuristics` / `qc_utils.py` and ensuring new signals are exported via `filter_qc_meta` so they land in `meta["extra"]["qc_signals"]` rather than top-level QC fields.

## Dataset cards (Hugging Face style)
- Each `convert(...)` / `run_engine(...)` call writes a per-run fragment (`*.card.json`) next to the primary JSONL via `write_card_fragment_for_run` (unless `[dataset_card].enabled = false`). A `CardFragment` captures `split`, `num_examples`, `num_bytes`, detected languages, license, size categories, task categories/ids, tags, source repos, and run stats.
- `[dataset_card]` in config (`DatasetCardConfig` in `core/config.py`) seeds fragment metadata: `split_name`, `license`, `task_categories`, `task_ids`, `tags`, plus the enable flag. Repo-level metadata from `RunMetadata` and `RepoContext` is also folded into fragments.
- `DatasetCardFields` / `render_dataset_card` in `dataset_card.py` build Hugging Face–style dataset cards (YAML front matter + Markdown). You can merge multiple fragments into a single card using `merge_fragments` + `render_dataset_card`, or call `build_dataset_card_from_fragments([...], overrides=..., body_overrides=...)` directly.
- Example end-to-end workflow:
  ```python
  from pathlib import Path
  from repocapsule.dataset_card import build_dataset_card_from_fragments

  fragments = list(Path("out").glob("*.card.json"))
  card_md = build_dataset_card_from_fragments(fragments)
  Path("out/dataset_card.md").write_text(card_md, encoding="utf-8")
  ```
This pattern works across many runs: each run appends a fragment, and the final card aggregates size, splits, languages, and tags.

## Configuration reference
Use `example_config.toml` as the canonical reference. A compact TOML sketch:
```toml
[sources.local]
skip_hidden = true

[[sources.specs]]
kind = "local_dir"
options = { root_dir = "path/to/repo" }

[[sources.specs]]
kind = "github_zip"
options = { url = "https://github.com/owner/repo/archive/refs/heads/main.zip" }

[sinks]
output_dir = "out"
jsonl_basename = "data"

[[sinks.specs]]
kind = "default_jsonl_prompt"
[sinks.specs.options]
jsonl_path = "out/data.jsonl"
prompt_path = "out/data.prompt.txt"

[[sinks.specs]]
kind = "parquet_dataset"
[sinks.specs.options]
path = "out/data.parquet"
partition_by = ["repo"]
compression = "snappy"
overwrite = true

[decode]
normalize = "NFC"
strip_controls = true
fix_mojibake = true

[chunk.policy]
mode = "doc"
target_tokens = 1700
overlap_tokens = 40

[qc]
enabled = false
mode = "inline"
min_score = 60.0

[dataset_card]
enabled = true
split_name = "train"
```
See `example_config.toml` for every knob (includes HTTP, logging, QC heuristics, and dataset card fields).

## Extending RepoCapsule
- **New Source/Sink:** Implement the `Source` or `Sink` protocol, then register a factory with `SourceRegistry`/`SinkRegistry` (via `core/registries.default_*` or a plugin). Place code under `src/repocapsule/sources/` or `sinks/` and add tests.
- **New bytes handler:** Register `(sniff, handler)` with `BytesHandlerRegistry` (e.g., for new binary formats). Handlers return iterable records given bytes, relative path, optional `RepoContext`, and optional `ChunkPolicy`.
- **Custom QC scorer:** Implement `QualityScorer` or a factory with an `id` and `build(cfg: QCConfig)`. Register via `quality_scorer_registry` or a plugin. Keep `qc.scorer` unset in declarative configs; use the registry instead.
- **Plugins:** Publish an entry point under `repocapsule.plugins` that receives all registries and performs registrations.

## Conventions for contributors
- **File layout:** New ingestion code lives under `src/repocapsule/sources/`, new sinks under `src/repocapsule/sinks/`, QC-related helpers under `src/repocapsule/core/extras/` or `src/repocapsule/core/`, and orchestration/CLI helpers under `src/repocapsule/cli/`. Keep new modules cohesive and small.
- **Registration, not wiring by hand:** Prefer `SourceRegistry`, `SinkRegistry`, `BytesHandlerRegistry`, and `QualityScorerRegistry` (optionally via `repocapsule.plugins`) over ad-hoc wiring. Declarative configs should stay runtime-free: do not stash live clients, scorers, or extractors inside `RepocapsuleConfig` fields in TOML.
- **HTTP and safety:** Use `SafeHttpClient` via `HttpConfig.build_client()` or `safe_http.get_global_http_client()` for all remote access (GitHub, PDFs, SQLite downloads). Avoid direct `requests` or `urllib` usage outside `safe_http` and source modules that already use it.
- **Logging:** Use `get_logger(__name__)` and respect `LoggingConfig` for levels/format/propagation. Avoid printing directly to stdout/stderr in core logic; log at `INFO`/`WARNING` where appropriate.
- **QC defaults:** Keep `qc.enabled` defaulting to `false` in example configs, and rely on `QCHeuristics` / `QCConfig` for tunable behavior. New QC behavior should either be implemented as a `QualityScorer` or as utilities in `qc_utils.py`, not baked into sinks or sources.
- **Tests and style:** Add tests under `tests/` using `pytest`, and run `PYTHONPATH=src pytest`. Maintain typing and style with `mypy --config-file pyproject.toml src` and `ruff check .`. Follow existing naming patterns (`*Source`, `*Sink`, `*Config`, `*Factory`) to keep the API surface predictable.

## Development & testing
- Dev dependencies: `[project.optional-dependencies].dev` (`pytest`, `pytest-cov`, `ruff`, `mypy`, `build`, `twine`).
- Tests: `PYTHONPATH=src pytest`
- Lint/format/type-check: `ruff check .` and `mypy --config-file pyproject.toml src`
- Minimum Python: 3.11
- Sample scripts: `scripts/manual_test_github_toml.py` (GitHub smoke test with QC/KQL option), `scripts/manual_test_web_pdf.py` (web PDF ingestion). They prepend `src/` to `sys.path` for in-repo runs.

## Contributor cheat sheet

This is the “I just want to do X” quick guide. Use it as a map to the rest of the README and codebase.

### If you want to ingest a new source type

Goal: “I have a new place where data lives (e.g. API, new DB, custom archive) and I want it to show up as records.”

1. Implement the `Source` protocol (see `core/interfaces.py`):
   - Yields `FileItem` objects with `path`, `data` (bytes), `size`, `origin_path`, `streamable`.
2. Implement a `SourceFactory` that:
   - Accepts a `SourceSpec` (from config).
   - Returns one or more concrete `Source` instances.
3. Register the factory with `SourceRegistry`:
   - Either in core (via `default_source_registry()` in `core/registries.py`), or
   - Via a plugin entry point (`repocapsule.plugins`) using `core/plugins.py`.
4. Add a `[[sources.specs]]` example to your config (TOML or JSON) so people know how to use it.

Rule of thumb: **All new sources should be discoverable by `kind` from config**, not hard-wired in code.

---

### If you want to add a new sink or output format

Goal: “I want to write output in a new format or layout (e.g. new Parquet layout, vector store dumper).”

1. Implement the `Sink` protocol (see `core/interfaces.py`):
   - Must support `write(record)` and `close()`.
   - Optional `finalize(records)` for appending run summaries or footers.
2. Implement a `SinkFactory` that:
   - Accepts a `SinkSpec` (from config).
   - Returns one or more concrete `Sink` instances plus primary paths (if needed).
3. Register the factory with `SinkRegistry`:
   - Via `default_sink_registry()` or a plugin.
4. Document how to use it via `[[sinks.specs]]` in config, including any required options.

Rule of thumb: **Sinks own file layout and compression; the pipeline just sends them records.**

---

### If you want to handle a new binary format

Goal: “I have a binary file type (e.g. custom logs, new event format) and want to turn it into records.”

1. Implement a bytes handler function:
   - Signature: `(data: bytes, rel_path: str, repo: RepoContext | None, policy: ChunkPolicy | None) -> Iterable[Record] | None`.
   - It may:
     - Decode and chunk internally, or
     - Return `None` to fall back to normal text handling.
2. Provide a “sniff” function that can recognize files of this type (magic bytes, extension, etc.).
3. Register `(sniff, handler)` with `BytesHandlerRegistry` (see `core/factories.py`).
4. Optionally expose it behind an extra (`[pdf]`, `[evtx]`, `[parquet]`-style) in `pyproject.toml`.

Rule of thumb: **Binary formats plug in via bytes handlers, not custom sources**, unless their location rules are also special.

---

### If you want to tweak record metadata

Goal: “I want different metadata on each record (extra fields, different path style, etc.).”

1. Look at `RecordMeta` and `build_record` in `core/records.py`.
2. Use `RepoContext.as_meta_seed()` to add repo-wide metadata.
3. Use `ensure_meta_dict` and `merge_meta_defaults` to safely extend metadata without breaking other components.
4. For QC-related metadata, make sure new signals end up under `meta["extra"]["qc_signals"]` (see `filter_qc_meta`).

Rule of thumb: **Keep `meta` stable and additive; don’t break existing keys that sinks or cards rely on.**

---

### If you want to change or extend QC behavior

Goal: “I want to change how quality scores are computed, what gets dropped, or what is exported.”

1. Start with `QCConfig` / `QCHeuristics` in `core/config.py` and `core/qc_utils.py`.
2. Implement or extend a `QualityScorer` (`core/interfaces.py`), or use `JSONLQualityScorer` (`qc.py`) as a reference.
3. Wrap new scorers in a `QualityScorerFactory` and register with `quality_scorer_registry` (`core/registries.py` / `core/factories.py`).
4. For inline/advisory QC, see `InlineQCController` and `QCSummaryTracker` in `core/qc_controller.py`.
5. For post-QC, see `qc.py` and how `run_engine` uses `QCConfig` to drive post-processing.

Rule of thumb: **New QC logic should be expressed as scorers + heuristics, not baked into sources or sinks.**

---

### If you want to touch the runner / high-level API

Goal: “I want to adjust how convert helpers behave or add a new ‘profile’ helper.”

1. Look at `runner.py`:
   - `convert`, `convert_local_dir`, `convert_github`, and `run_engine`.
   - Any profile builder helpers that create `RepocapsuleConfig` instances.
2. Keep `convert` thin: it should build a `PipelineEngine` via `build_pipeline_plan`, run it, and return `PipelineStats.as_dict()`.
3. If adding a new profile helper (e.g. “convert_something_else”), make sure it:
   - Builds a valid `RepocapsuleConfig`.
   - Uses existing factories/registries for sources/sinks.
   - Populates `metadata` and `[dataset_card]` sensibly.

Rule of thumb: **Profiles = “pre-filled config + sensible outputs”, not special pipelines.**

---

### If you want to work on dataset cards

Goal: “I want to change what’s in the HF dataset card or add new signals.”

1. Look at `dataset_card.py`:
   - `CardFragment`, `DatasetCardFields`.
   - `build_card_fragment_for_run`, `write_card_fragment_for_run`.
   - `merge_fragments`, `render_dataset_card`, `build_dataset_card_from_fragments`.
2. Add new stats to `PipelineStats` (if needed) and make sure they are included in `extra.stats` when building fragments.
3. Update `merge_fragments` to aggregate any new fields correctly.
4. Keep compatibility with the Hugging Face dataset card template (YAML front matter + Markdown sections).

Rule of thumb: **Card content is driven by fragments + config; don’t hardcode project-specific text in library code.**

## Configuration options in context

This section explains what the major configuration sections *mean* in terms of pipeline behavior, and when you’re likely to touch them. For full field lists, see `example_config.toml` and `core/config.py`.

### `sources.*` – What gets ingested

**Context:** Controls *where data comes from* and how aggressively it is filtered.

- `[sources.local]`, `[sources.github]`, `[sources.pdf]`, `[sources.csv]`, `[sources.sqlite]` set **defaults** for classes of sources (e.g. max file bytes, skip hidden, respect `.gitignore`, default columns).
- Each `[[sources.specs]]` entry says:
  - **`kind`** → which `SourceFactory` to use (e.g. `"local_dir"`, `"github_zip"`, `"csv_text"`, `"sqlite"`).
  - **`options`** → concrete parameters (paths, URLs, table names, queries, text column, etc.).
- These options define the “universe” of files/rows that will ever be seen by the pipeline. Downstream config can only filter further; it can’t bring back things the source never yielded.

Use this when:
- You want to add/remove repositories, log directories, or data tables.
- You need to cap input sizes or restrict extensions (performance/safety).
- You want multiple logical inputs feeding the same pipeline (e.g. several repos + a CSV log dump).

---

### `decode.*` – How bytes become text

**Context:** Controls **how raw bytes are turned into normalized text**, which affects chunking, QC, and token counts.

- Settings like `normalize`, `strip_controls`, `fix_mojibake` are about *clean, consistent text*: they aim to make downstream tokenization more predictable.
- `max_file_bytes` / similar caps give you protection against massive or corrupted inputs, at the cost of truncation.

Use this when:
- You see encoding issues (garbage characters, weird line breaks).
- You’re ingesting logs or binary-adjacent text that can contain odd control characters.
- You need hard safety bounds on read sizes.

---

### `chunk.*` – How text becomes model-friendly chunks

**Context:** Controls how decoded text is split into **LLM-sized units**.

- `policy.mode` (`"doc"` vs `"code"`) decides whether to favor paragraph/section boundaries or line blocks.
- `policy.target_tokens`, `overlap_tokens`, `min_tokens` tune:
  - Latency and dataset size (more/smaller chunks vs fewer/bigger).
  - Information redundancy via overlaps.
- `tokenizer_name` picks a tokenizer backend when `tok` extra is installed; otherwise a heuristic fallback is used.
- Certain options (e.g. `semantic_doc`) trade speed for nicer semantic splits on long documents.

Use this when:
- You’re preparing data for a specific model with a known context window.
- You want to trade off fewer, larger chunks (good for training) vs smaller chunks (better for retrieval or “question answering over docs”).
- You’re mixing code and prose and want different chunking policies per source (e.g. repos vs PDF docs).

---

### `pipeline.*` – Concurrency and processing behavior

**Context:** Controls *how* the pipeline runs (thread vs process, error handling), not what it does.

- `executor_kind`:
  - `"thread"` – best for I/O-bound work (file reads, HTTP).
  - `"process"` – best for CPU-heavy work (complex PDFs, EVTX parsing, heavy scorers).
  - `"auto"` – lets the library pick based on configured sources/handlers.
- `max_workers` and `submit_window` control throughput and memory usage:
  - More workers ⇒ more parallelism, but more RAM and potential I/O contention.
- `fail_fast` determines whether a single worker error kills the run vs aggregating errors in stats.

Use this when:
- You hit performance limits (too slow, under-utilized CPU).
- You hit resource limits (too many files open, memory pressure).
- You need deterministic failure behavior in production (e.g. CI should fail on any worker error).

---

### `sinks.*` – What the pipeline produces

**Context:** Controls **what artifacts are written** and how they’re named.

- `output_dir`, `jsonl_basename` set global defaults for primary outputs.
- Each `[[sinks.specs]]` entry says:
  - **`kind`** → which sink factory to use (JSONL, gzip JSONL, prompt text, Parquet dataset, etc.).
  - **`options`** → paths, compression, partitioning, overwrite behavior, etc.
- One sink is typically designated as the **primary JSONL** (used for QC/post-processing and dataset cards).

Use this when:
- You want to add/remove output formats (e.g. Parquet alongside JSONL).
- You’re integrating with downstream tools that expect a certain layout.
- You want to separate “primary training set” vs “diagnostic outputs”.

---

### `http.*` – Network safety and robustness

**Context:** Controls the behavior of the shared `SafeHttpClient` used by remote sources.

- `timeout`, `retries`, `user_agent` affect robustness and etiquette.
- PDF-specific settings (max PDF bytes, link caps, redirect rules) protect against unbounded downloads or hostile hosts.
- The underlying client enforces IP allow/deny policies and redirect safety.

Use this when:
- You’re fetching lots of remote content (GitHub, PDF lists, remote SQLite) and see timeouts or partial data.
- You want stricter limits in untrusted environments (e.g. pulling from the raw internet).

---

### `qc.*` – How quality control behaves

**Context:** Controls **whether** QC runs, **how strict** it is, and **where** it runs (inline vs post).

- `enabled` and `mode` (`inline`, `advisory`, `post`, `off`) determine:
  - Does QC run at all?
  - Does it drop records (`inline`) or only annotate them (`advisory` / `post`)?
- `min_score`, `drop_near_dups`, `heuristics` shape what “good enough” means.
- CSV/export related options control whether a separate QC report is produced.
- Post-QC concurrency options (`parallel_post`, etc.) mirror pipeline concurrency but only for scoring.

Use this when:
- You want to gate a dataset (e.g. drop the worst N% of records).
- You want diagnostic scoring only (don’t drop anything, just annotate).
- You’re tuning deduplication and heuristics for a particular data mix (code, logs, docs, etc.).

---

### `logging.*` – How noisy the library is

**Context:** Integrates RepoCapsule’s logging with your application or CLI.

- Controls:
  - Log level (INFO/DEBUG/WARNING).
  - Whether logs propagate to the root logger.
  - The logger name used by `get_logger(__name__)`.
- Applied early as part of `build_pipeline_plan`.

Use this when:
- You’re embedding RepoCapsule into a larger app and want consistent logging.
- You’re debugging a weird pipeline behavior and need more detail.

---

### `metadata.*` – Run-level metadata

**Context:** Stores cross-cutting **run identity** information that flows into summaries and dataset cards.

- `primary_jsonl` and `prompt_path` provide canonical locations for primary outputs.
- `repo_url` and other fields capture high-level context for all records in a run.
- `extra` is an open dict for user-defined metadata (run labels, notes, experiment IDs, etc.).

Use this when:
- You’re tracking runs across environments (e.g. experiment tracking).
- You want to carry project/internal metadata into every downstream consumer without patching records by hand.

---

### `dataset_card.*` – How HF-style metadata is seeded

**Context:** Bridges the pipeline to **Hugging Face dataset cards**.

- `enabled` toggles fragment writing on/off.
- `split_name` names the split produced by this run (`"train"`, `"validation"`, etc.).
- `license`, `task_categories`, `task_ids`, `tags` pre-populate `CardFragment` and `DatasetCardFields` so merged cards are meaningful without manual editing.

Use this when:
- You’re building a dataset intended for the Hugging Face Hub.
- You want per-run fragments that can later be merged into a single dataset card.
- You need consistent metadata across multiple runs/splits (e.g. train/validation/test produced on different days).

## Limitations & roadmap
- Extras required for certain formats/features: `[pdf]`, `[evtx]`, `[parquet]`, `[tok]`, `[qc]`. Without them, handlers are skipped or fall back to plain text.
- `repocapsule-qc` entry point is declared but no dedicated CLI module exists; use programmatic QC helpers for now.
- Token counts fall back to heuristic estimates when `tiktoken` is absent.
- Executor selection is heuristic; extremely large PDFs/EVTX workloads may need manual tuning of `pipeline.executor_kind`/`max_workers`.
- Future work: richer source types, additional sinks/handlers, stronger QC CLI, and expanded dataset card automation.

