"""
Tests for PDF formula transcription: recovering LaTeX from marker image refs.
"""
import io
import zipfile
from pathlib import Path

import pytest

from ril import core, db
from PIL import Image

from ril.converters import (
    EPUBConverter,
    classify_pdf_image_role,
    collapse_split_display_math,
    embed_pdf_images_in_markdown,
    is_dollar_delimited_math,
    looks_like_latex,
    normalize_marker_latex,
    normalize_pdf_markdown,
    remove_unused_img_ref_definitions,
    repair_marker_latex,
    repair_math_delimiters_in_markdown,
    strip_marker_html_artifacts,
    enrich_image_alt_text,
    sanitize_latex_for_conversion,
    recover_formula_images_in_markdown,
    validate_and_normalize_math,
    preprocess_formulas,
    convert_latex_to_mathml,
)
from bs4 import BeautifulSoup

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "marker_outputs"


def test_looks_like_latex_detects_marker_alt_text():
    assert looks_like_latex(r"\inline G_{t}")
    assert looks_like_latex(r"\frac{\eta}{\sqrt{G}}")
    assert not looks_like_latex("image")
    assert not looks_like_latex("")
    assert not looks_like_latex(r"\$0.06")


def test_is_dollar_delimited_math_accepts_short_vars_rejects_currency():
    assert is_dollar_delimited_math("e")
    assert is_dollar_delimited_math("n")
    assert is_dollar_delimited_math(r"E = mc^2")
    assert is_dollar_delimited_math(r"\frac{a}{b}")
    assert not is_dollar_delimited_math("19.99")
    assert not is_dollar_delimited_math("5000")
    assert not is_dollar_delimited_math("")


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


def test_looks_like_latex_detects_subscript_without_backslash():
    assert looks_like_latex("G_{t}")
    assert looks_like_latex("d_k")


def test_repair_marker_latex_fixes_malformed_angle_tokens():
    raw = r"\langle /\text{think} \rangle"
    repaired = repair_marker_latex(raw)
    assert r"\langle \text{think}" in repaired
    assert "/" not in repaired.split(r"\langle", 1)[1][:12]


def test_strip_marker_html_artifacts():
    md = '<span id="page-4-1"></span>![][img_ref_0]\n\nText.'
    assert "<span" not in strip_marker_html_artifacts(md)
    assert "![][img_ref_0]" in strip_marker_html_artifacts(md)


def test_enrich_image_alt_text_from_filename():
    assert enrich_image_alt_text("", "figures/beta_1.png") == ""
    assert enrich_image_alt_text("", r"eqs/\frac{a}{b}.png") == r"\frac{a}{b}"


def test_classify_pdf_image_role_by_dimensions():
    inline_img = Image.new("RGB", (240, 40), color="white")
    figure_img = Image.new("RGB", (800, 600), color="white")
    spacer_img = Image.new("RGB", (8, 8), color="white")

    assert classify_pdf_image_role(inline_img) == "formula-inline"
    assert classify_pdf_image_role(figure_img) == "figure"
    assert classify_pdf_image_role(spacer_img) == "spacer"


def test_enrich_image_alt_text_uses_dimension_role():
    inline_img = Image.new("RGB", (200, 36), color="white")
    figure_img = Image.new("RGB", (640, 480), color="white")

    assert enrich_image_alt_text("", "chart-1.png", inline_img) == "formula-inline"
    assert enrich_image_alt_text("image", "plot.png", figure_img) == "figure"


def test_collapse_split_display_math_merges_adjacent_blocks():
    md = "$$a = b$$\n\n$$c = d$$"
    out = collapse_split_display_math(md)
    assert out == "$$a = b c = d$$"


def test_remove_unused_img_ref_definitions():
    md = (
        "See ![figure][img_ref_0].\n\n"
        "[img_ref_0]: data:image/png;base64,abc\n"
        "[img_ref_1]: data:image/png;base64,def\n"
    )
    out = remove_unused_img_ref_definitions(md)
    assert "[img_ref_0]:" in out
    assert "[img_ref_1]:" not in out


def test_embed_pdf_images_only_emits_used_refs():
    img = Image.new("RGB", (120, 40), color="white")
    images = {"eq-0.png": img, "unused.png": Image.new("RGB", (400, 300), color="white")}
    md = "Chart ![](eq-0.png) here."
    updated, roles = embed_pdf_images_in_markdown(md, images)

    assert "![formula-inline][img_ref_0]" in updated
    assert "[img_ref_0]:" in updated
    assert "[img_ref_1]:" not in updated
    assert roles["img_ref_0"]["role"] == "formula-inline"


def test_repair_math_delimiters_in_markdown():
    md = "$$p_t = \\mathcal{D}(\\text{input}, \\, \\mathcal{S}_{t-1})$$"
    out = repair_math_delimiters_in_markdown(md)
    assert out.startswith("$$")
    assert "\\mathcal{D}" in out


def test_validate_and_normalize_math_preserves_fenced_code():
    md = "```python\nprice = \"$100\"\n```\n\nInline \\(x^2\\)"
    out = validate_and_normalize_math(md)
    assert 'price = "$100"' in out
    assert "$x^2$" in out


def test_normalize_pdf_markdown_full_pipeline():
    md = (
        '<span id="page-1-0"></span>![\\inline \\beta][img_ref_0]\n\n'
        "Block \\[E=mc^2\\]\n\n"
        "[img_ref_0]: data:image/png;base64,abc\n"
    )
    out = normalize_pdf_markdown(md)
    assert "<span" not in out
    assert "$\\beta$" in out or "$beta$" in out
    assert "$$E=mc^2$$" in out
    assert "img_ref_0" not in out


def test_sanitize_latex_for_conversion_maps_unicode_symbols():
    out = sanitize_latex_for_conversion(r"\langle \text{think} \rangle")
    assert r"\langle" in out
    out2 = sanitize_latex_for_conversion("x × y")
    assert r"\times" in out2


def test_recover_formula_images_direct_path():
    md = "Inline ![\\beta_1][img_ref_0] and direct ![\\frac{a}{b}](equation-0.png)."
    updated, removed = recover_formula_images_in_markdown(md)
    assert "$\\beta_1$" in updated or "$$" not in updated  # inline beta
    assert "$\\frac{a}{b}$" in updated or "$$\\frac{a}{b}$$" in updated
    assert "equation-0.png" not in updated


def test_recover_formula_images_keeps_real_images():
    md = "![Figure 1](chart.png)\n\n![\\inline x][img_ref_0]\n\n[img_ref_0]: data:image/png;base64,abc"
    updated, removed = recover_formula_images_in_markdown(md)
    assert "chart.png" in updated
    assert "$x$" in updated
    assert removed == {"img_ref_0"}


def test_preprocess_formulas_converts_inline_dollar_text_nodes():
    html = "<p>The value $d_k$ and block $$E = mc^2$$ appear here.</p>"
    soup = BeautifulSoup(html, "lxml")
    preprocess_formulas(soup, to_markdown=False)
    body = soup.find("body")
    assert body.find("math") is not None
    assert "$d_k$" not in body.get_text()


def test_preprocess_formulas_retries_math_fallback():
    html = '<p><span class="math-fallback" data-latex="x^2">x^2</span></p>'
    soup = BeautifulSoup(html, "lxml")
    preprocess_formulas(soup, to_markdown=False)
    assert soup.find("math") is not None
    assert soup.find(class_="math-fallback") is None


def test_convert_latex_to_mathml_handles_unicode_times():
    soup = convert_latex_to_mathml("a × b", "inline")
    assert soup.find("math") is not None


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


def test_md_to_html_fallback_wraps_single_letter_math():
    html = core.md_to_html_fallback(
        "The definition of $e$ uses a series.",
        "Euler",
    )
    assert 'class="math-inline"' in html
    assert 'data-latex="e"' in html
    assert "$e$" not in html


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


@pytest.mark.asyncio
async def test_export_markdown_recovers_legacy_formula_images(setup_test_environment):
    """Exporting markdown from legacy .md should inline formulas, not keep image refs."""
    library_dir = setup_test_environment["library_dir"]
    legacy_md = (
        "Parameters ![\\inline \\beta][img_ref_0] and ![\\inline \\gamma][img_ref_1].\n\n"
        "[img_ref_0]: data:image/jpeg;base64,/9j/4AAQ\n"
        "[img_ref_1]: data:image/jpeg;base64,/9j/4AAQ\n"
    )
    file_path = library_dir / "legacy_formulas.md"
    file_path.write_text(legacy_md, encoding="utf-8")

    article_id = db.add_article(
        url="https://example.com/legacy-formulas",
        title="Legacy Formulas",
        file_path=str(file_path),
        word_count=5,
        char_count=80,
        content="legacy formulas",
    )

    res = await core.export_article(article_id, "markdown", force=True)
    exported = Path(res["file_path"]).read_text(encoding="utf-8")

    assert "$\\beta$" in exported or "$beta$" in exported
    assert "$\\gamma$" in exported or "$gamma$" in exported
    assert "img_ref_0" not in exported
    assert "data:image/" not in exported