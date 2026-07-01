"""
Security tests for HTML preprocessing.

Verifies that preprocess_html strips javascript event handlers,
neutralizes javascript: URIs, and does not break normal HTML.
"""
import pytest
from ril.converters import preprocess_html


class TestPreprocessHtmlSecurity:
    """XSS / injection prevention in preprocess_html."""

    def test_removes_onerror_attribute(self):
        html = '<img src="x" onerror="alert(1)">'
        cleaned = preprocess_html(html)
        assert "onerror" not in cleaned

    def test_removes_onclick_attribute(self):
        html = '<button onclick="evil()">Click me</button>'
        cleaned = preprocess_html(html)
        assert "onclick" not in cleaned

    def test_removes_onload_attribute(self):
        html = '<body onload="steal()"><p>Text</p></body>'
        cleaned = preprocess_html(html)
        assert "onload" not in cleaned

    def test_removes_multiple_handlers_on_same_tag(self):
        html = '<div onmouseover="a()" onmouseout="b()">hover</div>'
        cleaned = preprocess_html(html)
        assert "onmouseover" not in cleaned
        assert "onmouseout" not in cleaned

    def test_neutralizes_javascript_href(self):
        html = '<a href="javascript:alert(1)">click</a>'
        cleaned = preprocess_html(html)
        assert "javascript:" not in cleaned

    def test_neutralizes_javascript_src(self):
        html = '<img src="javascript:alert(1)">'
        cleaned = preprocess_html(html)
        assert "javascript:" not in cleaned

    def test_neutralizes_javascript_case_insensitive(self):
        html = '<a href="JAVASCRIPT:alert(1)">click</a>'
        cleaned = preprocess_html(html)
        assert "javascript:" not in cleaned.lower()

    def test_combined_handler_and_javascript_uri(self):
        html = '<img src=x onerror="alert(1)"><a href="javascript:alert(1)">x</a>'
        cleaned = preprocess_html(html)
        assert "onerror" not in cleaned
        assert "javascript:" not in cleaned

    def test_does_not_break_normal_href(self):
        html = '<a href="https://example.com">Normal link</a>'
        cleaned = preprocess_html(html)
        assert "https://example.com" in cleaned

    def test_does_not_break_normal_img_src(self):
        html = '<img src="https://example.com/photo.jpg" alt="photo">'
        cleaned = preprocess_html(html)
        assert "https://example.com/photo.jpg" in cleaned

    def test_does_not_break_paragraph(self):
        html = "<p>Hello world</p>"
        cleaned = preprocess_html(html)
        assert "Hello world" in cleaned

    def test_does_not_break_blockquote(self):
        html = "<blockquote><p>Quoted text</p></blockquote>"
        cleaned = preprocess_html(html)
        assert "Quoted text" in cleaned

    def test_script_tag_stripped(self):
        html = "<script>alert('xss')</script><p>Safe</p>"
        cleaned = preprocess_html(html)
        assert "<script>" not in cleaned
        assert "Safe" in cleaned

    def test_style_tag_stripped(self):
        html = "<style>body { background: red; }</style><p>Content</p>"
        cleaned = preprocess_html(html)
        assert "<style>" not in cleaned
        assert "Content" in cleaned

    def test_noscript_stripped(self):
        html = "<noscript><p>Enable JS</p></noscript><p>Main</p>"
        cleaned = preprocess_html(html)
        assert "<noscript>" not in cleaned
        assert "Main" in cleaned


class TestPreprocessHtmlTracking:
    """Tracking param stripping in preprocess_html."""

    def test_removes_utm_from_links(self):
        html = '<a href="https://example.com/page?utm_source=newsletter&utm_medium=email">Link</a>'
        cleaned = preprocess_html(html)
        assert "utm_source" not in cleaned
        assert "utm_medium" not in cleaned
        assert "example.com" in cleaned

    def test_preserves_non_tracking_params(self):
        html = '<a href="https://example.com/search?q=python&page=2">Search</a>'
        cleaned = preprocess_html(html)
        assert "q=python" in cleaned

    def test_removes_empty_links(self):
        html = '<a href="https://example.com"></a><p>Text</p>'
        cleaned = preprocess_html(html)
        # Empty link with no text and no inner tags should be removed
        assert "Text" in cleaned

    def test_removes_cookie_banner_by_id(self):
        html = '<div id="onetrust-consent-sdk">Accept cookies</div><p>Article content</p>'
        cleaned = preprocess_html(html)
        assert "Accept cookies" not in cleaned
        assert "Article content" in cleaned
