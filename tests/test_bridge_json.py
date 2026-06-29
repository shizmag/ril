import pytest
import json
import io
from unittest.mock import patch, AsyncMock
from ril import db
from ril.bridge_json import main

@pytest.mark.asyncio
async def test_bridge_empty_input(capsys):
    with patch("sys.stdin", io.StringIO("")):
        await main()
    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert res["ok"] is False
    assert res["error"]["code"] == "EMPTY_INPUT"

@pytest.mark.asyncio
async def test_bridge_invalid_json(capsys):
    with patch("sys.stdin", io.StringIO("not-json")):
        await main()
    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_JSON"

@pytest.mark.asyncio
async def test_bridge_unknown_command(capsys):
    payload = json.dumps({"command": "nonexistent_cmd", "args": {}})
    with patch("sys.stdin", io.StringIO(payload)):
        await main()
    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert res["ok"] is False
    assert res["error"]["code"] == "UNKNOWN_COMMAND"

@pytest.mark.asyncio
async def test_bridge_get_stats(setup_test_environment, capsys):
    payload = json.dumps({"command": "get_reading_stats", "args": {}})
    with patch("sys.stdin", io.StringIO(payload)):
        await main()
    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert res["ok"] is True
    assert res["data"]["total_articles"] == 0

@pytest.mark.asyncio
async def test_bridge_process_url(mocker, setup_test_environment, capsys):
    mocker.patch(
        "ril.core.process_url",
        new_callable=AsyncMock,
        return_value={
            "id": 42,
            "url": "https://example.com",
            "title": "Quantum",
            "file_path": "/mock.md",
            "word_count": 100,
            "char_count": 500,
            "status": "unread"
        }
    )
    payload = json.dumps({
        "command": "process_url",
        "args": {"url": "https://example.com", "format": "markdown"}
    })
    with patch("sys.stdin", io.StringIO(payload)):
        await main()
    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert res["ok"] is True
    assert res["data"]["id"] == 42
    assert res["data"]["title"] == "Quantum"

@pytest.mark.asyncio
async def test_bridge_mark_read_unread(setup_test_environment, capsys):
    art_id = db.add_article("https://url.com", "Title", "/mock.md", 20, 100, "Content")
    
    # Mark Read
    payload = json.dumps({"command": "mark_article_read", "args": {"article_id": art_id}})
    with patch("sys.stdin", io.StringIO(payload)):
        await main()
    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert res["ok"] is True
    assert res["data"]["success"] is True
    assert db.get_article(art_id)["status"] == "read"

    # Mark Unread
    payload = json.dumps({"command": "mark_article_unread", "args": {"article_id": art_id}})
    with patch("sys.stdin", io.StringIO(payload)):
        await main()
    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert res["ok"] is True
    assert res["data"]["success"] is True
    assert db.get_article(art_id)["status"] == "unread"

@pytest.mark.asyncio
async def test_bridge_search_list_delete_reset(setup_test_environment, capsys, mocker):
    art_id = db.add_article("https://url.com", "Search Title", "/mock.md", 20, 100, "Specific query word")
    
    # Search
    payload = json.dumps({"command": "search_articles", "args": {"query": "Specific", "limit": 10}})
    with patch("sys.stdin", io.StringIO(payload)):
        await main()
    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert res["ok"] is True
    assert len(res["data"]) == 1
    assert res["data"][0]["id"] == art_id

    # List
    payload = json.dumps({"command": "list_articles", "args": {"status": "unread", "limit": 10}})
    with patch("sys.stdin", io.StringIO(payload)):
        await main()
    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert res["ok"] is True
    assert len(res["data"]) == 1

    # Get content
    library_dir = setup_test_environment["library_dir"]
    file_path = library_dir / "mock.md"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("Actual article text content")
    with db.get_db_connection() as conn:
        conn.execute("UPDATE articles SET file_path = ? WHERE id = ?", (str(file_path), art_id))
        conn.commit()

    payload = json.dumps({"command": "get_article_content", "args": {"article_id": art_id}})
    with patch("sys.stdin", io.StringIO(payload)):
        await main()
    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert res["ok"] is True
    assert "Actual article text content" in res["data"]["content"]

    # Delete
    mocker.patch("ril.core.delete_article", return_value=True)
    payload = json.dumps({"command": "delete_article", "args": {"article_id": art_id}})
    with patch("sys.stdin", io.StringIO(payload)):
        await main()
    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert res["ok"] is True
    assert res["data"]["success"] is True

    # Reset
    mocker.patch("ril.core.reset_library")
    payload = json.dumps({"command": "reset_library", "args": {}})
    with patch("sys.stdin", io.StringIO(payload)):
        await main()
    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert res["ok"] is True
    assert res["data"]["success"] is True


@pytest.mark.asyncio
async def test_bridge_process_url_with_force(mocker, setup_test_environment, capsys):
    mock_process = mocker.patch(
        "ril.core.process_url",
        new_callable=AsyncMock,
        return_value={
            "id": 42,
            "url": "https://example.com",
            "title": "Quantum",
            "file_path": "/mock.md",
            "word_count": 100,
            "char_count": 500,
            "status": "unread"
        }
    )
    payload = json.dumps({
        "command": "process_url",
        "args": {"url": "https://example.com", "format": "markdown", "force": True}
    })
    with patch("sys.stdin", io.StringIO(payload)):
        await main()
    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert res["ok"] is True
    mock_process.assert_called_once_with("https://example.com", converter=mocker.ANY, force=True)

