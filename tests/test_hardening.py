import pytest
import zipfile
from pathlib import Path
import io
import json
from unittest.mock import AsyncMock, MagicMock
from bs4 import BeautifulSoup

from ril import core, db
from ril.converters import EPUBConverter, preprocess_html

@pytest.mark.asyncio
async def test_reference_image_data_uri_packaged_as_epub_asset(setup_test_environment):
    # Minimal Markdown with a reference base64 image
    md = """# Test Reference Image

Some text.

![Figure 1][img_ref_0]

[img_ref_0]: data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==
"""
    # Create database entry
    library_dir = setup_test_environment["library_dir"]
    file_path = library_dir / "ref_img_test.md"
    file_path.write_text(md, encoding="utf-8")
    
    article_id = db.add_article(
        url="https://example.com/ref-img-test",
        title="Test Reference Image",
        file_path=str(file_path),
        word_count=4,
        char_count=50,
        content="Test Reference Image content"
    )
    
    # Export to EPUB
    res = await core.export_article(article_id, "epub", force=True)
    epub_path = Path(res["file_path"])
    
    # Open EPUB via zipfile
    assert zipfile.is_zipfile(epub_path)
    with zipfile.ZipFile(epub_path, "r") as z:
        namelist = z.namelist()
        
        # 1. Look for image file inside the ZIP (e.g. OEBPS/images/img_0.png)
        img_files = [name for name in namelist if "OEBPS/images/" in name]
        assert len(img_files) > 0, "No image file found packaged in EPUB"
        
        # 2. Check content.opf contains the manifest item
        opf_data = z.read("OEBPS/content.opf").decode("utf-8")
        assert "OEBPS/images/" not in opf_data  # OPF href is relative to OEBPS, so should be "images/..."
        assert "images/img_" in opf_data
        
        # 3. Check article.xhtml does not contain base64 in src
        xhtml_data = z.read("OEBPS/article.xhtml").decode("utf-8")
        assert "data:image/" not in xhtml_data
        assert "src=\"images/img_" in xhtml_data


@pytest.mark.asyncio
async def test_pdf_detection_head_405_falls_back_to_url_heuristic(mocker):
    # Mock httpx HEAD request to return a 405 response
    mock_resp = MagicMock()
    mock_resp.status_code = 405
    
    # Mock AsyncClient
    mock_client = MagicMock()
    mock_client.head = AsyncMock(return_value=mock_resp)
    mocker.patch("httpx.AsyncClient", return_value=mock_client)
    
    url = "https://example.com/doc.pdf"
    is_pdf = await core.detect_pdf_url_or_content(url)
    assert is_pdf is True


@pytest.mark.asyncio
async def test_pdf_detection_head_403_html_url_does_not_crash(mocker):
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    
    mock_client = MagicMock()
    mock_client.head = AsyncMock(return_value=mock_resp)
    mocker.patch("httpx.AsyncClient", return_value=mock_client)
    
    url = "https://example.com/article.html"
    is_pdf = await core.detect_pdf_url_or_content(url)
    assert is_pdf is False


@pytest.mark.asyncio
async def test_pdf_download_magic_bytes_wins(mocker, setup_test_environment):
    # Mock detect_pdf_url_or_content to say False initially
    mocker.patch("ril.core.detect_pdf_url_or_content", return_value=False)
    
    # URL looks like HTML, but downloaded content is actually PDF
    url = "https://example.com/get-document?id=123"
    
    # Mock download_pdf to write fake %PDF file
    temp_dir = setup_test_environment["temp_dir"]
    fake_pdf_file = temp_dir / "downloaded.pdf"
    fake_pdf_file.write_bytes(b"%PDF-1.4\ncontent")
    
    mocker.patch("ril.core.download_pdf", return_value=fake_pdf_file)
    
    # Mock fetch_html to raise a download trigger (which makes it check download)
    mocker.patch("ril.core.fetch_html", side_effect=Exception("Download is starting"))
    
    # Mock convert_pdf_with_marker
    mock_marker = mocker.patch("ril.core.convert_pdf_with_marker", return_value=("# PDF content", "PDF Title", {}))
    
    res = await core.process_url(url)
    assert res["title"] == "PDF Title"
    assert mock_marker.called


@pytest.mark.asyncio
async def test_pdf_like_url_non_pdf_content_does_not_call_marker(mocker, setup_test_environment):
    url = "https://example.com/not-really-a-pdf.pdf"
    
    # Mock detect_pdf_url_or_content to say True (based on URL)
    mocker.patch("ril.core.detect_pdf_url_or_content", return_value=True)
    
    # Mock download_pdf to write HTML content
    temp_dir = setup_test_environment["temp_dir"]
    fake_pdf_file = temp_dir / "downloaded.pdf"
    fake_pdf_file.write_text("<html><body><h1>Real Title</h1><p>Body</p></body></html>", encoding="utf-8")
    mocker.patch("ril.core.download_pdf", return_value=fake_pdf_file)
    
    # Mock convert_pdf_with_marker (should NOT be called)
    mock_marker = mocker.patch("ril.core.convert_pdf_with_marker")
    
    # Mock readability cleaning
    mocker.patch("ril.core.extract_article", return_value=("Real Title", "<h1>Real Title</h1><p>Body</p>"))
    
    # Mock converter
    from ril.converters import MarkdownConverter
    mock_conv = MagicMock(spec=MarkdownConverter)
    mock_conv.convert = AsyncMock(return_value="# Real Title\n\nBody")
    mock_conv.file_extension = ".md"
    
    res = await core.process_url(url, converter=mock_conv)
    assert res["title"] == "Real Title"
    mock_marker.assert_not_called()


@pytest.mark.asyncio
async def test_html_process_url_preserves_db_and_fts_indexing(mocker, setup_test_environment):
    url = "https://example.com/my-article"
    
    # Mock fetch_html to return article raw HTML
    mocker.patch("ril.core.fetch_html", new_callable=AsyncMock, return_value="<html><body><h1>FTS Article</h1><p>Supercalifragilistic content</p></body></html>")
    
    # Mock readability cleaning
    mocker.patch("ril.core.extract_article", return_value=("FTS Article", "<h1>FTS Article</h1><p>Supercalifragilistic content</p>"))
    
    mock_marker = mocker.patch("ril.core.convert_pdf_with_marker")
    
    # Use real MarkdownConverter to test conversion and saving
    from ril.converters import MarkdownConverter
    converter = MarkdownConverter()
    
    res = await core.process_url(url, converter=converter)
    
    assert res["title"] == "FTS Article"
    assert mock_marker.called is False
    
    # 1. Check file exists in LIBRARY_DIR
    file_path = Path(res["file_path"])
    assert file_path.exists()
    assert "# FTS Article" in file_path.read_text(encoding="utf-8")
    
    # 2. Check DB contains article
    article = db.get_article(res["id"])
    assert article is not None
    assert article["title"] == "FTS Article"
    
    # 3. Check FTS search
    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT article_id FROM articles_fts WHERE content MATCH ?", ("Supercalifragilistic",))
        fts_res = cursor.fetchone()
        assert fts_res is not None
        assert fts_res[0] == res["id"]
        
    # 4. Check export works
    export_res = await core.export_article(res["id"], "epub", force=True)
    assert Path(export_res["file_path"]).exists()


def test_math_placeholders_do_not_modify_fenced_code():
    md = """
```python
pattern = r"$\\d+"
latex = "$not_math$"
```
"""
    html = core.md_to_html_fallback(md, "Title")
    assert 'pattern = r&quot;$\\d+&quot;' in html
    assert 'latex = &quot;$not_math$&quot;' in html
    assert "MATH" not in html


def test_math_placeholders_do_not_modify_inline_code():
    md = "Here is inline code: `price = \"$100\"`."
    html = core.md_to_html_fallback(md, "Title")
    assert "price = \"$100\"" in html
    assert "MATH" not in html


def test_math_placeholders_still_preserve_real_math():
    md = "Normal math: $x^2 + y^2$. Display math: $$e = mc^2$$."
    html = core.md_to_html_fallback(md, "Title")
    assert "$x^2 + y^2$" in html
    assert "$$e = mc^2$$" in html


def test_preprocess_html_sanitizes_meaningful_svg():
    raw_html = """
    <div>
        <svg width="200" height="200" role="img" class="chart">
            <title>My Chart</title>
            <script>alert('dangerous')</script>
            <circle cx="50" cy="50" r="40" onclick="stealCookies()" onload="alert(1)"/>
        </svg>
    </div>
    """
    clean = preprocess_html(raw_html)
    assert "<svg" in clean
    assert "My Chart" in clean
    assert "dangerous" not in clean
    assert "onclick" not in clean
    assert "onload" not in clean


def test_preprocess_html_neutralizes_javascript_in_svg_links():
    raw_html = """
    <div>
        <svg width="200" height="200" role="img">
            <a href="javascript:stealData()" xlink:href="javascript:doBad()">Link</a>
        </svg>
    </div>
    """
    clean = preprocess_html(raw_html)
    assert "javascript:" not in clean
    assert 'href="#"' in clean


def test_preprocess_html_removes_decorative_svg_icon():
    raw_html = """
    <div>
        <svg width="16" height="16" aria-hidden="true" class="icon icon-star">
            <path d="M0 0h16v16H0z"/>
        </svg>
    </div>
    """
    clean = preprocess_html(raw_html)
    assert "<svg" not in clean


def test_preprocess_html_preserves_svg_with_title_desc_viewbox():
    raw_html = """
    <div>
        <svg viewBox="0 0 100 100">
            <title>Diagram Title</title>
            <desc>Diagram Description</desc>
            <rect width="100" height="100"/>
        </svg>
    </div>
    """
    clean = preprocess_html(raw_html)
    assert "<svg" in clean
    assert "Diagram Title" in clean
    assert "Diagram Description" in clean


@pytest.mark.asyncio
async def test_export_article_backward_compatible_without_force(setup_test_environment):
    library_dir = setup_test_environment["library_dir"]
    file_path = library_dir / "compat.md"
    file_path.write_text("# Title\n\nContent", encoding="utf-8")
    
    clean_html_path = library_dir / "compat.html_clean"
    clean_html_path.write_text("<h1>Title</h1><p>Content</p>", encoding="utf-8")
    
    article_id = db.add_article(
        url="https://example.com/compat",
        title="Title",
        file_path=str(file_path),
        word_count=2,
        char_count=10,
        content="Content"
    )
    
    target_path = library_dir / "compat.epub"
    target_path.write_text("fake cached epub", encoding="utf-8")
    
    meta_path = library_dir / "compat.epub.meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({"export_pipeline_version": core.EXPORT_PIPELINE_VERSION}, f)
        
    res = await core.export_article(article_id, "epub")
    assert Path(res["file_path"]).read_text(encoding="utf-8") == "fake cached epub"


@pytest.mark.asyncio
async def test_export_article_force_true_rebuilds(setup_test_environment):
    library_dir = setup_test_environment["library_dir"]
    file_path = library_dir / "force_test.md"
    file_path.write_text("# Force Title\n\nContent", encoding="utf-8")
    
    clean_html_path = library_dir / "force_test.html_clean"
    clean_html_path.write_text("<h1>Force Title</h1><p>Content</p>", encoding="utf-8")
    
    article_id = db.add_article(
        url="https://example.com/force",
        title="Force Title",
        file_path=str(file_path),
        word_count=2,
        char_count=10,
        content="Content"
    )
    
    target_path = library_dir / "force_test.epub"
    target_path.write_text("fake cached epub", encoding="utf-8")
    
    meta_path = library_dir / "force_test.epub.meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({"export_pipeline_version": core.EXPORT_PIPELINE_VERSION}, f)
        
    from ril.converters import EPUBConverter
    original_convert = EPUBConverter.convert
    EPUBConverter.convert = AsyncMock(return_value=b"brand new epub content")
    try:
        res = await core.export_article(article_id, "epub", force=True)
        assert Path(res["file_path"]).read_bytes() == b"brand new epub content"
    finally:
        EPUBConverter.convert = original_convert


@pytest.mark.asyncio
async def test_export_article_stale_meta_rebuilds(setup_test_environment):
    library_dir = setup_test_environment["library_dir"]
    file_path = library_dir / "stale_test.md"
    file_path.write_text("# Stale Title\n\nContent", encoding="utf-8")
    
    clean_html_path = library_dir / "stale_test.html_clean"
    clean_html_path.write_text("<h1>Stale Title</h1><p>Content</p>", encoding="utf-8")
    
    article_id = db.add_article(
        url="https://example.com/stale",
        title="Stale Title",
        file_path=str(file_path),
        word_count=2,
        char_count=10,
        content="Content"
    )
    
    target_path = library_dir / "stale_test.epub"
    target_path.write_text("fake cached epub", encoding="utf-8")
    
    meta_path = library_dir / "stale_test.epub.meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({"export_pipeline_version": "stale-version-xyz"}, f)
        
    from ril.converters import EPUBConverter
    original_convert = EPUBConverter.convert
    EPUBConverter.convert = AsyncMock(return_value=b"regenerated stale epub")
    try:
        res = await core.export_article(article_id, "epub")
        assert Path(res["file_path"]).read_bytes() == b"regenerated stale epub"
    finally:
        EPUBConverter.convert = original_convert


def test_delete_article_removes_export_meta_sidecars(setup_test_environment):
    library_dir = setup_test_environment["library_dir"]
    file_path = library_dir / "del_test.md"
    file_path.write_text("# Del Title\n\nContent", encoding="utf-8")
    
    article_id = db.add_article(
        url="https://example.com/del",
        title="Del Title",
        file_path=str(file_path),
        word_count=2,
        char_count=10,
        content="Content"
    )
    
    epub_path = library_dir / "del_test.epub"
    epub_path.write_text("epub", encoding="utf-8")
    epub_meta = library_dir / "del_test.epub.meta.json"
    epub_meta.write_text("{}", encoding="utf-8")
    
    html_path = library_dir / "del_test.html"
    html_path.write_text("html", encoding="utf-8")
    html_meta = library_dir / "del_test.html.meta.json"
    html_meta.write_text("{}", encoding="utf-8")
    
    deleted = core.delete_article(article_id)
    assert deleted is True
    
    assert not file_path.exists()
    assert not epub_path.exists()
    assert not epub_meta.exists()
    assert not html_path.exists()
    assert not html_meta.exists()
