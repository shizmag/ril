import pytest
import os
from unittest.mock import AsyncMock
from ril import core, db

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

@pytest.mark.asyncio
async def test_process_url_pipeline(mocker, setup_test_environment):
    library_dir = setup_test_environment["library_dir"]
    
    # Mock crawler HTML fetching
    mocker.patch("ril.core.fetch_html", new_callable=AsyncMock, return_value="<html>Raw HTML</html>")
    
    # Mock readability cleaning
    mocker.patch("ril.core.extract_article", return_value=("Квантовые процессоры", "<div>Clean article</div>"))
    
    # Mock converter (so we don't try to download mocked images)
    mock_convert = AsyncMock(return_value="# Квантовые процессоры\n\nТекст статьи.")
    mocker.patch("ril.converters.MarkdownConverter.convert", mock_convert)
    
    # Run the pipeline
    result = await core.process_url("https://habr.com/ru/articles/12345/")
    
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
        assert "# Квантовые процессоры" in content
        
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
