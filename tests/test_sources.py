import pytest

from feynman_loop import sources


def test_txt_is_decoded():
    assert sources.extract_text(filename="notes.txt", data=b"hello world") == "hello world"


def test_pdf_extension_routes_to_pdf_extractor(monkeypatch):
    monkeypatch.setattr(sources, "_extract_pdf", lambda data: "EXTRACTED")
    # case-insensitive on the extension
    assert sources.extract_text(filename="paper.PDF", data=b"%PDF-1.4...") == "EXTRACTED"


def test_pdf_with_no_text_raises(monkeypatch):
    # a scanned / image-only PDF yields no extractable text -> clear error, not empty string
    class _FakePage:
        def extract_text(self):
            return ""

    class _FakeReader:
        def __init__(self, *a, **k):
            self.pages = [_FakePage()]

    monkeypatch.setattr("pypdf.PdfReader", _FakeReader)
    with pytest.raises(ValueError):
        sources._extract_pdf(b"%PDF-fake")
