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
