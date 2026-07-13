"""
End-to-end PDF import → EPUB export integration tests (mocked marker).
"""
import io
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ril import core, db
from ril.converters import EPUBConverter, MarkdownConverter, validate_epub_structure

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "marker_outputs"
FIXTURE_FILES = sorted(FIXTURES_DIR.glob("*.md"))


def _fixture_title(md_path: Path) -> str:
    first_line = md_path.read_text(encoding="utf-8").splitlines()[0]
    return first_line.lstrip("# ").strip() or md_path.stem


@pytest.mark.asyncio
async def test_pdf_import_with_epub_format_writes_epub(monkeypatch, setup_test_environment, mocker):
    library_dir = setup_test_environment["library_dir"]
    fake_pdf = library_dir / "paper.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    mocker.patch("ril.core.detect_pdf_url_or_content", new_callable=AsyncMock, return_value=True)
    mocker.patch("ril.core.download_pdf", return_value=fake_pdf)
    mocker.patch("ril.core.is_pdf_file", return_value=True)

    fixture_md = (FIXTURES_DIR / "multi_section_paper.md").read_text(encoding="utf-8")
    mocker.patch(
        "ril.core.convert_pdf_with_marker",
        return_value=(fixture_md, "Attention Is All You Need", {}, {}),
    )

    url = "https://example.com/paper.pdf"
    result = await core.process_url(url, converter=EPUBConverter(), force=True)

    epub_path = Path(result["file_path"])
    assert epub_path.suffix == ".epub"
    assert epub_path.exists()

    warnings = validate_epub_structure(epub_path.read_bytes())
    assert warnings == []


@pytest.mark.asyncio
async def test_pdf_import_writes_html_clean_cache(monkeypatch, setup_test_environment, mocker):
    library_dir = setup_test_environment["library_dir"]
    fake_pdf = library_dir / "cached.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    mocker.patch("ril.core.detect_pdf_url_or_content", new_callable=AsyncMock, return_value=True)
    mocker.patch("ril.core.download_pdf", return_value=fake_pdf)
    mocker.patch("ril.core.is_pdf_file", return_value=True)
    mocker.patch(
        "ril.core.convert_pdf_with_marker",
        return_value=("# Cached PDF\n\nBody.", "Cached PDF", {}, {}),
    )

    await core.process_url(
        "https://example.com/cached.pdf",
        converter=MarkdownConverter(),
        force=True,
    )

    md_files = list(library_dir.glob("*.md"))
    assert md_files
    html_clean = md_files[0].with_suffix(".html_clean")
    assert html_clean.exists()
    assert "<html>" in html_clean.read_text(encoding="utf-8")


@pytest.mark.parametrize("fixture_path", FIXTURE_FILES, ids=lambda p: p.name)
@pytest.mark.asyncio
async def test_fixture_md_export_epub_structure(fixture_path, setup_test_environment):
    md = fixture_path.read_text(encoding="utf-8")
    library_dir = setup_test_environment["library_dir"]
    file_path = library_dir / fixture_path.name
    file_path.write_text(md, encoding="utf-8")

    article_id = db.add_article(
        url=f"https://example.com/{fixture_path.stem}",
        title=_fixture_title(fixture_path),
        file_path=str(file_path),
        word_count=100,
        char_count=500,
        content=_fixture_title(fixture_path),
    )

    res = await core.export_article(article_id, "epub", force=True)
    warnings = validate_epub_structure(Path(res["file_path"]).read_bytes())
    assert warnings == [], f"{fixture_path.name}: {warnings}"


@pytest.mark.asyncio
async def test_tables_fixture_epub_contains_table(setup_test_environment):
    md = (FIXTURES_DIR / "tables_and_lists.md").read_text(encoding="utf-8")
    library_dir = setup_test_environment["library_dir"]
    file_path = library_dir / "tables_and_lists.md"
    file_path.write_text(md, encoding="utf-8")

    article_id = db.add_article(
        url="https://example.com/tables",
        title="Experimental Results",
        file_path=str(file_path),
        word_count=200,
        char_count=1000,
        content="tables fixture",
    )

    res = await core.export_article(article_id, "epub", force=True)
    with zipfile.ZipFile(res["file_path"], "r") as z:
        xhtml = "".join(
            z.read(n).decode("utf-8")
            for n in z.namelist()
            if n.endswith(".xhtml")
        )

    assert "<table>" in xhtml
    assert "Transformer (base)" in xhtml


@pytest.mark.asyncio
async def test_export_prefers_html_clean_over_md_bridge(setup_test_environment):
    library_dir = setup_test_environment["library_dir"]
    md_path = library_dir / "prefer_html.md"
    md_path.write_text("# Title\n\nFrom markdown.", encoding="utf-8")
    html_clean = md_path.with_suffix(".html_clean")
    html_clean.write_text(
        core.md_to_html_fallback("# Title\n\nFrom cached HTML with $x^2$ math.", "Title"),
        encoding="utf-8",
    )

    article_id = db.add_article(
        url="https://example.com/prefer-html",
        title="Title",
        file_path=str(md_path),
        word_count=5,
        char_count=30,
        content="prefer html cache",
    )

    res = await core.export_article(article_id, "epub", force=True)
    with zipfile.ZipFile(res["file_path"], "r") as z:
        xhtml = z.read("OEBPS/article.xhtml").decode("utf-8")

    assert "From cached HTML" in xhtml
    assert "<math" in xhtml