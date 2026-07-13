import pytest
import os
from pathlib import Path
from unittest.mock import AsyncMock
from ril import core, db
from ril.converters import MarkdownConverter

def test_sanitize_filename():
    # Standard English titles
    assert core.sanitize_filename("Hello World!") == "hello_world"
    assert core.sanitize_filename("A  complex__title-here ") == "a_complex_title-here"
    
    # Cyrillic / Russian titles (should preserve letters but lowercase and strip punctuation)
    assert core.sanitize_filename("Квантовые Процессоры!") == "квантовые_процессоры"
    assert core.sanitize_filename("  ИИ и будущее разработки... ") == "ии_и_будущее_разработки"
    
    # Very long title truncation
    long_title = "a" * 100
    assert len(core.sanitize_filename(long_title)) == 60

    # Title that would end with underscore/hyphen after slicing to 60 chars
    title_ending_in_underscore = "Zero Trust для AI-агентов: как безопасно давать LLM доступ к инструментам"
    assert core.sanitize_filename(title_ending_in_underscore) == "zero_trust_для_ai-агентов_как_безопасно_давать_llm_доступ_к"
    
    title_ending_in_hyphen = "a" * 59 + "-" + "b"
    assert core.sanitize_filename(title_ending_in_hyphen) == "a" * 59

@pytest.mark.asyncio
async def test_process_url_creates_directory_if_missing(mocker, setup_test_environment, monkeypatch):
    library_dir = setup_test_environment["library_dir"]
    temp_dir = setup_test_environment["temp_dir"]
    import shutil
    
    # Move the DB path outside of library_dir for this test so we can delete library_dir
    # without deleting the database
    new_db_path = temp_dir / "metadata.db"
    monkeypatch.setattr("ril.config.DB_PATH", new_db_path)
    db.init_db()
    
    # Delete the library directory to simulate it being missing
    if library_dir.exists():
        shutil.rmtree(library_dir)
    assert not library_dir.exists()
    
    # Mock crawler HTML fetching
    mocker.patch("ril.core.fetch_html", new_callable=AsyncMock, return_value="<html>Raw HTML</html>")
    # Mock readability cleaning
    mocker.patch("ril.core.extract_article", return_value=("Test Missing Dir", "<div>Test content</div>"))
    # Mock playwright
    mock_playwright = mocker.patch("ril.core.async_playwright", create=True)
    mock_p = AsyncMock()
    mock_playwright.return_value.__aenter__.return_value = mock_p
    mock_browser = AsyncMock()
    mock_p.chromium.launch.return_value = mock_browser
    mock_page = AsyncMock()
    mock_browser.new_page.return_value = mock_page
    mock_page.evaluate = AsyncMock(side_effect=[None, None, 500])
    mock_page.pdf = AsyncMock()
    
    # Mock convert_pdf_with_marker
    mocker.patch("ril.core.convert_pdf_with_marker", return_value=("# Test Missing Dir\n\nContent.", "Test Missing Dir", {}, {}))
    
    # Process url should recreate the directory and succeed
    result = await core.process_url("https://example.com/test-missing-dir", converter=MarkdownConverter())
    
    assert library_dir.exists()
    assert (library_dir / "images").exists()
    assert os.path.exists(result["file_path"])

@pytest.mark.asyncio
async def test_process_url_pipeline(mocker, setup_test_environment):
    library_dir = setup_test_environment["library_dir"]
    
    # Mock crawler HTML fetching
    mocker.patch("ril.core.fetch_html", new_callable=AsyncMock, return_value="<html>Raw HTML</html>")
    
    # Mock readability cleaning
    mocker.patch("ril.core.extract_article", return_value=("Квантовые процессоры", "<div>Квантовые процессоры Текст статьи</div>"))
    
    # Mock playwright
    mock_playwright = mocker.patch("ril.core.async_playwright", create=True)
    mock_p = AsyncMock()
    mock_playwright.return_value.__aenter__.return_value = mock_p
    mock_browser = AsyncMock()
    mock_p.chromium.launch.return_value = mock_browser
    mock_page = AsyncMock()
    mock_browser.new_page.return_value = mock_page
    mock_page.evaluate = AsyncMock(side_effect=[None, None, 500])
    mock_page.pdf = AsyncMock()

    # Mock convert_pdf_with_marker
    mocker.patch(
        "ril.core.convert_pdf_with_marker",
        return_value=("# Квантовые процессоры\n\nТекст статьи.", "Квантовые процессоры", {}, {})
    )
    
    # Run the pipeline
    result = await core.process_url("https://habr.com/ru/articles/12345/", converter=MarkdownConverter())
    
    # Assertions on pipeline result
    assert result["title"] == "Квантовые процессоры"
    assert result["url"] == "https://habr.com/ru/articles/12345/"
    assert result["word_count"] == 4  # "Квантовые", "процессоры", "Текст", "статьи" (regular words)
    assert result["status"] == "unread"
    
    # Check that file was written to the isolated vault
    file_path = result["file_path"]
    assert os.path.exists(file_path)
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
        assert "Квантовые процессоры Текст статьи" in content
        
    # Check that it's in the database
    db_article = db.get_article(result["id"])
    assert db_article is not None
    assert db_article["title"] == "Квантовые процессоры"
    assert db_article["url"] == "https://habr.com/ru/articles/12345/"

def test_delete_article(setup_test_environment):
    library_dir = setup_test_environment["library_dir"]
    
    # 1. Add mock article
    article_id = db.add_article(
        url="https://delete-me.com",
        title="Delete Me Title",
        file_path=str(library_dir / "test_delete.md"),
        word_count=100,
        char_count=500,
        content="Test delete content"
    )
    
    # Create the markdown file and mock image directory
    file_path = library_dir / "test_delete.md"
    file_path.write_text("Markdown content")
    
    img_dir = library_dir / "images" / "test_delete"
    img_dir.mkdir(parents=True, exist_ok=True)
    (img_dir / "test_img.jpg").write_text("image bytes")
    
    assert file_path.exists()
    assert img_dir.exists()
    
    # 2. Call core delete
    success = core.delete_article(article_id)
    assert success is True
    
    # 3. Assertions: DB and files deleted
    assert db.get_article(article_id) is None
    assert not file_path.exists()
    assert not img_dir.exists()

def test_reset_library(setup_test_environment):
    library_dir = setup_test_environment["library_dir"]
    
    # 1. Add mock articles
    db.add_article("https://url1.com", "Art 1", str(library_dir / "art1.md"), 10, 50, "Content 1")
    db.add_article("https://url2.com", "Art 2", str(library_dir / "art2.md"), 20, 100, "Content 2")
    
    (library_dir / "art1.md").write_text("art1 content")
    (library_dir / "art2.md").write_text("art2 content")
    
    img1_dir = library_dir / "images" / "art1"
    img1_dir.mkdir(parents=True, exist_ok=True)
    (img1_dir / "img1.jpg").write_text("img1 bytes")
    
    img2_dir = library_dir / "images" / "art2"
    img2_dir.mkdir(parents=True, exist_ok=True)
    (img2_dir / "img2.jpg").write_text("img2 bytes")
    
    # Call reset
    core.reset_library()
    
    # Assertions
    assert db.get_stats()["total_articles"] == 0
    assert not (library_dir / "art1.md").exists()
    assert not (library_dir / "art2.md").exists()
    assert not img1_dir.exists()
    assert not img2_dir.exists()
    assert (library_dir / "images").exists() # The images directory itself should exist
    assert setup_test_environment["db_path"].exists() # The database file itself should still exist

@pytest.mark.asyncio
async def test_process_url_pdf(mocker, setup_test_environment):
    """PDF pipeline using mocked convert_pdf_with_marker (no real marker-pdf)."""
    library_dir = setup_test_environment["library_dir"]
    fake_pdf = library_dir / "temp.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    mocker.patch("ril.core.download_pdf", return_value=fake_pdf)

    from PIL import Image
    mock_img = Image.new("RGB", (100, 100))

    mocker.patch(
        "ril.core.convert_pdf_with_marker",
        return_value=(
            "# Mock PDF Title\n\nSome text.\n\n![image-0.jpg](image-0.jpg)",
            "Mock PDF Title",
            {"image-0.jpg": mock_img},
            {},
        ),
    )

    result = await core.process_url("https://example.com/test-paper.pdf")

    assert result["title"] == "Mock PDF Title"
    assert result["url"] == "https://example.com/test-paper.pdf"
    assert result["status"] == "unread"
    assert result["file_path"].endswith(".epub")

    epub_path = Path(result["file_path"])
    assert epub_path.exists()
    md_path = epub_path.with_suffix(".md")
    assert md_path.exists()
    content = md_path.read_text(encoding="utf-8")
    assert "# Mock PDF Title" in content
    assert "![image-0.jpg][img_ref_0]" in content
    assert "[img_ref_0]: data:image/jpeg;base64," in content

    db_article = db.get_article(result["id"])
    assert db_article is not None
    assert db_article["title"] == "Mock PDF Title"


@pytest.mark.asyncio
async def test_process_url_pdf_disabled_images(mocker, monkeypatch, setup_test_environment):
    """PDF pipeline with DISABLE_IMAGES=True — images must be stripped."""
    from ril import core, config
    monkeypatch.setattr(config, "DISABLE_IMAGES", True)

    library_dir = setup_test_environment["library_dir"]
    fake_pdf = library_dir / "temp_no_img.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    mocker.patch("ril.core.download_pdf", return_value=fake_pdf)

    from PIL import Image
    mock_img = Image.new("RGB", (100, 100))

    mocker.patch(
        "ril.core.convert_pdf_with_marker",
        return_value=(
            "# Mock PDF Title\n\nSome text.\n\n![image-0.jpg](image-0.jpg)",
            "Mock PDF Title",
            {"image-0.jpg": mock_img},
            {},
        ),
    )

    result = await core.process_url("https://example.com/test-paper.pdf")

    assert result["title"] == "Mock PDF Title"

    md_path = Path(result["file_path"]).with_suffix(".md")
    assert md_path.exists()
    content = md_path.read_text(encoding="utf-8")
    assert "# Mock PDF Title" in content
    assert "image-0.jpg" not in content
    assert "img_ref_0" not in content
    assert "data:image/jpeg;base64," not in content



@pytest.mark.asyncio
async def test_process_url_duplicate_check(mocker, setup_test_environment):
    library_dir = setup_test_environment["library_dir"]
    
    # Mock crawler HTML fetching
    mocker.patch("ril.core.fetch_html", new_callable=AsyncMock, return_value="<html>Raw HTML</html>")
    
    # Mock readability cleaning
    mocker.patch("ril.core.extract_article", return_value=("Duplicate Title", "<div>Content</div>"))
    
    # Mock playwright
    mock_playwright = mocker.patch("ril.core.async_playwright", create=True)
    mock_p = AsyncMock()
    mock_playwright.return_value.__aenter__.return_value = mock_p
    mock_browser = AsyncMock()
    mock_p.chromium.launch.return_value = mock_browser
    mock_page = AsyncMock()
    mock_browser.new_page.return_value = mock_page
    mock_page.evaluate = AsyncMock(side_effect=[None, None, 500])
    mock_page.pdf = AsyncMock()

    # Mock convert_pdf_with_marker
    mocker.patch("ril.core.convert_pdf_with_marker", return_value=("# Duplicate Title\n\nContent.", "Duplicate Title", {}, {}))
    
    # First time processing should succeed
    url = "https://example.com/duplicate-test"
    res1 = await core.process_url(url)
    assert res1["title"] == "Duplicate Title"
    
    # Second time processing without force should raise ValueError
    with pytest.raises(ValueError) as excinfo:
        await core.process_url(url)
    assert "already exists in library" in str(excinfo.value)
    
    # Second time processing with force should succeed
    res2 = await core.process_url(url, force=True)
    assert res2["title"] == "Duplicate Title"
    assert res2["id"] == res1["id"]


@pytest.mark.asyncio
async def test_process_url_routing_logic(mocker, setup_test_environment):
    from ril.converters import MarkdownConverter
    # Mock crawler HTML fetching
    mocker.patch("ril.core.fetch_html", new_callable=AsyncMock, return_value="<html>Raw HTML</html>")
    
    # Mock readability cleaning
    mocker.patch("ril.core.extract_article", return_value=("Web Page Title", "<div>Web body</div>"))
    
    # Mock playwright
    mock_playwright = mocker.patch("ril.core.async_playwright", create=True)
    mock_p = AsyncMock()
    mock_playwright.return_value.__aenter__.return_value = mock_p
    mock_browser = AsyncMock()
    mock_p.chromium.launch.return_value = mock_browser
    mock_page = AsyncMock()
    mock_browser.new_page.return_value = mock_page
    mock_page.evaluate = AsyncMock(side_effect=[None, None, 500])
    mock_page.pdf = AsyncMock()

    # Mock convert_pdf_with_marker
    mock_marker = mocker.patch("ril.core.convert_pdf_with_marker", return_value=("# Web Page Title\n\nContent.", "Web Page Title", {}, {}))
    
    from pathlib import Path
    mocker.patch("ril.core.download_pdf", lambda url: Path("/fake/path.pdf"))
    mocker.patch("ril.core.is_pdf_file", return_value=True)
    
    # 1. Processing a standard web page
    web_url = "https://example.com/math-page"
    web_result = await core.process_url(web_url, converter=MarkdownConverter())
    assert web_result["title"] == "Web Page Title"
    assert not mock_marker.called
    
    # Reset mocks
    mock_marker.reset_mock()
    
    # 2. Processing an arXiv PDF link (e.g. arXiv link or direct PDF)
    pdf_url = "https://arxiv.org/pdf/1706.03762"
    mocker.patch("pathlib.Path.exists", return_value=True)
    mocker.patch("pathlib.Path.unlink", return_value=None)
    
    pdf_result = await core.process_url(pdf_url)
    assert pdf_result["title"] == "Web Page Title"
    assert mock_marker.called


