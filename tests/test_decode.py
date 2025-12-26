from pathlib import Path

from sievio.core.decode import (
    _maybe_repair_cp1252_utf8,
    decode_bytes,
    read_decoded_text,
    read_text,
)


def test_decode_utf8_happy_path() -> None:
    original = "Hello π – café"
    data = original.encode("utf-8")

    dec = decode_bytes(data)

    assert dec.text == original
    assert dec.encoding.lower().startswith("utf-8")
    assert dec.had_replacement is False


def test_decode_handles_utf8_bom() -> None:
    data = b"\xef\xbb\xbf" + b"Hi"

    dec = decode_bytes(data)

    assert dec.text == "Hi"
    assert dec.encoding.startswith("utf-8")
    assert dec.had_replacement is False


def test_decode_utf16_bom_is_stripped() -> None:
    data = "\ufeffHello".encode("utf-16")

    dec = decode_bytes(data)

    assert dec.text == "Hello"
    assert dec.encoding.startswith("utf-16")
    assert dec.had_replacement is False


def test_decode_utf32_bom_is_stripped() -> None:
    data = "Hello".encode("utf-32")

    dec = decode_bytes(data)

    assert dec.text == "Hello"
    assert dec.encoding.startswith("utf-32")
    assert dec.had_replacement is False


def test_decode_utf16_heuristic_without_bom() -> None:
    raw = "Hello world".encode("utf-16-le")

    dec = decode_bytes(raw)

    assert dec.text == "Hello world"
    assert dec.encoding in {"utf-16-le", "utf-8"}
    assert dec.had_replacement is False
    assert "\x00" not in dec.text


def test_decode_cp1252_fallback_repairs_text() -> None:
    data = "François".encode("cp1252")

    dec = decode_bytes(data, fix_mojibake=True)

    assert dec.encoding == "cp1252"
    assert dec.text == "François"
    assert dec.had_replacement is False
    assert dec.provenance.mojibake_repaired is False


def test_decode_latin1_fallback_marks_encoding() -> None:
    data = b"\x81\x8d\xfa"

    dec = decode_bytes(data, fix_mojibake=False)

    assert dec.encoding == "latin-1"
    assert dec.text == "ú"
    assert dec.had_replacement is False


def test_decode_normalizes_newlines_and_strips_controls() -> None:
    text = "line1\r\nline2\rline3\u200b\t\x01end"
    data = text.encode("utf-8")

    dec = decode_bytes(data)

    assert dec.text == "line1\nline2\nline3\tend"
    assert "\r" not in dec.text
    assert "\u200b" not in dec.text
    assert "\x01" not in dec.text
    assert "\t" in dec.text
    assert dec.had_replacement is False
    assert dec.provenance.newlines_normalized is True
    assert dec.provenance.controls_stripped > 0
    assert dec.provenance.lossy is True
    assert dec.provenance.changed is True


def test_mojibake_repair_accepts_obvious_utf8_misdecode() -> None:
    assert _maybe_repair_cp1252_utf8("FranÃ§ois") == "François"


def test_mojibake_repair_rejects_non_cp1252_text() -> None:
    text = "A\u0100 Ã©"

    assert _maybe_repair_cp1252_utf8(text) == text


def test_decode_mojibake_provenance_flags() -> None:
    data = "FranÃ§ois".encode("cp1252")

    dec = decode_bytes(data, fix_mojibake=True)

    assert dec.text == "François"
    assert dec.provenance.mojibake_repaired is False
    assert dec.provenance.changed is False
    assert dec.had_replacement is False


def test_decode_literal_replacement_char_does_not_set_flag() -> None:
    text = "hello \ufffd world"
    data = text.encode("utf-8")

    dec = decode_bytes(data)

    assert dec.text == text
    assert dec.had_replacement is False
    assert dec.provenance.decode_replacements is False


def test_decode_unicode_normalization_flags() -> None:
    text = "Cafe\u0301"
    data = text.encode("utf-8")

    dec = decode_bytes(data, normalize="NFC")

    assert dec.text == "Café"
    assert dec.provenance.unicode_normalized is True
    assert dec.provenance.changed is True
    assert dec.had_replacement is False


def test_read_text_full_and_truncated(tmp_path: Path) -> None:
    full_path = tmp_path / "sample.txt"
    full_path.write_bytes(b"Hello\nworld")

    assert read_text(full_path) == "Hello\nworld"

    capped = tmp_path / "big.txt"
    capped.write_bytes(("A" * 10_000).encode("utf-8"))

    out = read_text(capped, max_bytes=100)

    assert out
    assert len(out.encode("utf-8")) <= 100
    assert "\r" not in out
    assert "\x00" not in out


def test_read_text_zero_max_bytes_reads_nothing(tmp_path: Path) -> None:
    path = tmp_path / "empty.txt"
    path.write_bytes(b"Hello")

    out = read_text(path, max_bytes=0)

    assert out == ""


def test_read_text_with_opener_uses_stream(tmp_path: Path) -> None:
    data_path = tmp_path / "data.txt"
    data_path.write_bytes(b"Opener works")

    def _opener():
        return data_path.open("rb")

    out = read_text(tmp_path / "missing.txt", opener=_opener)

    assert out == "Opener works"


def test_read_text_accepts_bytes_path(tmp_path: Path) -> None:
    path = tmp_path / "bytes.txt"
    path.write_bytes(b"Bytes path")

    out = read_text(path.as_posix().encode("utf-8"))

    assert out == "Bytes path"


def test_read_decoded_text_returns_provenance(tmp_path: Path) -> None:
    path = tmp_path / "prov.txt"
    path.write_bytes(b"Hello\r\nworld")

    dec = read_decoded_text(path)

    assert dec is not None
    assert dec.text == "Hello\nworld"
    assert dec.provenance.newlines_normalized is True
