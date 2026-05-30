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
    
    # 2. Assert reference image links are present in Markdown
    assert "![Logo][img_ref_0]" in markdown_result
    assert "![Base64][img_ref_1]" in markdown_result
    
    # 3. Assert base64 representations are embedded at the end
    assert "[img_ref_0]: data:image/png;base64,ZmFrZS1wbmctaW1hZ2UtYnl0ZXM=" in markdown_result
    assert "[img_ref_1]: data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=" in markdown_result
    
    # 4. Assert NO files/directories are written to disk in library/images/test-article
    img_dir = library_dir / "images" / "test-article"
    assert not img_dir.exists() or len(list(img_dir.glob("*"))) == 0

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
async def test_download_single_image_bytes(mocker, setup_test_environment):
    converter = MarkdownConverter()
    
    mock_resp = MagicMock()
    mock_resp.content = b"bytes"
    mock_resp.headers = {"content-type": "image/webp"}
    mock_resp.raise_for_status = MagicMock()
    
    client = MagicMock()
    client.get = AsyncMock(return_value=mock_resp)
    
    import asyncio
    sem = asyncio.Semaphore(1)
    
    res = await converter._download_single_image_bytes(
        client,
        "https://example.com/image.webp",
        sem
    )
    assert res is not None
    content, content_type = res
    assert content == b"bytes"
    assert content_type == "image/webp"

@pytest.mark.asyncio
async def test_markdown_converter_quotes_and_spacing(setup_test_environment):
    converter = MarkdownConverter()
    
    html = (
        "<blockquote>\n"
        "  <p>First paragraph in quote.</p>\n"
        "  <p>Second paragraph in quote.</p>\n"
        "</blockquote>\n"
        "<p>This is a paragraph\n"
        "with internal newlines.</p>\n"
        "<h2>Heading with spaces</h2>"
    )
    
    result = await converter.convert(html, "https://example.com", "test-spacing")
    
    # 1. Blockquote formatted correctly without leading/trailing empty > lines
    assert "> First paragraph in quote." in result
    assert ">" in result
    assert "> Second paragraph in quote." in result
    assert "\n> \n" not in result
    
    # 2. Paragraph collapsed single newlines
    assert "This is a paragraph with internal newlines." in result
    
    # 3. Heading wrapped nicely
    assert "## Heading with spaces" in result

@pytest.mark.asyncio
async def test_markdown_converter_advanced_formatting(setup_test_environment):
    converter = MarkdownConverter()
    
    html = (
        "<p>Привет &nbsp; мир! Это тест\u200b форматирования .</p>"
        "<p>Ссылка с трекером: <a href=\"https://example.com/page?utm_source=telegram&utm_medium=cpc&id=123\">кликни сюда</a>.</p>"
        "<p>Пробелы ( внутри скобок ) и перед знаками препинания , верно ?</p>"
        "<p>Тире - это классный символ , или даже -- вот так.</p>"
        "<p></p>"
        "<iframe src=\"https://www.youtube.com/embed/dQw4w9WgXcQ\" title=\"Видео с разбором\"></iframe>"
    )
    
    result = await converter.convert(html, "https://example.com", "test-advanced-formatting")
    
    assert "Привет мир! Это тест форматирования." in result
    assert "[кликни сюда](https://example.com/page?id=123)" in result
    assert "Пробелы (внутри скобок) and перед знаками препинания, верно?" in result or "Пробелы (внутри скобок) и перед знаками препинания, верно?" in result
    assert "Тире — это классный символ, или даже — вот так." in result
    assert "🔗 [Видео с разбором](https://www.youtube.com/embed/dQw4w9WgXcQ)" in result


@pytest.mark.asyncio
async def test_markdown_converter_premium_formatting(setup_test_environment):
    converter = MarkdownConverter()
    
    # 1. Line-break links & 7. Strikethrough & 6. Navigation junk
    html_1 = (
        "<p>Some text before [подозрительная<br>ссылка<br>"
        "удалена](https://example.com/some-path)<br>"
        "<s>лопату</s> and ~~другие~~ tools.<br>"
        "→<br>"
        "Читать далее</p>"
    )
    res_1 = await converter.convert(html_1, "https://example.com", "test-premium-1")
    
    # Line-breaks in link text collapsed
    assert "[подозрительная ссылка удалена](https://example.com/some-path)" in res_1
    # Strikethrough markdown preserved
    assert "~~лопату~~" in res_1
    assert "~~другие~~" in res_1
    # Junk stripped
    assert "→" not in res_1
    assert "Читать далее" not in res_1

    # 2. Cyrillic double percent-encoded URL and tracking params
    html_2 = '<a href="https://example.com/224909-istoriju-pishut-pobediteli...%25D0%25B8%25D0%25B8?utm_source=telegram&fbclid=abc">ссылка</a>'
    res_2 = await converter.convert(html_2, "https://example.com", "test-premium-2")
    assert "[ссылка](https://example.com/224909-istoriju-pishut-pobediteli...ии)" in res_2

    # 3. Punctuation spacing and sticking formatting
    html_3 = (
        "<p>Aaron Courville.<strong>Источник</strong>.</p>"
        "<p>Aaron Courville.<strong>Источник</strong><a href=\"/url\">Link</a></p>"
        "<p>Some<strong>bold</strong>word and word<strong>bold</strong>.</p>"
        "<p><a href=\"/url\">Источник.</a></p>"
    )
    res_3 = await converter.convert(html_3, "https://example.com", "test-premium-3")
    assert "Aaron Courville. **Источник**." in res_3
    assert "Aaron Courville. **Источник** [Link]" in res_3
    assert "Some **bold** word" in res_3
    assert "word **bold**." in res_3
    assert "[Источник](/url)." in res_3

    # 4. Blockquotes and blank lines
    html_4 = (
        "<blockquote>"
        "<p>Цитата часть 1</p>"
        "<p>Цитата часть 2</p>"
        "</blockquote>"
    )
    res_4 = await converter.convert(html_4, "https://example.com", "test-premium-4")
    # Inside blockquote empty line should have '>' prefix
    assert "> Цитата часть 1\n>\n> Цитата часть 2" in res_4

    # 5. Header spacing
    html_5 = (
        "<p>Text before</p>"
        "<h2>Header 1</h2>"
        "<p>Text after</p>"
    )
    res_5 = await converter.convert(html_5, "https://example.com", "test-premium-5")
    assert "Text before\n\n## Header 1\n\nText after" in res_5

    # 8. List formation from consecutive link-only lines
    html_8 = (
        "<p><a href=\"#1\">Поиск истоков</a></p>"
        "<p><a href=\"#2\">Становление</a></p>"
    )
    res_8 = await converter.convert(html_8, "https://example.com", "test-premium-8")
    assert "* [Поиск истоков](#1)\n* [Становление](#2)" in res_8

    # 9. List formation from a single line of consecutive links (TOC)
    html_9 = (
        "<p><a href=\"#1\">Поиск истоков</a> <a href=\"#2\">Становление</a></p>"
    )
    res_9 = await converter.convert(html_9, "https://example.com", "test-premium-9")
    assert "* [Поиск истоков](#1)\n* [Становление](#2)" in res_9


@pytest.mark.asyncio
async def test_markdown_converter_lazy_loaded_images(mocker, setup_test_environment):
    from ril.converters import extract_real_image_src
    from bs4 import BeautifulSoup
    
    # 1. Test unit extraction of lazy loaded images
    html_img_lazy = (
        '<img data-src="https://example.com/lazy1.png" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" />'
    )
    soup1 = BeautifulSoup(html_img_lazy, "lxml")
    img1 = soup1.find("img")
    assert extract_real_image_src(img1) == "https://example.com/lazy1.png"
    
    # 2. Test src attribute rewrite during Markdown conversion
    converter = MarkdownConverter()
    
    # Mock httpx image download
    mock_response = MagicMock()
    mock_response.content = b"fake-bytes"
    mock_response.headers = {"content-type": "image/png"}
    mock_response.raise_for_status = MagicMock()
    mocker.patch("httpx.AsyncClient.get", return_value=mock_response)
    
    html = (
        '<div>'
        '  <img data-src="https://example.com/lazy-load-image.png" src="placeholder.gif" alt="Lazy Image" />'
        '</div>'
    )
    
    res = await converter.convert(html, "https://example.com", "test-lazy-image")
    # Verified that placeholder is bypassed and real image path is resolved
    assert "![Lazy Image][img_ref_0]" in res
    assert "[img_ref_0]: data:image/png;base64,ZmFrZS1ieXRlcw==" in res


def test_fix_formatting_punctuation_image_spacing():
    from ril.converters import clean_markdown
    
    # 1. Image sticking to word should get a space before the '!', but keep '![' together
    input_md = "на каждом временном шаге![t](images/test/formula.svg) новый элемент"
    output_md = clean_markdown(input_md)
    assert "шаге ![t](images/test/formula.svg)" in output_md
    assert "шаге! [t]" not in output_md
    
    # 2. Normal punctuation followed by link should have space after '.' and before '['
    input_md_2 = "подозрительно.[ссылка](http://example.com) кликай"
    output_md_2 = clean_markdown(input_md_2)
    assert "подозрительно. [ссылка](http://example.com)" in output_md_2


@pytest.mark.asyncio
async def test_html_converter_basic(setup_test_environment):
    from ril.converters import HTMLConverter
    converter = HTMLConverter()
    assert converter.file_extension == ".html"
    
    html = "<h1>Заголовок статьи</h1><p>Какое-то интересное содержание.</p>"
    result = await converter.convert(html, "https://example.com", "test-html-basic")
    
    assert "<!DOCTYPE html>" in result
    assert "<title>Заголовок статьи</title>" in result
    assert "Какое-то интересное содержание." in result
    assert "theme-toggle" in result
    assert "Inter" in result


@pytest.mark.asyncio
async def test_html_converter_images(mocker, setup_test_environment):
    from ril.converters import HTMLConverter
    converter = HTMLConverter()
    
    # Mock httpx.AsyncClient.get response for image download
    mock_response = MagicMock()
    mock_response.content = b"fake-jpeg-bytes"
    mock_response.headers = {"content-type": "image/jpeg"}
    mock_response.raise_for_status = MagicMock()
    
    mocker.patch("httpx.AsyncClient.get", return_value=mock_response)
    
    html = (
        "<h1>Статья с картинками</h1>"
        "<p>Картинка снаружи:</p>"
        '<img src="https://example.com/logo.jpg" alt="Logo" />'
        "<p>Уже base64 картинка:</p>"
        '<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==" />'
    )
    
    result = await converter.convert(html, "https://example.com/page", "test-html-images")
    
    # Check that external image is now a base64 string
    assert "data:image/jpeg;base64," in result
    assert "ZmFrZS1qcGVnLWJ5dGVz" in result  # base64 for "fake-jpeg-bytes"
    
    # Check that the existing base64 image is preserved
    assert "data:image/png;base64,iVBORw0KGgo" in result


@pytest.mark.asyncio
async def test_html_converter_mobile_responsive(mocker, setup_test_environment):
    from ril.converters import HTMLConverter
    from bs4 import BeautifulSoup
    converter = HTMLConverter()
    
    # Mock httpx image download
    mock_response = MagicMock()
    mock_response.content = b"fake-bytes"
    mock_response.headers = {"content-type": "image/png"}
    mock_response.raise_for_status = MagicMock()
    mocker.patch("httpx.AsyncClient.get", return_value=mock_response)
    
    html = (
        "<h1>Заголовок</h1>"
        "<p>Формула в тексте <img src=\"https://example.com/formula.png\" alt=\"formula\" width=\"30\" /> и продолжение текста.</p>"
        "<div><img src=\"https://example.com/illustration.png\" alt=\"big picture\" /></div>"
        "<table><tr><th>Колонка</th></tr><tr><td>Данные</td></tr></table>"
    )
    
    result = await converter.convert(html, "https://example.com", "test-html-mobile")
    
    # Parse output to verify layout structure
    soup = BeautifulSoup(result, "lxml")
    
    # 1. Inline image should NOT have illustration class
    formula_img = soup.find("img", alt="formula")
    assert formula_img is not None
    classes = formula_img.get("class", [])
    assert "illustration" not in classes
    
    # 2. Block/Illustration image SHOULD have illustration class
    big_img = soup.find("img", alt="big picture")
    assert big_img is not None
    assert "illustration" in big_img.get("class", [])
    
    # 3. Table should be wrapped in table-container
    table = soup.find("table")
    assert table is not None
    assert table.parent.name == "div"
    assert table.parent.get("class") == ["table-container"]
    
    # 4. Viewport meta tag and media queries should exist
    assert '<meta name="viewport" content="width=device-width, initial-scale=1.0">' in result
    assert '@media (max-width: 640px)' in result
    assert 'img.illustration' in result
    assert 'box-sizing: border-box' in result


@pytest.mark.asyncio
async def test_epub_converter_basic(mocker, setup_test_environment):
    from ril.converters import EPUBConverter
    import zipfile
    import io
    
    converter = EPUBConverter()
    assert converter.file_extension == ".epub"
    
    # Mock httpx image download
    mock_response = MagicMock()
    mock_response.content = b"fake-epub-bytes"
    mock_response.headers = {"content-type": "image/png"}
    mock_response.raise_for_status = MagicMock()
    mocker.patch("httpx.AsyncClient.get", return_value=mock_response)
    
    html = (
        "<h1>EPUB Title</h1>"
        "<p>This is a paragraph with <img src=\"https://example.com/logo.png\" alt=\"Logo\" />.</p>"
    )
    
    epub_bytes = await converter.convert(html, "https://example.com", "test-epub")
    assert isinstance(epub_bytes, bytes)
    
    # Read the zip structure from epub bytes
    with zipfile.ZipFile(io.BytesIO(epub_bytes)) as z:
        # Check files inside EPUB
        namelist = z.namelist()
        assert "mimetype" in namelist
        assert "META-INF/container.xml" in namelist
        assert "OEBPS/content.opf" in namelist
        assert "OEBPS/toc.ncx" in namelist
        assert "OEBPS/style.css" in namelist
        assert "OEBPS/article.xhtml" in namelist
        assert "OEBPS/images/img_0.png" in namelist
        
        # Check mimetype is correct and stored uncompressed
        info = z.getinfo("mimetype")
        assert info.compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"
        
        # Verify content.opf and article.xhtml have correct data
        content_opf = z.read("OEBPS/content.opf").decode("utf-8")
        assert "EPUB Title" in content_opf
        assert "images/img_0.png" in content_opf
        
        article_xhtml = z.read("OEBPS/article.xhtml").decode("utf-8")
        assert "EPUB Title" in article_xhtml
        assert 'src="images/img_0.png"' in article_xhtml








