from __future__ import annotations

import pytest

from repocapsule.sources_webpdf import WebPdfListSource


URLS = ["https://example.com/a.pdf", "https://example.com/b.PDF"]
PDF_MAGIC = b"%PDF-1.7"


def test_webpdf_list_source_serial_and_deduplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        URLS[0]: (PDF_MAGIC + b" A", {"Content-Disposition": 'attachment; filename="report.pdf"'}),
        URLS[1]: (PDF_MAGIC + b" B", {"Content-Disposition": 'attachment; filename="report.pdf"'}),
    }
    call_order: list[str] = []

    def fake_download(self: WebPdfListSource, url: str):
        call_order.append(url)
        data, headers = responses[url]
        hdrs = dict(headers)
        hdrs.setdefault("_X-SNIFF", data[:8].hex())
        return data, hdrs

    monkeypatch.setattr(WebPdfListSource, "_download", fake_download)

    src = WebPdfListSource(URLS, add_prefix="docs")
    items = list(src.iter_files())

    assert call_order == URLS  # serial iteration preserves input order
    assert len(items) == 2
    assert [item.path for item in items] == ["docs/report.pdf", "docs/report__1.pdf"]
    assert all(item.path.lower().endswith(".pdf") for item in items)
    assert [item.data for item in items] == [responses[URLS[0]][0], responses[URLS[1]][0]]


def test_webpdf_list_source_respects_require_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        URLS[0]: (
            b"%PDX-0.0 not a pdf",
            {"Content-Disposition": 'attachment; filename="keep.pdf"', "_X-SNIFF": b"%PDX-0.".hex()},
        ),
        URLS[1]: (
            PDF_MAGIC + b" real",
            {"Content-Disposition": 'attachment; filename="keep.pdf"'},
        ),
    }
    attempted: list[str] = []

    def fake_download(self: WebPdfListSource, url: str):
        attempted.append(url)
        data, headers = responses[url]
        return data, dict(headers)

    monkeypatch.setattr(WebPdfListSource, "_download", fake_download)

    src = WebPdfListSource(URLS, require_pdf=True)
    items = list(src.iter_files())

    assert attempted == URLS
    assert len(items) == 1
    assert items[0].path == "keep.pdf"  # Content-Disposition-derived, with .pdf enforced
    assert items[0].data == responses[URLS[1]][0]
