"""
Optional e2e tests for marker-pdf.

These tests actually load ML models and convert real PDFs.
They are SKIPPED by default and only run when RIL_MARKER_E2E=1.

Usage:
    RIL_MARKER_E2E=1 python -m pytest tests/e2e/test_marker_pdf.py -v
"""
import os
import pytest
from pathlib import Path


MARKER_E2E = os.getenv("RIL_MARKER_E2E") == "1"

skip_marker = pytest.mark.skipif(
    not MARKER_E2E,
    reason="marker-pdf e2e is slow and requires local ML models. Set RIL_MARKER_E2E=1 to run."
)


@skip_marker
def test_real_marker_pdf_conversion_simple(tmp_path):
    """
    Convert a minimal real PDF using marker-pdf.
    Requires local marker-pdf models and GPU/MPS/CPU.
    """
    from ril.core import convert_pdf_with_marker

    # Create a very simple synthetic PDF (header-only, 1 byte PDF-like)
    # In a real e2e you would use a real fixture PDF.
    fixture_pdf = Path(__file__).parent / "fixtures" / "sample.pdf"
    if not fixture_pdf.exists():
        pytest.skip("No fixture PDF found at tests/e2e/fixtures/sample.pdf")

    markdown, title, images, marker_meta = convert_pdf_with_marker(fixture_pdf)

    assert isinstance(markdown, str)
    assert len(markdown) > 0
    # title may be None or a string
    assert title is None or isinstance(title, str)
    assert isinstance(images, dict)
