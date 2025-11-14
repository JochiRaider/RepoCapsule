from __future__ import annotations

import posixpath

import pytest

from repocapsule.sources_webpdf import WebPdfListSource, WebPagePdfSource


def test_webpage_pdf_source_filters_and_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    html = """
    <html>
        <head><base href="/docs/"></head>
        <body>
            <a href="a.pdf">first</a>
            <a href="sub/b.pdf">second</a>
            <a href="https://other.example.com/c.pdf">skip cross-domain</a>
            <a href="notes.txt">skip ambiguous</a>
        </body>
    </html>
    """
    monkeypatch.setattr(WebPagePdfSource, "_fetch_html", lambda self: html)

    requested: list[str] = []

    def fake_download(self: WebPdfListSource, url: str):
        requested.append(url)
        data = b"%PDF-1.4 stub"
        headers = {"Content-Disposition": f'attachment; filename="{posixpath.basename(url)}"'}
        headers["_X-SNIFF"] = data[:8].hex()
        return data, headers

    monkeypatch.setattr(WebPdfListSource, "_download", fake_download)

    source = WebPagePdfSource(
        "https://example.com/start/index.html",
        include_ambiguous=False,
        same_domain=True,
        add_prefix="webdocs",
        max_links=10,
    )

    items = list(source.iter_files())

    expected_urls = [
        "https://example.com/docs/a.pdf",
        "https://example.com/docs/sub/b.pdf",
    ]

    assert requested == expected_urls
    assert len(items) == len(expected_urls)
    assert [item.path for item in items] == ["webdocs/a.pdf", "webdocs/b.pdf"]
