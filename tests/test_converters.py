import pytest
import os
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
from ril.converters import MarkdownConverter

@pytest.mark.asyncio
async def test_markdown_converter_basic(setup_test_environment):
    converter = MarkdownConverter()
    assert converter.file_extension == ".md"
    
    html = "<h1>Heading 1</h1><p>Paragraph text.</p>"
    result = await converter.convert(html, "https://example.com", "test-basic")
    
    assert "# Heading 1" in result
    assert "Paragraph text." in result

@pytest.mark.asyncio
async def test_markdown_converter_images(mocker, setup_test_environment):
    converter = MarkdownConverter()
    library_dir = setup_test_environment["library_dir"]
    
    # Mock httpx.AsyncClient.get response
    mock_response = MagicMock()
    mock_response.content = b"fake-png-image-bytes"
    mock_response.headers = {"content-type": "image/png"}
    mock_response.raise_for_status = MagicMock()
    
    mocker.patch("httpx.AsyncClient.get", return_value=mock_response)
    
    # HTML with an external image and an inline base64 image
    html = (
        "<div>"
        "  <p>Here is an image:</p>"
        '  <img src="https://example.com/assets/logo.png" alt="Logo" />'
        "  <p>And a base64 image:</p>"
        '  <img src="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=" alt="Base64" />'
        "</div>"
    )
    
    markdown_result = await converter.convert(html, "https://example.com/page", "test-article")
    
    # 1. Assert markdown format conversions
    assert "Here is an image:" in markdown_result
    
    # 2. Assert relative image links are present in Markdown
    # E.g. ![Logo](images/test-article/xxxx.png)
    assert "images/test-article/" in markdown_result
    assert ".png" in markdown_result
    assert ".jpeg" in markdown_result
    
    # 3. Assert files are written to disk in library/images/test-article
    img_dir = library_dir / "images" / "test-article"
    assert img_dir.exists()
    
    # We should have two image files
    files = list(img_dir.glob("*"))
    assert len(files) == 2
    
    # Verify file content
    png_files = [f for f in files if f.suffix == ".png"]
    assert len(png_files) == 1
    with open(png_files[0], "rb") as f:
        assert f.read() == b"fake-png-image-bytes"
        
    jpeg_files = [f for f in files if f.suffix == ".jpeg"]
    assert len(jpeg_files) == 1

@pytest.mark.asyncio
async def test_markdown_converter_edge_cases(mocker, setup_test_environment):
    converter = MarkdownConverter()
    
    # 1. Missing src attribute in img
    html_no_src = "<p>Text</p><img>"
    res_no_src = await converter.convert(html_no_src, "https://example.com", "test-no-src")
    assert "![]" in res_no_src or "<img>" not in res_no_src
    
    # 2. Invalid base64 signature (missing ';base64,')
    html_bad_b64 = '<img src="data:image/png,not-base64-content" />'
    res_bad_b64 = await converter.convert(html_bad_b64, "https://example.com", "test-bad-b64")
    assert "images/test-bad-b64/" not in res_bad_b64
    
    # 3. Connection exception on image download
    mocker.patch("httpx.AsyncClient.get", side_effect=Exception("Connection timed out"))
    html_conn_fail = '<img src="https://example.com/logo.png" />'
    res_conn_fail = await converter.convert(html_conn_fail, "https://example.com", "test-conn-fail")
    assert "https://example.com/logo.png" in res_conn_fail

def test_get_extension_from_mime():
    converter = MarkdownConverter()
    assert converter._get_extension_from_mime("image/png") == ".png"
    assert converter._get_extension_from_mime("image/jpeg") == ".jpg"
    assert converter._get_extension_from_mime("image/gif") == ".gif"
    assert converter._get_extension_from_mime("image/webp") == ".webp"
    assert converter._get_extension_from_mime("image/svg+xml") == ".svg"
    assert converter._get_extension_from_mime("image/unsupported") == ""

@pytest.mark.asyncio
async def test_download_single_image_mime_fallbacks(mocker, setup_test_environment):
    converter = MarkdownConverter()
    library_dir = setup_test_environment["library_dir"]
    img_dir = library_dir / "images" / "test-fallbacks"
    img_dir.mkdir(parents=True, exist_ok=True)
    
    mock_resp = MagicMock()
    mock_resp.content = b"bytes"
    mock_resp.headers = {}
    mock_resp.raise_for_status = MagicMock()
    
    client = MagicMock()
    client.get = AsyncMock(return_value=mock_resp)
    
    filename = await converter._download_single_image(
        client,
        "https://example.com/image.webp",
        img_dir,
        "hash1"
    )
    assert filename == "hash1.webp"
    
    filename2 = await converter._download_single_image(
        client,
        "https://example.com/image-no-ext",
        img_dir,
        "hash2"
    )
    assert filename2 == "hash2.jpg"

