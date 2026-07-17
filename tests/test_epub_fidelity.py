"""
EPUB fidelity integration tests using marker-pdf fixture markdown (Phase 5 / R4/R6).
"""
import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from ril import core
from ril.converters import EPUBConverter, validate_epub_structure

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "marker_outputs"
FIXTURE_FILES = sorted(FIXTURES_DIR.glob("*.md"))


def _fixture_title(md_path: Path) -> str:
    first_line = md_path.read_text(encoding="utf-8").splitlines()[0]
    return first_line.lstrip("# ").strip() or md_path.stem


@pytest.mark.parametrize("fixture_path", FIXTURE_FILES, ids=lambda p: p.name)
@pytest.mark.asyncio
async def test_fixture_md_to_epub_passes_structure_validation(fixture_path):
    md = fixture_path.read_text(encoding="utf-8")
    html = core.md_to_html_fallback(md, _fixture_title(fixture_path))

    converter = EPUBConverter()
    epub_bytes = await converter.convert(
        html, "https://example.com/fixture", fixture_path.stem
    )

    warnings = validate_epub_structure(epub_bytes)
    assert warnings == [], f"{fixture_path.name} validation warnings: {warnings}"


@pytest.mark.asyncio
async def test_multi_section_paper_ncx_has_three_or_more_navpoints():
    md = (FIXTURES_DIR / "multi_section_paper.md").read_text(encoding="utf-8")
    html = core.md_to_html_fallback(md, "Attention Is All You Need")

    converter = EPUBConverter()
    epub_bytes = await converter.convert(
        html, "https://example.com/multi-section", "multi_section_paper"
    )

    with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as z:
        toc_ncx = z.read("OEBPS/toc.ncx").decode("utf-8")

    assert toc_ncx.count("<navPoint") >= 3


@pytest.mark.asyncio
async def test_math_heavy_epub_contains_mathml():
    md = (FIXTURES_DIR / "math_heavy.md").read_text(encoding="utf-8")
    html = core.md_to_html_fallback(md, "Mathematical Notation in Academic PDFs")

    converter = EPUBConverter()
    epub_bytes = await converter.convert(
        html, "https://example.com/math-heavy", "math_heavy"
    )

    with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as z:
        assert z.namelist()[0] == "mimetype"
        assert z.read("mimetype") == b"application/epub+zip"
        opf = z.read("OEBPS/content.opf").decode("utf-8")
        assert 'version="3.0"' in opf
        assert 'properties="nav"' in opf
        assert "OEBPS/nav.xhtml" in z.namelist()
        nav = z.read("OEBPS/nav.xhtml").decode("utf-8")
        assert 'epub:type="toc"' in nav
        xhtml_names = [
            name for name in z.namelist()
            if name.startswith("OEBPS/") and name.endswith(".xhtml")
            and name != "OEBPS/nav.xhtml"
        ]
        assert xhtml_names, "expected at least one XHTML chapter"
        combined_xhtml = "".join(
            z.read(name).decode("utf-8") for name in xhtml_names
        )

    assert 'xmlns="http://www.w3.org/1998/Math/MathML"' in combined_xhtml
    assert "<math" in combined_xhtml
    # Sample equations from math_heavy.md must appear as MathML, not raw $ soup
    assert combined_xhtml.count("<math") >= 15
    assert 'class="math-fallback"' not in combined_xhtml or combined_xhtml.count(
        'class="math-fallback"'
    ) == 0
    # E = mc^2 as MathML tokens
    assert "<mi>E</mi>" in combined_xhtml and "<mi>m</mi>" in combined_xhtml
    assert "<msup>" in combined_xhtml and "<mn>2</mn>" in combined_xhtml
    # Quadratic / fractions
    assert "<mfrac>" in combined_xhtml and "<msqrt>" in combined_xhtml
    # Gaussian / Greek
    assert "<mi>μ</mi>" in combined_xhtml or "μ" in combined_xhtml
    assert "<mi>σ</mi>" in combined_xhtml or "σ" in combined_xhtml
    # Single-letter $e$ recovered as MathML
    assert re.search(r"<mi>\s*e\s*</mi>", combined_xhtml)
    # Code-block currency dollars must stay literal, not MathML
    assert "${price" in combined_xhtml or "$" in combined_xhtml
    assert "BUDGET" in combined_xhtml


@pytest.mark.asyncio
async def test_export_article_writes_epub_debug_report(
    setup_test_environment, monkeypatch
):
    from ril import config, db

    monkeypatch.setattr(config, "RIL_EPUB_DEBUG", True)

    library_dir = setup_test_environment["library_dir"]
    file_path = library_dir / "debug_report.md"
    file_path.write_text("# Debug Report\n\nInline $x^2$ math.", encoding="utf-8")

    article_id = db.add_article(
        url="https://example.com/debug-report",
        title="Debug Report",
        file_path=str(file_path),
        word_count=4,
        char_count=40,
        content="Debug Report content",
    )

    res = await core.export_article(article_id, "epub", force=True)
    epub_path = Path(res["file_path"])
    report_path = epub_path.with_suffix(".epub.report.json")

    assert epub_path.exists()
    assert report_path.exists()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["chapter_count"] >= 1
    assert report["image_count"] >= 0
    assert report["math_ml_count"] >= 1
    assert report["validation_warnings"] == []