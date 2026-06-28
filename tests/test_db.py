import pytest
from ril import db

def test_add_and_get_article():
    # Insert new article
    art_id = db.add_article(
        url="https://example.com/test",
        title="Test Title",
        file_path="/path/to/test.md",
        word_count=100,
        char_count=500,
        content="Test content is here."
    )
    
    assert art_id is not None
    
    # Retrieve article by ID
    article = db.get_article(art_id)
    assert article is not None
    assert article["id"] == art_id
    assert article["url"] == "https://example.com/test"
    assert article["title"] == "Test Title"
    assert article["status"] == "unread"
    assert article["word_count"] == 100
    
    # Retrieve article by URL
    article_by_url = db.get_article_by_url("https://example.com/test")
    assert article_by_url is not None
    assert article_by_url["id"] == art_id

def test_add_duplicate_url_updates_existing():
    # Insert first version
    art_id_1 = db.add_article(
        url="https://example.com/dup",
        title="Version 1",
        file_path="/path/v1.md",
        word_count=50,
        char_count=200,
        content="Initial content"
    )
    
    # Insert second version (same URL)
    art_id_2 = db.add_article(
        url="https://example.com/dup",
        title="Version 2",
        file_path="/path/v2.md",
        word_count=70,
        char_count=300,
        content="Updated content"
    )
    
    # Assert ID is the same (updates instead of adding new row)
    assert art_id_1 == art_id_2
    
    article = db.get_article(art_id_1)
    assert article["title"] == "Version 2"
    assert article["file_path"] == "/path/v2.md"
    assert article["word_count"] == 70

def test_list_articles():
    # Insert unread article
    db.add_article(
        url="https://example.com/unread",
        title="Unread Page",
        file_path="/path/unread.md",
        word_count=10,
        char_count=50,
        content="Unread context."
    )
    
    # Insert read article
    read_id = db.add_article(
        url="https://example.com/read",
        title="Read Page",
        file_path="/path/read.md",
        word_count=20,
        char_count=100,
        content="Read context."
    )
    db.mark_as_read(read_id, "read")
    
    # List all
    all_arts = db.list_articles()
    assert len(all_arts) == 2
    
    # List unread
    unread_arts = db.list_articles(status="unread")
    assert len(unread_arts) == 1
    assert unread_arts[0]["title"] == "Unread Page"
    
    # List read
    read_arts = db.list_articles(status="read")
    assert len(read_arts) == 1
    assert read_arts[0]["title"] == "Read Page"

def test_mark_as_read_invalid_id():
    success = db.mark_as_read(99999, "read")
    assert not success

def test_get_stats():
    # Initial empty stats
    stats = db.get_stats()
    assert stats["total_articles"] == 0
    assert stats["total_words"] == 0
    
    # Insert an article
    db.add_article(
        url="https://example.com/stats-test",
        title="Stats Title",
        file_path="/path/stats.md",
        word_count=150,
        char_count=600,
        content="Some words to stats."
    )
    
    stats = db.get_stats()
    assert stats["total_articles"] == 1
    assert stats["unread_articles"] == 1
    assert stats["read_articles"] == 0
    assert stats["total_words"] == 150
    assert stats["unread_words"] == 150
    
    # Mark as read
    art = db.list_articles()[0]
    db.mark_as_read(art["id"], "read")
    
    stats = db.get_stats()
    assert stats["read_articles"] == 1
    assert stats["unread_articles"] == 0
    assert stats["read_words"] == 150
    assert stats["unread_words"] == 0

def test_search_articles():
    # Insert articles
    db.add_article(
        url="https://example.com/art1",
        title="Artificial Intelligence",
        file_path="/path/ai.md",
        word_count=50,
        char_count=200,
        content="Artificial intelligence is the intelligence of machines."
    )
    
    db.add_article(
        url="https://example.com/art2",
        title="Quantum Processor",
        file_path="/path/quantum.md",
        word_count=60,
        char_count=250,
        content="Quantum processors work based on qubits instead of bits."
    )
    
    # Search for "intelligence"
    results = db.search_articles("intelligence")
    assert len(results) == 1
    assert results[0]["title"] == "Artificial Intelligence"
    assert "intelligence" in results[0]["snippet"].lower()
    
    # Search for "qubit"
    results_q = db.search_articles("qubits")
    assert len(results_q) == 1
    assert results_q[0]["title"] == "Quantum Processor"
    
    # Search for non-existent word
    results_none = db.search_articles("banana")
    assert len(results_none) == 0

def test_delete_article():
    art_id = db.add_article(
        url="https://example.com/del",
        title="To Delete",
        file_path="/path/del.md",
        word_count=10,
        char_count=50,
        content="Delete this content."
    )
    
    assert db.get_article(art_id) is not None
    
    # Delete it
    success = db.delete_article(art_id)
    assert success
    assert db.get_article(art_id) is None
    
    # Try deleting again
    success_retry = db.delete_article(art_id)
    assert not success_retry


def test_search_articles_lemmatization():
    # Insert article with inflections
    db.add_article(
        url="https://example.com/NLP-test",
        title="Python Crawling Adventures",
        file_path="/path/nlp.md",
        word_count=50,
        char_count=200,
        content="We were running towards the crawling insects while they jumped."
    )
    
    # 1. Search singular form for plural in content ("insect" -> "insects")
    res1 = db.search_articles("insect")
    assert len(res1) == 1
    assert res1[0]["title"] == "Python Crawling Adventures"
    
    # 2. Search base verb form for past tense ("jump" -> "jumped")
    res2 = db.search_articles("jump")
    assert len(res2) == 1
    
    # 3. Search different verb inflection ("run" -> "running")
    res3 = db.search_articles("run")
    assert len(res3) == 1
    
    # 4. Search query with boolean operator ("run AND crawling")
    res4 = db.search_articles("run AND crawling")
    assert len(res4) == 1

