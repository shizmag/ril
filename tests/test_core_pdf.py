"""
Fast unit/integration tests for the PDF pipeline in ril.core.

These tests NEVER load real marker-pdf models.
marker-pdf e2e is handled in tests/e2e/test_marker_pdf.py (RIL_MARKER_E2E=1).
"""
import os
import io
import re
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

import ril.core as core
from ril import db, config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_convert(path, markdown="# Fake Title\n\nBody text.", title="Fake Title", images=None):
    """Factory returning a fake convert_pdf_with_marker that also asserts on the path."""
    def _impl(pdf_path):
        assert pdf_path == path, f"Expected path {path}, got {pdf_path}"
        return (markdown, title, images or {})
    return _impl


# ---------------------------------------------------------------------------
# 1. URL detection
# ---------------------------------------------------------------------------

class TestPdfUrlDetection:
    """Tests for is_pdf detection in process_url."""

    def _is_pdf_url(self, url: str) -> bool:
        url_lower = url.lower()
        return url_lower.split("?")[0].endswith(".pdf") or "/pdf/" in url_lower

    def test_simple_pdf_extension(self):
        assert self._is_pdf_url("https://example.com/file.pdf") is True

    def test_pdf_with_query_string(self):
        assert self._is_pdf_url("https://example.com/file.pdf?download=1") is True

    def test_pdf_in_path_segment(self):
        assert self._is_pdf_url("https://arxiv.org/pdf/2301.12345") is True

    def test_non_pdf_url(self):
        assert self._is_pdf_url("https://example.com/article") is False

    def test_html_page_url(self):
        assert self._is_pdf_url("https://example.com/page.html") is False

    def test_pdf_case_insensitive(self):
        assert self._is_pdf_url("https://example.com/report.PDF") is True


# ---------------------------------------------------------------------------
# 2. download_pdf safety
# ---------------------------------------------------------------------------

class TestDownloadPdf:
    def test_uses_mkstemp_not_mktemp(self, tmp_path):
        """Ensure download_pdf uses mkstemp (no race-condition temp file naming)."""
        import urllib.request

        fake_content = b"%PDF-1.4 fake content"

        class _FakeResponse:
            def __init__(self):
                self.bio = io.BytesIO(fake_content)
            def __enter__(self):
                return self.bio
            def __exit__(self, *a):
                return False

        with patch("urllib.request.urlopen", return_value=_FakeResponse()):
            result = core.download_pdf("https://example.com/test.pdf")

        assert isinstance(result, Path)
        assert result.suffix == ".pdf"
        # File should exist after download
        assert result.exists()
        assert result.read_bytes() == fake_content
        # Cleanup
        result.unlink(missing_ok=True)

    def test_raises_on_network_error(self):
        with patch("urllib.request.urlopen", side_effect=OSError("network error")):
            with pytest.raises(OSError, match="network error"):
                core.download_pdf("https://example.com/test.pdf")


# ---------------------------------------------------------------------------
# 3. process_url PDF — success path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_pdf_success_with_mock_marker(monkeypatch, setup_test_environment):
    """End-to-end PDF orchestration with fully mocked marker-pdf."""
    tmp_path = setup_test_environment["temp_dir"]
    fake_pdf = tmp_path / "sample.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(core, "download_pdf", lambda url: fake_pdf)
    monkeypatch.setattr(
        core,
        "convert_pdf_with_marker",
        _fake_convert(
            fake_pdf,
            markdown="# Mock PDF Title\n\nThis is extracted PDF text.",
            title="Mock PDF Title",
        ),
    )

    result = await core.process_url("https://example.com/sample.pdf")

    # Basic result shape
    assert result["title"] == "Mock PDF Title"
    assert result["url"] == "https://example.com/sample.pdf"
    assert result["status"] == "unread"
    assert result["word_count"] > 0

    # Markdown file saved
    file_path = Path(result["file_path"])
    assert file_path.exists()
    content = file_path.read_text(encoding="utf-8")
    assert "Mock PDF Title" in content

    # DB record
    record = db.get_article(result["id"])
    assert record is not None
    assert record["title"] == "Mock PDF Title"


# ---------------------------------------------------------------------------
# 4. Image stripping when DISABLE_IMAGES=True
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_pdf_strips_images_when_disabled(monkeypatch, setup_test_environment):
    tmp_path = setup_test_environment["temp_dir"]
    fake_pdf = tmp_path / "sample.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    # Marker returns markdown with an image reference
    md_with_image = "# Title\n\nText.\n\n![fig](figure-0.png)"
    from PIL import Image as PILImage
    mock_img = PILImage.new("RGB", (50, 50), color="red")
    images = {"figure-0.png": mock_img}

    monkeypatch.setattr(core, "download_pdf", lambda url: fake_pdf)
    monkeypatch.setattr(
        core,
        "convert_pdf_with_marker",
        lambda p: (md_with_image, "Title", images),
    )
    monkeypatch.setattr(config, "DISABLE_IMAGES", True)

    result = await core.process_url("https://example.com/doc.pdf")

    content = Path(result["file_path"]).read_text(encoding="utf-8")
    assert "figure-0.png" not in content
    assert "data:image/" not in content
    assert "img_ref_" not in content


# ---------------------------------------------------------------------------
# 5. Image embedding when DISABLE_IMAGES=False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_pdf_embeds_images_when_enabled(monkeypatch, setup_test_environment):
    tmp_path = setup_test_environment["temp_dir"]
    fake_pdf = tmp_path / "sample.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    md_with_image = "# Title\n\nText.\n\n![fig](figure-0.jpg)"
    from PIL import Image as PILImage
    mock_img = PILImage.new("RGB", (50, 50), color="blue")
    images = {"figure-0.jpg": mock_img}

    monkeypatch.setattr(core, "download_pdf", lambda url: fake_pdf)
    monkeypatch.setattr(
        core,
        "convert_pdf_with_marker",
        lambda p: (md_with_image, "Title", images),
    )
    monkeypatch.setattr(config, "DISABLE_IMAGES", False)

    result = await core.process_url("https://example.com/doc.pdf")

    content = Path(result["file_path"]).read_text(encoding="utf-8")
    assert "[img_ref_0]: data:image/" in content


# ---------------------------------------------------------------------------
# 6. Temp PDF cleanup on success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_pdf_cleanup_on_success(monkeypatch, setup_test_environment, tmp_path):
    fake_pdf = tmp_path / "sample.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(core, "download_pdf", lambda url: fake_pdf)
    monkeypatch.setattr(
        core,
        "convert_pdf_with_marker",
        lambda p: ("# Title\n\nBody.", "Title", {}),
    )

    assert fake_pdf.exists()
    await core.process_url("https://example.com/cleanup.pdf")
    # Temp file must be deleted after processing
    assert not fake_pdf.exists()


# ---------------------------------------------------------------------------
# 7. Temp PDF cleanup on marker error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_pdf_cleanup_on_marker_error(monkeypatch, setup_test_environment, tmp_path):
    fake_pdf = tmp_path / "error.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(core, "download_pdf", lambda url: fake_pdf)

    def _raise(p):
        raise RuntimeError("marker exploded")

    monkeypatch.setattr(core, "convert_pdf_with_marker", _raise)

    with pytest.raises(RuntimeError, match="marker exploded"):
        await core.process_url("https://example.com/error.pdf")

    # Temp file must still be cleaned up
    assert not fake_pdf.exists()


# ---------------------------------------------------------------------------
# 8. Duplicate URL without force
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_pdf_duplicate_url_without_force(monkeypatch, setup_test_environment, tmp_path):
    fake_pdf = tmp_path / "dup.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    def _make_fake_pdf(url):
        p = tmp_path / "dup.pdf"
        p.write_bytes(b"%PDF-1.4 fake")
        return p

    monkeypatch.setattr(core, "download_pdf", _make_fake_pdf)
    monkeypatch.setattr(
        core,
        "convert_pdf_with_marker",
        lambda p: ("# Doc\n\nBody.", "Doc", {}),
    )

    url = "https://example.com/dup.pdf"
    await core.process_url(url)

    with pytest.raises(ValueError, match="already exists in library"):
        await core.process_url(url)


# ---------------------------------------------------------------------------
# 9. Force update replaces existing record
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_pdf_force_updates_existing(monkeypatch, setup_test_environment, tmp_path):
    def _make_fake_pdf(url):
        p = tmp_path / "force.pdf"
        p.write_bytes(b"%PDF-1.4 fake")
        return p

    monkeypatch.setattr(core, "download_pdf", _make_fake_pdf)
    monkeypatch.setattr(
        core,
        "convert_pdf_with_marker",
        lambda p: ("# Doc\n\nBody.", "Doc", {}),
    )

    url = "https://example.com/force.pdf"
    res1 = await core.process_url(url)

    # Force update with different title
    monkeypatch.setattr(
        core,
        "convert_pdf_with_marker",
        lambda p: ("# Updated Doc\n\nNew body.", "Updated Doc", {}),
    )
    res2 = await core.process_url(url, force=True)

    # Same DB id, updated title
    assert res2["id"] == res1["id"]
    record = db.get_article(res2["id"])
    assert record["title"] == "Updated Doc"


# ---------------------------------------------------------------------------
# 10. Title fallback from URL when marker returns None title
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_pdf_title_fallback_from_url(monkeypatch, setup_test_environment, tmp_path):
    fake_pdf = tmp_path / "my_report.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(core, "download_pdf", lambda url: fake_pdf)
    monkeypatch.setattr(
        core,
        "convert_pdf_with_marker",
        lambda p: ("# \n\nBody text.", None, {}),  # title=None
    )

    result = await core.process_url("https://example.com/my_report.pdf")
    # Title should be derived from URL filename
    assert result["title"] != "" and result["title"] is not None
    assert "PDF Document" in result["title"] or "my" in result["title"].lower() or "report" in result["title"].lower()
