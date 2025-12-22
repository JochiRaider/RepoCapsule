import pytest

from sievio.core import accel as accel_utils
from sievio.core import qc_utils

pytest.importorskip("sievio_accel")


def test_simhash64_parity_with_accel(monkeypatch):
    text = "Token"
    accel_utils._reset_accel_cache_for_tests()
    monkeypatch.setenv("SIEVIO_ACCEL", "0")
    python_val = qc_utils.simhash64(text)

    accel_utils._reset_accel_cache_for_tests()
    monkeypatch.setenv("SIEVIO_ACCEL", "1")
    accel_val = qc_utils.simhash64(text)
    assert accel_val == python_val


def test_minhash_parity_with_accel(monkeypatch):
    text = "abcdefg " * 200
    accel_utils._reset_accel_cache_for_tests()
    monkeypatch.setenv("SIEVIO_ACCEL", "0")
    python_sig = qc_utils.minhash_signature_for_text(text, k=5, n_perm=128)

    accel_utils._reset_accel_cache_for_tests()
    monkeypatch.setenv("SIEVIO_ACCEL", "1")
    accel_sig = qc_utils.minhash_signature_for_text(text, k=5, n_perm=128)
    assert accel_sig == python_sig
