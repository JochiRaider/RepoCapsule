import hashlib

import pytest

from sievio.core import qc_utils
from sievio.core.qc_utils import (
    MinHashLSH,
    minhash_signature_for_text,
    parse_ok,
    repetition_rate,
    simhash64,
)


def test_parse_ok_rejects_partial_json():
    assert parse_ok('{"a": 1', "json") == 0.0
    assert parse_ok('{"a": 1}\n{invalid', "jsonl") == 0.0


def test_parse_ok_handles_invalid_yaml():
    try:
        import yaml  # noqa: F401
    except Exception:
        pytest.skip("yaml not available")

    assert parse_ok("key: : value\n -", "yaml") == 0.0


def test_parse_ok_handles_malformed_restructuredtext_or_markdown():
    # Very short or heading-free text should fall back to a reduced confidence score.
    assert parse_ok("nonsense content", "markdown") < 1.0
    assert parse_ok("title\n---\nbody", "restructuredtext") == pytest.approx(1.0)


def test_simhash64_single_token_matches_hash():
    text = "Token"
    expected = int.from_bytes(
        hashlib.blake2b(text.lower().encode("utf-8"), digest_size=8).digest(),
        "little",
    )
    assert simhash64(text) == expected


def test_minhash_lsh_rejects_invalid_params_and_signatures():
    with pytest.raises(ValueError):
        MinHashLSH(n_perm=0)
    with pytest.raises(ValueError):
        MinHashLSH(n_perm=10, bands=3)
    with pytest.raises(ValueError):
        MinHashLSH(n_perm=10, bands=5, jaccard_threshold=1.5)

    lsh = MinHashLSH(n_perm=8, bands=2)
    with pytest.raises(ValueError):
        lsh.add_and_check("doc", (1, 2, 3))


def test_repetition_rate_caps_grams_deterministically():
    text = "abcd " * 50
    k = 4
    max_grams = 10
    truncated = text[: max_grams + k - 1]
    assert repetition_rate(text, k=k, max_grams=max_grams) == pytest.approx(
        repetition_rate(truncated, k=k, max_grams=None)
    )


def test_minhash_signature_supports_large_perm_counts_deterministically():
    text = "abcdefg " * 200
    sig = minhash_signature_for_text(text, k=5, n_perm=256)
    assert len(sig) == 256
    assert any(val != 0xFFFFFFFF for val in sig[128:])
    assert sig == minhash_signature_for_text(text, k=5, n_perm=256)
    assert sig[:128] == minhash_signature_for_text(text, k=5, n_perm=128)


def test_minhash_signature_caps_shingles_consistently():
    text = "abcd " * 200
    k = 4
    max_shingles = 20
    truncated = text[: max_shingles + k - 1]
    assert minhash_signature_for_text(
        text,
        k=k,
        n_perm=32,
        max_shingles=max_shingles,
    ) == minhash_signature_for_text(truncated, k=k, n_perm=32, max_shingles=None)


def test_parse_ok_respects_max_bytes():
    text = '{"a": 1}\n' * 100
    assert parse_ok(text, "jsonl", max_bytes=10) == 0.0


def test_minhash_rejects_excessive_perm_counts():
    text = "abcdefg " * 10
    with pytest.raises(ValueError):
        minhash_signature_for_text(text, k=5, n_perm=qc_utils._MINHASH_MAX_PERMS + 1)
    with pytest.raises(ValueError):
        MinHashLSH(n_perm=qc_utils._MINHASH_MAX_PERMS + 1, bands=1)


def test_perplexity_model_fail_soft_when_loading_fails(monkeypatch):
    class BoomTokenizer:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            raise OSError("missing model")

    monkeypatch.setattr(qc_utils, "_ensure_hf", lambda: True)
    monkeypatch.setattr(qc_utils, "AutoTokenizer", BoomTokenizer)
    model = qc_utils.PerplexityModel("missing", local_files_only=True)
    assert model.model is None
    assert model.tok is None
    assert model.ppl("hello") == float("inf")
