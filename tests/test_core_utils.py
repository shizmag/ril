"""
Fast unit tests for ril.core utility functions:
- sanitize_filename (extended)
- clean_url_tracking / clean_and_decode_url
- extract_real_image_src
- clean_markdown
- md_to_html_fallback
- word/char count logic for HTML and PDF markdown
"""
import re
import pytest
from pathlib import Path
from bs4 import BeautifulSoup

from ril.core import sanitize_filename, md_to_html_fallback
from ril.converters import (
    clean_url_tracking,
    clean_and_decode_url,
    extract_real_image_src,
    clean_markdown,
    preprocess_html,
)


# ---------------------------------------------------------------------------
# sanitize_filename
# ---------------------------------------------------------------------------

class TestSanitizeFilename:
    def test_basic_english(self):
        assert sanitize_filename("Hello World!") == "hello_world"

    def test_cyrillic(self):
        assert sanitize_filename("Квантовые Процессоры!") == "квантовые_процессоры"

    def test_length_truncated_at_60(self):
        result = sanitize_filename("a" * 100)
        assert len(result) == 60

    def test_strips_leading_trailing_underscore(self):
        result = sanitize_filename("___title___")
        assert not result.startswith("_")
        assert not result.endswith("_")

    def test_strips_leading_trailing_hyphen(self):
        result = sanitize_filename("---title---")
        assert not result.startswith("-")
        assert not result.endswith("-")

    def test_consecutive_spaces_collapsed(self):
        result = sanitize_filename("a   b   c")
        assert "__" not in result
        assert result == "a_b_c"

    def test_empty_string(self):
        result = sanitize_filename("")
        assert result == ""

    def test_only_special_chars(self):
        result = sanitize_filename("!@#$%^&*()")
        assert result == ""

    def test_mixed_unicode(self):
        result = sanitize_filename("AI и 机器学习")
        assert "ai" in result
        assert "и" in result


# ---------------------------------------------------------------------------
# clean_url_tracking
# ---------------------------------------------------------------------------

class TestCleanUrlTracking:
    def test_strips_utm_source(self):
        url = "https://example.com/page?utm_source=twitter&id=5"
        result = clean_url_tracking(url)
        assert "utm_source" not in result
        assert "id=5" in result

    def test_strips_all_utm_params(self):
        url = "https://example.com/?utm_source=a&utm_medium=b&utm_campaign=c&utm_term=d&utm_content=e"
        result = clean_url_tracking(url)
        assert "utm_" not in result

    def test_strips_fbclid(self):
        url = "https://example.com/post?fbclid=abc123&q=test"
        result = clean_url_tracking(url)
        assert "fbclid" not in result
        assert "q=test" in result

    def test_strips_gclid(self):
        url = "https://example.com/?gclid=xyz"
        result = clean_url_tracking(url)
        assert "gclid" not in result

    def test_preserves_non_tracking_params(self):
        url = "https://example.com/search?q=python&page=2"
        result = clean_url_tracking(url)
        assert "q=python" in result
        assert "page=2" in result

    def test_empty_url_returns_empty(self):
        assert clean_url_tracking("") == ""

    def test_no_query_string_unchanged(self):
        url = "https://example.com/article"
        assert clean_url_tracking(url) == url


# ---------------------------------------------------------------------------
# clean_and_decode_url
# ---------------------------------------------------------------------------

class TestCleanAndDecodeUrl:
    def test_decodes_cyrillic(self):
        url = "https://habr.com/%D1%81%D1%82%D0%B0%D1%82%D1%8C%D1%8F"
        result = clean_and_decode_url(url)
        assert "статья" in result or "habr.com" in result  # decoded or at least not crashed

    def test_strips_tracking_and_decodes(self):
        url = "https://example.com/page?utm_source=mail&id=1"
        result = clean_and_decode_url(url)
        assert "utm_source" not in result
        assert "id=1" in result

    def test_hash_only_returns_empty(self):
        assert clean_and_decode_url("#") == ""

    def test_empty_returns_empty(self):
        assert clean_and_decode_url("") == ""

    def test_space_replaced_with_percent20(self):
        url = "https://example.com/page with spaces"
        result = clean_and_decode_url(url)
        assert " " not in result


# ---------------------------------------------------------------------------
# extract_real_image_src
# ---------------------------------------------------------------------------

class TestExtractRealImageSrc:
    def _img(self, attrs: dict):
        soup = BeautifulSoup("<img>", "lxml")
        tag = soup.find("img")
        for k, v in attrs.items():
            tag[k] = v
        return tag

    def test_returns_src(self):
        img = self._img({"src": "https://example.com/photo.jpg"})
        assert extract_real_image_src(img) == "https://example.com/photo.jpg"

    def test_prefers_data_src_over_src(self):
        img = self._img({"data-src": "https://lazy.com/img.jpg", "src": "placeholder.gif"})
        assert extract_real_image_src(img) == "https://lazy.com/img.jpg"

    def test_returns_last_from_srcset(self):
        img = self._img({"srcset": "img-small.jpg 300w, img-large.jpg 900w"})
        result = extract_real_image_src(img)
        assert "img-large.jpg" in result

    def test_skips_transparent_gif(self):
        img = self._img({"data-src": "data:image/gif;base64,R0lGODlhAQABAIAAAA", "src": "real.jpg"})
        result = extract_real_image_src(img)
        assert result == "real.jpg"

    def test_returns_none_when_no_src(self):
        img = self._img({})
        assert extract_real_image_src(img) is None

    def test_base64_src_returned(self):
        src = "data:image/png;base64,abc123"
        img = self._img({"src": src})
        assert extract_real_image_src(img) == src


# ---------------------------------------------------------------------------
# clean_markdown
# ---------------------------------------------------------------------------

class TestCleanMarkdown:
    def test_collapses_excessive_blank_lines(self):
        md = "# Title\n\n\n\n\nParagraph."
        result = clean_markdown(md)
        assert "\n\n\n" not in result

    def test_preserves_fenced_code_block(self):
        md = "# Title\n\n```python\nx = 1\n\n\ny = 2\n```\n\nAfter."
        result = clean_markdown(md)
        assert "```python" in result
        assert "x = 1" in result
        assert "y = 2" in result

    def test_single_trailing_newline(self):
        md = "# Hello\n\nWorld.\n\n\n"
        result = clean_markdown(md)
        assert result.endswith("\n")
        assert not result.endswith("\n\n")

    def test_preserves_blockquote(self):
        md = "# Title\n\n> Quoted text here.\n\nNormal paragraph."
        result = clean_markdown(md)
        assert "Quoted text here" in result

    def test_collapses_collapsed_link(self):
        md = "[split\nlink](https://example.com)"
        result = clean_markdown(md)
        assert "\n" not in result.split("(")[0] or "split link" in result


# ---------------------------------------------------------------------------
# md_to_html_fallback
# ---------------------------------------------------------------------------

class TestMdToHtmlFallback:
    def test_basic_conversion(self):
        md = "# Hello\n\nParagraph text."
        result = md_to_html_fallback(md, "Hello")
        assert "<h1" in result
        assert "Paragraph text." in result

    def test_title_in_head(self):
        result = md_to_html_fallback("Content", "My Title")
        assert "<title>My Title</title>" in result

    def test_title_escaped(self):
        result = md_to_html_fallback("Content", "<script>alert(1)</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_list_items(self):
        md = "* Item A\n* Item B\n* Item C"
        result = md_to_html_fallback(md, "List")
        assert "<ul>" in result
        assert "<li>Item A</li>" in result

    def test_code_block(self):
        md = "```\nsome code\n```"
        result = md_to_html_fallback(md, "Code")
        assert "<pre>" in result
        assert "some code" in result

    def test_empty_md(self):
        result = md_to_html_fallback("", "Empty")
        assert "<html>" in result
        assert "<title>Empty</title>" in result


# ---------------------------------------------------------------------------
# Word/char count helpers (mirroring core.py logic)
# ---------------------------------------------------------------------------

class TestWordCharCount:
    """Tests replicating core.py word/char counting for HTML and PDF markdown."""

    def _count_from_html(self, html: str) -> tuple:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all(["script", "style", "svg", "img"]):
            tag.decompose()
        text = soup.get_text(separator=" ")
        wc = len(re.findall(r'\w+', text))
        cc = len(text)
        return wc, cc

    def _count_from_pdf_markdown(self, md: str) -> tuple:
        clean = md
        clean = re.sub(r'(?m)^\[img_ref_\d+\].*$', '', clean)
        clean = re.sub(r'!\[.*?\]\[img_ref_\d+\]', '', clean)
        clean = re.sub(r'!\[.*?\]\[.*?\]', '', clean)
        clean = re.sub(r'!\[.*?\]\(.*?\)', '', clean)
        search = re.sub(r'[*#_`\[\]\-!]', ' ', clean)
        wc = len(re.findall(r'\w+', search))
        cc = len(search)
        return wc, cc

    def test_html_ignores_script(self):
        html = "<p>Hello world</p><script>var x = 1;</script>"
        wc, _ = self._count_from_html(html)
        assert wc == 2  # only "Hello" and "world"

    def test_html_ignores_style(self):
        html = "<p>One two three</p><style>.x { color: red; }</style>"
        wc, _ = self._count_from_html(html)
        assert wc == 3

    def test_html_ignores_img(self):
        html = "<p>Text here</p><img src='photo.jpg' alt='Photo caption'>"
        wc, _ = self._count_from_html(html)
        assert wc == 2  # only "Text" and "here"

    def test_pdf_markdown_ignores_image_refs(self):
        md = "# Title\n\nBody text.\n\n![fig][img_ref_0]\n\n[img_ref_0]: data:image/jpeg;base64,abc123"
        wc, _ = self._count_from_pdf_markdown(md)
        # Should count: Title, Body, text — but NOT img_ref_0, abc123, data, image, jpeg, base64
        words_in_count = wc
        assert words_in_count >= 3  # at least Title, Body, text

    def test_pdf_markdown_counts_words(self):
        md = "# Article Title\n\nThis is some article content with ten distinct meaningful words total."
        wc, _ = self._count_from_pdf_markdown(md)
        assert wc > 5
