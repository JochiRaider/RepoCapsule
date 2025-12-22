import hashlib
import sys
import types

import pytest

from sievio.core import accel as accel_utils
from sievio.core import qc_utils


def _install_dummy_accel(monkeypatch, *, simhash_value=12345, minhash_value=(1, 2, 3)):
    pkg = types.ModuleType("sievio_accel")
    pkg.__path__ = []
    mod = types.ModuleType("sievio_accel.qc")
    mod.simhash64 = lambda text, max_tokens=None: simhash_value
    mod.minhash_signature_with_coeffs = lambda *args, **kwargs: minhash_value
    mod.minhash_signature_for_text = lambda *args, **kwargs: minhash_value
    monkeypatch.setitem(sys.modules, "sievio_accel", pkg)
    monkeypatch.setitem(sys.modules, "sievio_accel.qc", mod)


def test_accel_shim_prefers_accel_when_available(monkeypatch):
    accel_utils._reset_accel_cache_for_tests()
    monkeypatch.setenv("SIEVIO_ACCEL", "1")
    _install_dummy_accel(monkeypatch, simhash_value=777, minhash_value=(9, 8, 7))

    assert qc_utils.simhash64("hello") == 777
    sig = qc_utils.minhash_signature_for_text("hello world", k=5, n_perm=3)
    assert sig == (9, 8, 7)


def test_accel_shim_respects_disable_env(monkeypatch):
    accel_utils._reset_accel_cache_for_tests()
    monkeypatch.setenv("SIEVIO_ACCEL", "0")
    _install_dummy_accel(monkeypatch, simhash_value=777, minhash_value=(9, 8, 7))

    text = "Token"
    expected = int.from_bytes(
        hashlib.blake2b(text.lower().encode("utf-8"), digest_size=8).digest(),
        "little",
    )
    assert qc_utils.simhash64(text) == expected

    minhash_text = "abcd " * 50
    expected_sig = qc_utils._minhash_signature(
        qc_utils._shingle_hashes(minhash_text, k=4, max_shingles=None),
        n_perm=3,
    )
    assert qc_utils.minhash_signature_for_text(minhash_text, k=4, n_perm=3) == expected_sig


def test_accel_shim_require_raises_when_missing(monkeypatch):
    accel_utils._reset_accel_cache_for_tests()
    monkeypatch.setenv("SIEVIO_ACCEL", "require")
    monkeypatch.delitem(sys.modules, "sievio_accel", raising=False)
    monkeypatch.delitem(sys.modules, "sievio_accel.qc", raising=False)
    monkeypatch.setattr(
        accel_utils.importlib,
        "import_module",
        lambda _: (_ for _ in ()).throw(ModuleNotFoundError("sievio_accel.qc")),
    )

    with pytest.raises(RuntimeError):
        qc_utils.simhash64("hello")
