import pytest
import os
from unittest.mock import AsyncMock
from ril import db
from ril.mcp_server import (
    process_url,
    search_articles,
    list_articles,
    mark_article_read,
    mark_article_unread,
    get_reading_stats,
    get_article_content,
    delete_article,
    reset_library
)

# ... [unchanged code in between] ...

@pytest.mark.asyncio
async def test_mcp_process_url(mocker, setup_test_environment):
    # Mock core.process_url to avoid network crawl
    mock_process = mocker.patch(
        "ril.core.process_url",
        new_callable=AsyncMock,
        return_value={
            "id": 42,
            "title": "Quantum Computation",
            "file_path": "/mock/quantum.md",
            "word_count": 500,
            "char_count": 2500
        }
    )
    
    response = await process_url("https://example.com/quantum")
    assert "Saved successfully!" in response
    assert "Quantum Computation" in response
    assert "500 words" in response
    from ril.converters import EPUBConverter
    assert isinstance(mock_process.call_args[1]["converter"], EPUBConverter)
    
    # Test valid html format
    response_html = await process_url("https://example.com/quantum", format="html")
    assert "Saved successfully!" in response_html
    # Check that HTMLConverter was passed
    from ril.converters import HTMLConverter
    assert isinstance(mock_process.call_args[1]["converter"], HTMLConverter)

    # Test invalid format
    response_invalid = await process_url("https://example.com/quantum", format="pdf")
    assert "Error: format must be either" in response_invalid

@pytest.mark.asyncio
async def test_mcp_process_url_failure(mocker, setup_test_environment):
    mocker.patch("ril.core.process_url", new_callable=AsyncMock, side_effect=Exception("Connection refused"))
    
    response = await process_url("https://example.com/fail")
    assert "Failed to save URL" in response
    assert "Connection refused" in response

def test_mcp_search_articles(setup_test_environment):
    # Insert test article
    db.add_article(
        url="https://example.com/mcp-art",
        title="MCP Search Test",
        file_path="/mock/mcp.md",
        word_count=50,
        char_count=200,
        content="This is specifically for testing the MCP search tool."
    )
    
    # Positive search
    response = search_articles("specifically")
    assert "Found 1 matching article(s)" in response
    assert "MCP Search Test" in response
    assert "specifically" in response
    
    # Negative search
    response_neg = search_articles("pineapple")
    assert "No articles found matching query" in response_neg

def test_mcp_list_articles(setup_test_environment):
    db.add_article(
        url="https://example.com/list1",
        title="Article 1",
        file_path="/mock/1.md",
        word_count=100,
        char_count=500,
        content="Content 1"
    )
    
    response = list_articles()
    assert "Listing 1 recent articles:" in response
    assert "Article 1" in response
    
    # Invalid filter test
    response_invalid = list_articles(status="invalid_status")
    assert "Error: status filter must be either" in response_invalid

def test_mcp_mark_read_unread(setup_test_environment):
    art_id = db.add_article(
        url="https://example.com/mark",
        title="Mark Test",
        file_path="/mock/mark.md",
        word_count=10,
        char_count=50,
        content="Mark test content"
    )
    
    # Mark read
    res_read = mark_article_read(art_id)
    assert "Successfully marked" in res_read
    assert "read" in res_read
    
    art = db.get_article(art_id)
    assert art["status"] == "read"
    
    # Mark unread
    res_unread = mark_article_unread(art_id)
    assert "Successfully marked" in res_unread
    assert "unread" in res_unread
    
    art = db.get_article(art_id)
    assert art["status"] == "unread"
    
    # Non-existent article
    assert "not found" in mark_article_read(99999)

def test_mcp_get_reading_stats(setup_test_environment):
    # Empty stats
    assert "Your library is empty" in get_reading_stats()
    
    # Add article
    db.add_article(
        url="https://example.com/stats",
        title="Stats Article",
        file_path="/mock/stats.md",
        word_count=200,
        char_count=1000,
        content="Stats article content"
    )
    
    stats_resp = get_reading_stats()
    assert "Read It Later Stats:" in stats_resp
    assert "Total Articles: 1" in stats_resp
    assert "Unread: 1" in stats_resp

def test_mcp_get_article_content(setup_test_environment):
    library_dir = setup_test_environment["library_dir"]
    file_path = library_dir / "article.md"
    
    # Write a dummy file with base64 images and references to tests sandbox
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(
            "# Dummy Content\n"
            "This is a mock article file with inline images:\n"
            "![alt][img_ref_0]\n"
            "And standard link: ![alt2](https://example.com/img.png)\n\n"
            "[img_ref_0]: data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA\n"
        )
        
    art_id = db.add_article(
        url="https://example.com/content",
        title="Content Article",
        file_path=str(file_path),
        word_count=10,
        char_count=50,
        content="Content"
    )
    
    content_resp = get_article_content(art_id)
    assert "Dummy Content" in content_resp
    assert "Content Article" in content_resp
    
    # Assert images/base64 are stripped for reading
    assert "data:image/png" not in content_resp
    assert "img_ref_0" not in content_resp
    assert "https://example.com/img.png" not in content_resp
    
    # Missing file check
    db.add_article(
        url="https://example.com/missing",
        title="Missing File",
        file_path="/mock/nonexistent_file.md",
        word_count=10,
        char_count=50,
        content="Content"
    )
    missing_id = db.get_article_by_url("https://example.com/missing")["id"]
    assert "Error: The file for this article could not be found" in get_article_content(missing_id)
    
    # Non-existent article ID check
    assert "not found" in get_article_content(99999)

def test_mcp_delete_article(mocker, setup_test_environment):
    mock_delete = mocker.patch("ril.core.delete_article", return_value=True)
    resp = delete_article(42)
    mock_delete.assert_called_once_with(42)
    assert "Successfully deleted" in resp

def test_mcp_reset_library(mocker, setup_test_environment):
    mock_reset = mocker.patch("ril.core.reset_library")
    resp = reset_library()
    mock_reset.assert_called_once()
    assert "Library reset successfully" in resp
