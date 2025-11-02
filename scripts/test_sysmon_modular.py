"""Minimal driver to exercise repocapsule on sysmon-modular.

Run from VS Code (or `python scripts/test_sysmon_modular.py`). No CLI needed.
- Streams the GitHub zipball (RAM-safe)
- Writes both JSONL and prompt text in a single pass
- Optionally runs light QC scoring if available
"""
from __future__ import annotations

# put these 3 lines at the very top
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))


from pathlib import Path
from typing import Optional, Set
import os

from repocapsule.log import configure_logging

# Prefer streaming implementations if present
try:  # pragma: no cover
    from repocapsule.convert_streaming import (
        convert_github_url_to_jsonl_autoname,
    )
except Exception:  # fallback to non-streaming convert (still works)
    from repocapsule.convert import (
        convert_github_url_to_jsonl_autoname,
    )


URL = "https://github.com/olafhartong/sysmon-modular"
OUT_DIR = Path("out")  # change if you want an absolute path

# Focus the crawl to formats used by sysmon-modular (plus a few helpful docs)
INCLUDE_EXTS: Optional[Set[str]] = {
    ".yml", ".yaml",  # Sysmon rules/config
    ".xml",            # Sysmon XML
    ".md",             # README / docs
    ".txt",
}


def main() -> None:
    # Optional: set a token to avoid low-rate limits
    # os.environ.setdefault("GITHUB_TOKEN", "<your token>")

    log = configure_logging(level="INFO")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    log.info("Converting repo → JSONL + prompt: %s", URL)

    jsonl_path, prompt_path = convert_github_url_to_jsonl_autoname(
        URL,
        OUT_DIR,
        include_exts=INCLUDE_EXTS,
        also_prompt_text=True,      # stream-writes both in one pass
        kql_from_markdown=False,    # not needed for this repo
    )

    print("\n=== Done ===")
    print("JSONL :", jsonl_path)
    print("Prompt:", prompt_path)

    # Optional: run QC (lightweight heuristics; heavy deps are optional)
    try:
        from repocapsule.qc import score_jsonl_to_csv
        qc_csv = score_jsonl_to_csv(jsonl_path, lm_model_id=None)  # set a model id if you want perplexity
        print("QC CSV:", qc_csv)
    except Exception as e:  # pragma: no cover
        print("QC skipped:", e)


if __name__ == "__main__":
    main()
