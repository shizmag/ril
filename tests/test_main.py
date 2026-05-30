import pytest
import sys
from unittest.mock import patch, AsyncMock, MagicMock
from ril import db
from ril.cli import main

def test_cli_stats_empty(setup_test_environment, capsys):
    with patch("sys.argv", ["main.py", "stats"]):
        main()
        captured = capsys.readouterr()
        assert "Library is empty." in captured.out

def test_cli_stats_populated(setup_test_environment, capsys):
    db.add_article(
        url="https://example.com/cli",
        title="CLI Article",
        file_path="/mock.md",
        word_count=45,
        char_count=200,
        content="Testing main.py interface"
    )
    with patch("sys.argv", ["main.py", "stats"]):
        main()
        captured = capsys.readouterr()
        assert "Read It Later Library Stats:" in captured.out
        assert "Total articles:  1" in captured.out
        assert "Total words:     45" in captured.out

def test_cli_list(setup_test_environment, capsys):
    db.add_article("https://url.com", "List Title", "/mock.md", 20, 100, "Content")
    with patch("sys.argv", ["main.py", "list"]):
        main()
        captured = capsys.readouterr()
        assert "[1] [Unread] List Title" in captured.out
        assert "Words: 20" in captured.out

def test_cli_list_empty(setup_test_environment, capsys):
    with patch("sys.argv", ["main.py", "list"]):
        main()
        captured = capsys.readouterr()
        assert "No articles found." in captured.out

def test_cli_search(setup_test_environment, capsys):
    db.add_article("https://url.com", "Search Title", "/mock.md", 20, 100, "Unique keyword here")
    with patch("sys.argv", ["main.py", "search", "keyword"]):
        main()
        captured = capsys.readouterr()
        assert "[1] [Unread] Search Title" in captured.out
        assert "Unique ***keyword***" in captured.out


def test_cli_search_empty(setup_test_environment, capsys):
    with patch("sys.argv", ["main.py", "search", "nonexistent"]):
        main()
        captured = capsys.readouterr()
        assert "No matches found" in captured.out

def test_cli_read_unread(setup_test_environment, capsys):
    art_id = db.add_article("https://url.com", "Read Title", "/mock.md", 20, 100, "Content")
    
    # Mark read
    with patch("sys.argv", ["main.py", "read", str(art_id)]):
        main()
        captured = capsys.readouterr()
        assert f"Article {art_id} marked as read." in captured.out
        
    assert db.get_article(art_id)["status"] == "read"
    
    # Mark unread
    with patch("sys.argv", ["main.py", "unread", str(art_id)]):
        main()
        captured = capsys.readouterr()
        assert f"Article {art_id} marked as unread." in captured.out
        
    assert db.get_article(art_id)["status"] == "unread"
    
    # Non-existent article ID
    with patch("sys.argv", ["main.py", "read", "9999"]):
        main()
        captured = capsys.readouterr()
        assert "Article 9999 not found." in captured.out

def test_cli_add_success(mocker, setup_test_environment, capsys):
    mocker.patch(
        "ril.core.process_url",
        new_callable=AsyncMock,
        return_value={
            "id": 1,
            "title": "CLI Added Title",
            "file_path": "/mock/cli.md",
            "word_count": 150
        }
    )
    with patch("sys.argv", ["main.py", "add", "https://example.com/cli-add"]):
        main()
        captured = capsys.readouterr()
        assert "Success!" in captured.out
        assert "CLI Added Title" in captured.out
        assert "150" in captured.out

def test_cli_add_failure(mocker, setup_test_environment, capsys):
    mocker.patch("ril.core.process_url", new_callable=AsyncMock, side_effect=Exception("Failed crawl"))
    
    with pytest.raises(SystemExit) as exc_info:
        with patch("sys.argv", ["main.py", "add", "https://example.com/fail"]):
            main()
            
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Error: Failed crawl" in captured.err

def test_cli_run_bot(mocker):
    mock_run_bot = mocker.patch("ril.telegram_bot.run_bot")
    with patch("sys.argv", ["main.py", "bot"]):
        main()
        mock_run_bot.assert_called_once()

def test_cli_run_mcp(mocker):
    mock_mcp_run = mocker.patch("ril.mcp_server.mcp.run")
    with patch("sys.argv", ["main.py", "mcp"]):
        main()
        mock_mcp_run.assert_called_once_with(transport="stdio")

def test_cli_delete(mocker, setup_test_environment, capsys):
    mock_delete = mocker.patch("ril.core.delete_article", return_value=True)
    with patch("sys.argv", ["main.py", "delete", "42"]):
        main()
        captured = capsys.readouterr()
        assert "Article 42 successfully deleted." in captured.out
        mock_delete.assert_called_once_with(42)

def test_cli_reset_confirmed(mocker, setup_test_environment, capsys):
    mock_reset = mocker.patch("ril.core.reset_library")
    with patch("sys.argv", ["main.py", "reset", "--yes"]):
        main()
        captured = capsys.readouterr()
        assert "Library and database successfully cleared." in captured.out
        mock_reset.assert_called_once()

def test_cli_reset_aborted(mocker, setup_test_environment, capsys):
    mock_reset = mocker.patch("ril.core.reset_library")
    with patch("builtins.input", return_value="no"):
        with patch("sys.argv", ["main.py", "reset"]):
            main()
            captured = capsys.readouterr()
            assert "Aborted." in captured.out
            mock_reset.assert_not_called()
