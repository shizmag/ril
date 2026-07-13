"""
Tests for PDF formula transcription: recovering LaTeX from marker image refs.
"""
import io
import zipfile
from pathlib import Path

import pytest

from ril import core, db
from ril.converters import (
    EPUBConverter,
    looks_like_latex,
    normalize_marker_latex,
    recover_formula_images_in_markdown,
    validate_and_normalize_math,
    preprocess_formulas,
)
from bs4 import BeautifulSoup

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "marker_outputs"


def test_looks_like_latex_detects_marker_alt_text():
    assert looks_like_latex(r"\inline G_{t}")
    assert looks_like_latex(r"\frac{\eta}{\sqrt{G}}")
    assert not looks_like_latex("image")
    assert not looks_like_latex("")
    assert not looks_like_latex(r"\$0.06")


def test_normalize_marker_latex_strips_inline_prefix():
    assert normalize_marker_latex(r"\inline G_{t}") == "G_{t}"
    assert normalize_marker_latex("x — y").replace("—", "-") == "x - y" or normalize_marker_latex("x — y") == "x - y"


def test_recover_formula_images_in_markdown_inline():
    md = "Value ![\\inline G_{t}][img_ref_0] here.\n\n[img_ref_0]: data:image/png;base64,abc"
    updated, removed = recover_formula_images_in_markdown(md)
    assert "$G_{t}$" in updated
    assert "img_ref_0" in removed
    assert "[img_ref_0]:" not in updated


def test_recover_formula_images_in_markdown_display():
    md = "![\\theta_{t+1} = \\theta_t][img_ref_1]\n\n[img_ref_1]: data:image/png;base64,abc"
    updated, removed = recover_formula_images_in_markdown(md)
    assert "$$\\theta_{t+1} = \\theta_t$$" in updated
    assert "img_ref_1" in removed


def test_recover_formula_images_keeps_real_images():
    md = "![Figure 1](chart.png)\n\n![\\inline x][img_ref_0]\n\n[img_ref_0]: data:image/png;base64,abc"
    updated, removed = recover_formula_images_in_markdown(md)
    assert "chart.png" in updated
    assert "$x$" in updated
    assert removed == {"img_ref_0"}


def test_preprocess_formulas_recovers_latex_from_img_alt():
    html = '<p><img alt="\\frac{a}{b}" src="data:image/png;base64,abc" /></p>'
    soup = BeautifulSoup(html, "lxml")
    preprocess_formulas(soup, to_markdown=False)
    body = soup.find("body")
    assert body.find("math") is not None
    assert body.find("img") is None


@pytest.mark.asyncio
async def test_md_to_html_formula_alt_exports_to_mathml(setup_test_environment):
    md = "![\\frac{a}{b}][img_ref_0]\n\n[img_ref_0]: data:image/png;base64,YWJj"
    html = core.md_to_html_fallback(md, "Formula Alt Test")

    converter = EPUBConverter()
    epub_bytes = await converter.convert(html, "https://example.com/formula-alt", "formula_alt")

    with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as z:
        xhtml = z.read("OEBPS/article.xhtml").decode("utf-8")

    assert "<math" in xhtml
    assert 'class="math-fallback"' not in xhtml


def test_md_to_html_fallback_preserves_escaped_currency():
    md = "| Cost | \\$0.06 |"
    html = core.md_to_html_fallback(md, "Currency")
    assert "$0.06" in html or "\\$0.06" in html
    assert "math-inline" not in html


def test_validate_and_normalize_math_converts_paren_delimiters():
    md = r"Inline \(E=mc^2\) and block \[a^2+b^2=c^2\]"
    out = validate_and_normalize_math(md)
    assert "$E=mc^2$" in out
    assert "$$a^2+b^2=c^2$$" in out


@pytest.mark.asyncio
async def test_math_heavy_fixture_epub_has_mathml_no_fallback(monkeypatch):
    monkeypatch.setattr(core.config, "RIL_EPUB_DEBUG", True)

    md = (FIXTURES_DIR / "math_heavy.md").read_text(encoding="utf-8")
    html = core.md_to_html_fallback(md, "Mathematical Notation in Academic PDFs")

    converter = EPUBConverter()
    epub_bytes = await converter.convert(
        html, "https://example.com/math-heavy", "math_heavy"
    )

    with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as z:
        combined = "".join(
            z.read(name).decode("utf-8")
            for name in z.namelist()
            if name.endswith(".xhtml")
        )

    assert "<math" in combined
    assert combined.count('class="math-fallback"') == 0


@pytest.mark.asyncio
async def test_library_snippet_formula_recovery_to_epub(setup_test_environment, monkeypatch):
    """Regression: Russian Adam article style formula image refs."""
    snippet = (
        "![\\theta_{t+1} = \\theta_{t} - \\frac{\\eta}{\\sqrt{G_{t} + \\epsilon}} g_{t}][img_ref_60]\n\n"
        "Где ![\\inline G_{t}][img_ref_61] — сумма.\n\n"
        "[img_ref_60]: data:image/jpeg;base64,/9j/4AAQ\n"
        "[img_ref_61]: data:image/jpeg;base64,/9j/4AAQ\n"
    )
    recovered, removed = recover_formula_images_in_markdown(snippet)
    assert "$$" in recovered
    assert "$G_{t}$" in recovered
    assert len(removed) == 2

    library_dir = setup_test_environment["library_dir"]
    file_path = library_dir / "adam_snippet.md"
    file_path.write_text(recovered, encoding="utf-8")

    article_id = db.add_article(
        url="https://example.com/adam-snippet",
        title="Adam Snippet",
        file_path=str(file_path),
        word_count=10,
        char_count=100,
        content="Adam optimizer snippet",
    )

    res = await core.export_article(article_id, "epub", force=True)
    with zipfile.ZipFile(res["file_path"], "r") as z:
        xhtml = z.read("OEBPS/article.xhtml").decode("utf-8")

    assert "<math" in xhtml
    assert "img_ref_60" not in xhtml