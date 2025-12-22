# AGENTS.md – accel/ guidance (native acceleration package)

This directory contains the optional Rust acceleration add-on for Sievio (PyO3/maturin).
It **inherits** the repository-root `AGENTS.md` rules; follow those first. This file adds
stricter constraints for native code, ABI stability, determinism, and “optional install”
behavior.

## 1) Scope

- Implement **hot-path, pure(ish)** accelerators (CPU-heavy, allocation-heavy helpers), starting with QC utilities (e.g., hashing, shingling, token-ish heuristics).
- Preserve **observable Python semantics** (inputs/outputs, edge-case behavior, determinism) unless the task explicitly scopes a behavior change.
- Keep **I/O, network access, and pipeline orchestration** in Python. The Rust layer should not own sinks/sources, filesystem walking, HTTP, config loading, or record routing.

## 2) Package & boundary goals (maximum decoupling)

- The add-on must be **optional**:
  - Core `sievio` must run with no Rust toolchain and no compiled wheels installed.
  - Import failures must **gracefully fall back** to the pure-Python implementation.
- The Rust package should expose a **small, stable Python-facing API**. Prefer a limited set of functions that mirror the Python helpers rather than exposing internal Rust types.
- Keep the public surface **domain-oriented** (e.g., `sievio_accel.qc_utils`) so additional accelerators can be added later without changing the import pattern.

## 3) Layout guidance (one distribution, one extension module)

Preferred approach: **one accelerator distribution** (e.g., `sievio-accel` / import `sievio_accel`) that ships **one compiled extension module**, with multiple “domains” exposed as Python submodules.

Why:
- Reduces wheel/build matrix complexity and CI time.
- Makes optional install UX simple (`pip install sievio-accel`).
- Avoids multiplying ABI/packaging surfaces per domain.

Implementation shape:
- One `cdylib` extension (e.g., `sievio_accel._native`).
- Thin Python wrappers per domain:
  - `sievio_accel/qc_utils.py` re-exports `sievio_accel._native.qc_*` functions (or similar).
  - Future domains follow the same pattern (`sievio_accel/chunk.py`, `sievio_accel/decode.py`, etc.).

Do **not** create a separate compiled module per Sievio module unless you have a concrete, measured reason (e.g., mutually exclusive heavy dependencies, unusually large compile times, or platform constraints that justify splitting).

## 4) Import shim pattern (core stays authoritative)

In core Python modules (e.g., `src/sievio/core/qc_utils.py`), prefer:

- Try to import accelerated functions behind a single local indirection layer.
- Otherwise use the existing Python implementation.

Guidelines:
- Do not scatter `try/except ImportError` around the file; centralize it near the top or in a small helper module.
- Keep the **Python implementation as the reference** for correctness; Rust must match it.

Optional control knobs (if needed):
- An environment variable to force disable acceleration for debugging (e.g., `SIEVIO_ACCEL=0`).
- A version/feature probe function in `sievio_accel` (e.g., `sievio_accel.features()`).

## 5) Rust/PyO3 engineering rules

- **No panics across the FFI boundary.** Convert errors into Python exceptions (`PyErr`) and return `PyResult`.
- Keep signatures **simple and copy-stable**:
  - Prefer `&str`, `PyBytes`, `Vec<u32>`, `u64`, `usize`, `f64`, and tuples/lists of primitives.
  - Avoid accepting arbitrary Python objects unless absolutely necessary.
- Release the GIL for CPU-bound loops where safe (`Python::allow_threads`), but do not call back into Python while the GIL is released.
- Determinism:
  - Avoid global RNG usage unless the Python side is also deterministic and seed-controlled.
  - Ensure hashing/signature algorithms are stable across runs and platforms (explicit endianness, explicit truncation rules).
- Unsafe code policy:
  - Avoid `unsafe` unless it is demonstrably necessary and isolated, with comments explaining invariants and tests that exercise them.

## 6) Build & test workflow (discover-first, then run)

Do not guess commands if the repo provides them; **read the root docs** and any local README/CI instructions first.

Typical local loop (adjust to the repo’s actual layout):
- From repo root:
  - Create/activate the project venv (this repo uses a venv-based workflow).
  - Install dev deps (and rust tooling if it is modeled as an extra).
  - Run the baseline Python tests **without** the accel package installed.
- From this accel directory:
  - `maturin develop` (debug) or `maturin develop --release` (perf validation).
  - `cargo test` for Rust-unit coverage (if present).
- Back at repo root:
  - Re-run the same Python tests **with** the accel module importable.
  - If parity tests exist, run them explicitly (or add them if missing).

## 7) Review guidelines (P0 / P1)

P0 (explicit scope required; tests must cover):
- Any change to Python-visible behavior (return values, error behavior, thresholds, determinism).
- The import shim / fallback behavior.
- Build system changes (maturin/pyproject/Cargo features) that affect installability.

P1 (risk notes + targeted tests expected):
- Performance-only refactors (hot loops) that could regress edge cases.
- New accelerated domains or expanding the accel API surface.

## 8) What “done” means for an accelerator PR

- Core still passes tests with **no** accel installed.
- Core passes the same tests with accel installed, and the accelerated code path is exercised.
- Parity is demonstrated for representative inputs (including empty/short strings, large inputs, unicode-heavy text, and pathological cases).
- Packaging remains optional and does not introduce mandatory runtime dependencies for core.