"""
Golden fixture corpus for marker-pdf-style markdown outputs (R5).

These tests assert fixture existence and expected content markers only.
No EPUB or conversion pipeline logic is exercised here.
"""
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "marker_outputs"

EXPECTED_FIXTURES = [
    "multi_section_paper.md",
    "math_heavy.md",
    "figures_and_captions.md",
    "tables_and_lists.md",
    "reference_images.md",
]


@pytest.fixture
def fixture_paths():
    return {name: FIXTURES_DIR / name for name in EXPECTED_FIXTURES}


def test_fixtures_directory_exists():
    assert FIXTURES_DIR.is_dir(), f"Missing fixtures directory: {FIXTURES_DIR}"


@pytest.mark.parametrize("filename", EXPECTED_FIXTURES)
def test_fixture_file_exists(filename):
    path = FIXTURES_DIR / filename
    assert path.is_file(), f"Missing fixture: {path}"


@pytest.mark.parametrize("filename", EXPECTED_FIXTURES)
def test_fixture_is_non_empty(filename):
    path = FIXTURES_DIR / filename
    content = path.read_text(encoding="utf-8")
    assert len(content.strip()) > 50, f"Fixture too short: {filename}"


def test_multi_section_paper_structure(fixture_paths):
    content = fixture_paths["multi_section_paper.md"].read_text(encoding="utf-8")
    h1_count = content.count("\n# ")
    assert h1_count >= 3, "Expected at least 3 h1 sections"
    assert "## " in content, "Expected h2 subsections"
    assert "$h_t$" in content or "$d_k$" in content, "Expected inline math"
    assert "$$PE_" in content, "Expected display math"


def test_math_heavy_delimiters(fixture_paths):
    content = fixture_paths["math_heavy.md"].read_text(encoding="utf-8")
    assert "$x^2$" in content or "$E = mc^2$" in content, "Expected inline $ math"
    assert "$$E = mc^2$$" in content, "Expected display $$ math"
    assert r"\(" in content and r"\)" in content, "Expected \\( \\) delimiters"
    assert r"\[" in content and r"\]" in content, "Expected \\[ \\] delimiters"
    assert "```python" in content, "Expected fenced code block"
    assert 'f"${price' in content or "cost = $19.99" in content, (
        "Expected literal $ in code blocks"
    )


def test_figures_and_captions_markers(fixture_paths):
    content = fixture_paths["figures_and_captions.md"].read_text(encoding="utf-8")
    assert "![Figure 1][img_ref_0]" in content, "Expected reference-style image"
    assert "*Figure 1:" in content, "Expected italic caption"
    assert "[img_ref_0]: data:image/png;base64," in content, "Expected image definition"
    assert content.count("![Figure") >= 2, "Expected multiple figure references"


def test_tables_and_lists_markers(fixture_paths):
    content = fixture_paths["tables_and_lists.md"].read_text(encoding="utf-8")
    assert "| Model |" in content or "|-------|" in content, "Expected markdown table"
    assert "\n- " in content, "Expected unordered list"
    assert "\n1. " in content, "Expected ordered list"
    assert "*Table 1:" in content, "Expected table caption"


def test_reference_images_minimal(fixture_paths):
    content = fixture_paths["reference_images.md"].read_text(encoding="utf-8")
    assert "![Diagram][img_ref_0]" in content
    assert "[img_ref_0]: data:image/png;base64," in content
    # Tiny valid 1x1 PNG base64 used across the project
    assert "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ" in content