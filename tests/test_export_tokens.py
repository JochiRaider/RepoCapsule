from __future__ import annotations

import json

from repocapsule.export import annotate_exact_token_counts


class StubTokenizer:
    def encode(self, text: str, disallowed_special=()):
        # Simple whitespace tokenizer for testing purposes.
        return [tok for tok in text.split(" ") if tok]


def test_annotate_exact_token_counts_updates_records(tmp_path):
    path = tmp_path / "dataset.jsonl"
    records = [
        {"text": "hello world", "meta": {"approx_tokens": 5}},
        {"text": "", "meta": {"kind": "qc_summary", "mode": "inline"}, "qc": {"scored": 0}},
    ]
    path.write_text("\n".join(json.dumps(rec) for rec in records) + "\n", encoding="utf-8")

    annotate_exact_token_counts(path, tokenizer=StubTokenizer())

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    first = lines[0]
    assert first["meta"]["approx_tokens"] == 5
    assert first["meta"]["token_count"] == 2  # exact tokens from stub
    summary = lines[1]
    assert summary["meta"]["kind"] == "qc_summary"
    assert "token_count" not in summary.get("meta", {})
