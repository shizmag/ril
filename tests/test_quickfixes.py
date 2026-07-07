import pytest
import os
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from PIL import Image
import io

from ril import core, db
from ril.converters import preprocess_html, EPUBConverter, HTMLConverter, MarkdownConverter

def test_md_to_html_fallback_tables():
    md = """
| Col1 | Col2 |
|---|---|
| Val1 | Val2 |
"""
    html = core.md_to_html_fallback(md, "Test Title")
    assert "<table>" in html
    assert "Col1" in html
    assert "Val1" in html

def test_md_to_html_fallback_reference_image_data_uri():
    md = """
![Figure][img_ref_0]

[img_ref_0]: data:image/png;base64,abcdef
"""
    html = core.md_to_html_fallback(md, "Test Title")
    assert "<img" in html
    assert 'src="data:image/png;base64,abcdef"' in html

def test_md_to_html_fallback_fenced_code():
    md = """
```python
def test():
    pass
```
"""
    html = core.md_to_html_fallback(md, "Test Title")
    assert "<pre>" in html
    assert "<code>" in html or "<code" in html
    assert "def test():" in html

@pytest.mark.asyncio
async def test_html_process_url_does_not_render_to_pdf(mocker, setup_test_environment):
    # Mock crawler HTML fetching
    mocker.patch("ril.core.fetch_html", new_callable=AsyncMock, return_value="<html><body><h1>Title</h1><p>Test</p></body></html>")
    
    # Mock readability cleaning
    mocker.patch("ril.core.extract_article", return_value=("Title", "<h1>Title</h1><p>Test</p>"))
    
    # Mock convert_pdf_with_marker to ensure it's NOT called
    mock_marker = mocker.patch("ril.core.convert_pdf_with_marker")
    
    # Mock converter
    mock_converter = MagicMock(spec=MarkdownConverter)
    mock_converter.convert = AsyncMock(return_value="# Title\n\nTest")
    mock_converter.file_extension = ".md"
    
    result = await core.process_url("https://example.com/test-article", converter=mock_converter)
    
    # Assert marker converter is NOT called
    mock_marker.assert_not_called()
    # Assert converter's convert is called with the clean HTML
    mock_converter.convert.assert_called_once()

@pytest.mark.asyncio
async def test_pdf_process_url_still_uses_marker(mocker, setup_test_environment):
    # Mock download_pdf to return a fake file path
    fake_pdf = Path("/fake/path.pdf")
    mocker.patch("ril.core.download_pdf", return_value=fake_pdf)
    mocker.patch("ril.core.is_pdf_file", return_value=True)
    
    # Mock convert_pdf_with_marker to ensure it IS called
    mock_marker = mocker.patch("ril.core.convert_pdf_with_marker", return_value=("# Mock PDF Title\n\nContent.", "Mock PDF Title", {}))
    
    # Mock Path exists/unlink/write_text
    mocker.patch("pathlib.Path.exists", return_value=True)
    mocker.patch("pathlib.Path.unlink", return_value=None)
    
    # Prevent open() from failing or writing to library by mocking builtins.open
    mocker.patch("builtins.open", mocker.mock_open())
    
    result = await core.process_url("https://example.com/paper.pdf")
    
    assert mock_marker.called
    assert result["title"] == "Mock PDF Title"

@pytest.mark.asyncio
async def test_export_rebuilds_when_pipeline_version_changes(setup_test_environment):
    library_dir = setup_test_environment["library_dir"]
    
    # 1. Add mock article to DB
    file_path = library_dir / "test_article.md"
    file_path.write_text("# Test Article\n\nContent")
    
    clean_html_path = library_dir / "test_article.html_clean"
    clean_html_path.write_text("<h1>Test Article</h1><p>Content</p>")
    
    article_id = db.add_article(
        url="https://example.com/test",
        title="Test Article",
        file_path=str(file_path),
        word_count=2,
        char_count=10,
        content="Test Article Content"
    )
    
    # 2. Create existing stale export and a stale meta file
    target_epub = library_dir / "test_article.epub"
    target_epub.write_text("stale epub data")
    
    meta_path = library_dir / "test_article.epub.meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({"export_pipeline_version": "stale-version"}, f)
        
    # 3. Call export_article. Since version is stale, it should rebuild and regenerate the file.
    original_convert = EPUBConverter.convert
    
    async def mock_convert(self, html_content, base_url, article_slug):
        return b"fresh epub data"
        
    EPUBConverter.convert = mock_convert
    try:
        res = await core.export_article(article_id, "epub")
        # Check that target file contains fresh data
        assert target_epub.read_bytes() == b"fresh epub data"
        
        # Check that meta file has the new version
        with open(meta_path, "r", encoding="utf-8") as f:
            meta_data = json.load(f)
            assert meta_data["export_pipeline_version"] == core.EXPORT_PIPELINE_VERSION
    finally:
        EPUBConverter.convert = original_convert

def test_optimize_image_preserves_formula_or_chart_quality():
    img = Image.new("RGBA", (100, 100), color="blue")
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    png_bytes = buffered.getvalue()
    
    converter = HTMLConverter()
    
    for role in ["formula", "math", "chart", "diagram", "table", "screenshot"]:
        res = converter._optimize_image(png_bytes, "image/png", role=role)
        if res:
            optimized_bytes, mime = res
            assert "jpeg" not in mime.lower()
            
    res_text_qual = converter._optimize_image(png_bytes, "image/png", preserve_text_quality=True)
    if res_text_qual:
        _, mime = res_text_qual
        assert "jpeg" not in mime.lower()

def test_preprocess_html_preserves_meaningful_svg():
    # Meaningful SVG (should be preserved)
    meaningful_html = '<div><svg width="200" height="200" role="img"><title>Diagram</title><circle cx="50" cy="50" r="40" /></svg></div>'
    result1 = preprocess_html(meaningful_html)
    assert "<svg" in result1
    assert "Diagram" in result1
    
    # Decorative/tiny SVG (should be removed)
    decorative_html = '<div><svg width="16" height="16" aria-hidden="true"><path d="..." /></svg></div>'
    result2 = preprocess_html(decorative_html)
    assert "<svg" not in result2
