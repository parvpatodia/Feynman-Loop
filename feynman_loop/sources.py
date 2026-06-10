"""Turn a source file into plain text for ingestion.

Supports .txt and .pdf. PDF extraction handles text-based PDFs; scanned/image-only PDFs would
need OCR, which is out of scope for v1 (we raise a clear error instead of returning empty text).
The extracted text then flows through the same chunk -> embed -> judge pipeline as pasted text.
"""

from __future__ import annotations

import io


def extract_text(*, filename: str, data: bytes) -> str:
    """Extract plain text from an uploaded source, dispatching by file extension."""
    if filename.lower().endswith(".pdf"):
        return _extract_pdf(data)
    # WHY: errors="replace" so an odd byte never crashes ingestion; the judge tolerates noise.
    return data.decode("utf-8", errors="replace")


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader  # WHY: lazy import so the non-PDF path pays no import cost

    reader = PdfReader(io.BytesIO(data))
    text = "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()
    if not text:
        raise ValueError(
            "No extractable text found in the PDF. It may be scanned or image-only; "
            "OCR is not supported in v1. Try a text-based PDF or paste the text directly."
        )
    return text


def load_source(path: str) -> str:
    """Read a file from disk and extract its text (.txt or .pdf)."""
    with open(path, "rb") as f:
        return extract_text(filename=path, data=f.read())
