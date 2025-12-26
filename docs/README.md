# Sievio Documentation

Welcome to the documentation for **Sievio**, the library-first, configuration-driven ingestion pipeline. This directory contains detailed guides for operators, developers, and AI agents.

## Core Documentation
*Start here to run, configure, and understand the pipeline.*

- **[Technical Manual](TECHNICAL_MANUAL.md)** (`TECHNICAL_MANUAL.md`)
  The primary operator runbook. Covers installation, pipeline architecture, CLI commands, Python API usage, and debugging flows.
- **[Configuration Reference](CONFIGURATION.md)** (`CONFIGURATION.md`)
  The exhaustive reference for `SievioConfig`. Generated directly from the dataclasses.
  > **Note:** Run `python3 scripts/generate_configuration_md.py` to refresh this file after code changes.

## Operations & Scale
*For running in production, ensuring quality, and handling large datasets.*

- **[Deployment & Sharding](DEPLOYMENT.md)** (`DEPLOYMENT.md`)
  Guide to running distributed/sharded jobs, merging statistics, and operational tips for high-throughput environments.
- **[Quality Control](QUALITY_CONTROL.md)** (`QUALITY_CONTROL.md`)
  Deep dive into QC signals, safety modes (inline vs. post-hoc), scoring heuristics, and export formats.

## Recipes
*Task-specific guides and examples.*

- **[Cookbook](cookbook/)** (`cookbook/`)
  Practical recipes for common tasks, including:
  - PDF ingestion and parsing.
  - Custom PII scrubbing/regex patterns.
  - Deduplication and post-QC workflows.

## For Developers & AI Agents
*Internal architecture and contribution guidelines.*

- **[Contributing](CONTRIBUTING.md)** (`CONTRIBUTING.md`)
  Links to the canonical contribution guide and setup instructions.
- **[LLM Architecture Guide](../LLMS.md)** (`../LLMS.md`)
  **Critical for AI:** A condensed routing map and architecture guide for LLMs working on this codebase.
- **[Agent Rules](../AGENTS.md)** (`../AGENTS.md`)
  **Critical for AI:** Operational rules, safety invariants, and "definition of done" for AI coding assistants.

---

### Quick Links
- **Root README**: [`../README.md`](../README.md) – Quickstart and high-level overview.
- **Example Config**: [`../example_config.toml`](../example_config.toml) – Comprehensive configuration file with defaults.