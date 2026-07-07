import pytest
import io
import zipfile
import re
import os
from pathlib import Path
from PIL import Image
from unittest.mock import MagicMock, AsyncMock

from ril.converters import (
    MarkdownConverter,
    EPUBConverter,
    fix_markdown_image_syntax,
    preprocess_formulas
)
from bs4 import BeautifulSoup


def test_formula_conversion():
    """
    Tests correct conversion of math formulas from HTML/LaTeX markup to Markdown delimiters ($ and $$).
    """
    # 1. MathJax script tags (inline and display)
    html_mathjax = (
        "<p>Equation inline: <script type='math/tex'>E=mc^2</script></p>"
        "<p>Equation block: <script type='math/tex; mode=display'>\\nabla \\times \\vec{E} = -\\frac{\\partial \\vec{B}}{\\partial t}</script></p>"
    )
    soup_mj = BeautifulSoup(html_mathjax, "lxml")
    preprocess_formulas(soup_mj, to_markdown=True)
    res_mj = str(soup_mj)
    
    assert "$E=mc^2$" in res_mj
    assert "$$\\nabla \\times \\vec{E} = -\\frac{\\partial \\vec{B}}{\\partial t}$$" in res_mj

    # 2. KaTeX elements with annotation
    html_katex = (
        "<div class='katex-display'>"
        "  <span class='katex'>"
        "    <span class='katex-mathml'><math><mi>x</mi></math></span>"
        "    <annotation encoding='application/x-tex'>f(x) = x^2</annotation>"
        "  </span>"
        "</div>"
    )
    soup_kt = BeautifulSoup(html_katex, "lxml")
    preprocess_formulas(soup_kt, to_markdown=True)
    res_kt = str(soup_kt)
    
    assert "$$f(x) = x^2$$" in res_kt

    # 3. Raw LaTeX delimiters in text nodes
    html_raw = "<p>Let \\( a^2 + b^2 = c^2 \\) be the equation, and block: \\[ y = mx + c \\]</p>"
    soup_raw = BeautifulSoup(html_raw, "lxml")
    preprocess_formulas(soup_raw, to_markdown=True)
    res_raw = str(soup_raw)
    
    assert "$a^2 + b^2 = c^2$" in res_raw
    assert "$$y = mx + c$$" in res_raw

    # 4. Formula images (e.g. Habr style)
    html_images = (
        "<p>Equation inline: <img class='formula inline' source='\\pm i' alt='\\pm i' /></p>"
        "<p>Equation block: <img class='formula' source='x^5 - 6x + 3 = 0.' alt='x^5 - 6x + 3 = 0.' /></p>"
    )
    soup_imgs = BeautifulSoup(html_images, "lxml")
    preprocess_formulas(soup_imgs, to_markdown=True)
    res_imgs = str(soup_imgs)
    
    assert "$\\pm i$" in res_imgs
    assert "$$x^5 - 6x + 3 = 0.$$" in res_imgs



def test_markdown_image_syntax_repair():
    """
    Tests the validation/regex engine that fixes syntax mistakes in Markdown images.
    """
    # 1. Unclosed parenthesis at end of line or followed by text
    md_unclosed = "Some description ![Illustration](images/my_photo.png and then other text"
    md_fixed1 = fix_markdown_image_syntax(md_unclosed)
    assert "![Illustration](images/my_photo.png)" in md_fixed1
    assert "and then other text" in md_fixed1

    # 2. Backslashes in image paths
    md_backslashes = "Review this: ![Architecture Diagram](assets\\images\\schema.png)"
    md_fixed2 = fix_markdown_image_syntax(md_backslashes)
    assert "![Architecture Diagram](assets/images/schema.png)" in md_fixed2

    # 3. Already valid syntax remains unmodified and uncorrupted
    md_valid = "Correct one: ![Sample Image](images/logo.webp) in text."
    md_fixed3 = fix_markdown_image_syntax(md_valid)
    assert md_fixed3 == md_valid


def test_image_compression_and_resizing():
    """
    Tests Pillow compression module:
    - Resize max dimension down to 1200px.
    - Opaque RGBA/RGB image gets converted to progressive JPEG with reduced quality.
    - Verifies the size of optimized bytes is smaller than original bytes.
    """
    converter = MarkdownConverter()
    import random

    # Create a large opaque RGBA image (1600x1200)
    large_img = Image.new("RGBA", (1600, 1200))
    
    # Add random pixel noise to make it very heavy and prevent PNG compression
    pixels = large_img.load()
    for i in range(1600):
        for j in range(1200):
            pixels[i, j] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255), 255)
                
    img_io = io.BytesIO()
    large_img.save(img_io, format="PNG")
    original_bytes = img_io.getvalue()

    # Compress/resize image
    optimized = converter._optimize_image(original_bytes, "image/png", max_dim=1200, quality=75)
    assert optimized is not None
    opt_bytes, opt_mime = optimized

    # Verify mime type is progressive JPEG (since opaque)
    assert opt_mime == "image/jpeg"
    assert len(opt_bytes) < len(original_bytes)

    # Read back and check dimensions
    opt_img = Image.open(io.BytesIO(opt_bytes))
    w, h = opt_img.size
    assert max(w, h) == 1200
    assert w == 1200
    assert h == 900  # Kept aspect ratio 4:3 (1600x1200 -> 1200x900)


@pytest.mark.asyncio
async def test_epub_mock_document_assembly(mocker):
    """
    Verifies the compilation of a mock document into a valid EPUB container:
    - Verifies document structures inside zip archive (mimetype, container.xml, content.opf, article.xhtml).
    - Verifies mock image assets are properly optimized, packaged inside directories, and referenced correctly.
    """
    converter = EPUBConverter()
    
    # Mock httpx.AsyncClient.get for image downloads
    mock_resp = MagicMock()
    mock_resp.headers = {"content-type": "image/png"}
    # Make a dummy PNG to return
    dummy_img = Image.new("RGB", (100, 100), color="green")
    img_io = io.BytesIO()
    dummy_img.save(img_io, format="PNG")
    mock_resp.content = img_io.getvalue()
    mock_resp.raise_for_status = MagicMock()
    
    mocker.patch("httpx.AsyncClient.get", return_value=mock_resp)

    # HTML with external web image and math equations
    html_content = (
        "<h1>Mock Ebook</h1>"
        "<p>This book contains an image: <img src='https://example.com/cover.png' alt='Cover' /></p>"
        "<p>Let's write a formula: <script type='math/tex'>x^2 + y^2 = r^2</script></p>"
    )
    
    epub_bytes = await converter.convert(html_content, "https://example.com", "mock-ebook")
    assert isinstance(epub_bytes, bytes)

    # Inspect zip contents
    with zipfile.ZipFile(io.BytesIO(epub_bytes)) as epub_zip:
        files = epub_zip.namelist()
        
        # Check standard EPUB files
        assert "mimetype" in files
        assert "META-INF/container.xml" in files
        assert "OEBPS/content.opf" in files
        assert "OEBPS/toc.ncx" in files
        assert "OEBPS/style.css" in files
        assert "OEBPS/article.xhtml" in files
        
        # Verify the image got downloaded, optimized (converted to JPEG because opaque), and packaged
        # Image will be img_0.jpg since original was png but opaque, converting it to JPEG.
        assert "OEBPS/images/img_0.jpg" in files or "OEBPS/images/img_0.png" in files
        
        # Verify content.opf references the correct image path
        content_opf = epub_zip.read("OEBPS/content.opf").decode("utf-8")
        assert "images/img_0.jpg" in content_opf or "images/img_0.png" in content_opf
        
        # Verify article.xhtml references the correct local image and contains MathML
        article_xhtml = epub_zip.read("OEBPS/article.xhtml").decode("utf-8")
        assert 'src="images/img_0.jpg"' in article_xhtml or 'src="images/img_0.png"' in article_xhtml
        
        # Verify the math got converted to MathML for EPUB compatibility
        assert "<math" in article_xhtml
        assert "xmlns=\"http://www.w3.org/1998/Math/MathML\"" in article_xhtml
