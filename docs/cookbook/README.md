# Sievio Cookbook

This directory contains task-focused recipes and configuration examples for common Sievio workflows. Each recipe demonstrates how to wire together core components—sources, sinks, middleware, and QC—to solve specific problems.

## Recipes

### [PDF Ingestion](pdf_ingestion.md)
**File:** `pdf_ingestion.md`
Learn how to ingest remote PDFs from a list of URLs or a web page.
- **Key Concepts:** `WebPdfSource`, `SafeHttpClient`, binary decoding.
- **Requires:** `pip install -e ".[pdf]"`

### [Custom PII Scrubbing](custom_pii_scrubbing.md)
**File:** `custom_pii_scrubbing.md`
Implement a lightweight `RecordMiddleware` to scrub sensitive patterns (emails, phone numbers) before data hits the sink.
- **Key Concepts:** `RecordMiddleware`, regex scrubbing, pipeline overrides.
- **Requires:** Core only.

### [Deduplication & Post-QC](dedup_post_qc.md)
**File:** `dedup_post_qc.md`
A guide to setting up global deduplication using MinHash LSH and running quality control passes after the main ingestion.
- **Key Concepts:** `GlobalDedupStore`, `PostQCHook`, `JSONLQualityScorer`.
- **Requires:** `pip install -e ".[qc]"`

---

## Usage Tip

All recipes use standard `sievio` CLI commands or Python scripts. Before running a recipe, ensure you have the necessary "extras" installed in your environment:

```bash
# Install all common extras (recommended for dev)
pip install -e ".[dev,tok,pdf,evtx,parquet,qc]"
```