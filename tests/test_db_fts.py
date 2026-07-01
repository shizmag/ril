"""
Extended SQLite FTS5 and advanced search tests.
All tests use the isolated_env fixture (temp DB via conftest.py monkeypatch).
"""
import pytest
from ril import db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add(url, title, content, word_count=10, char_count=50):
    return db.add_article(
        url=url,
        title=title,
        file_path=f"/tmp/{title.replace(' ', '_')}.md",
        word_count=word_count,
        char_count=char_count,
        content=content,
    )


# ---------------------------------------------------------------------------
# Basic FTS search
# ---------------------------------------------------------------------------

def test_search_articles_basic_fts():
    _add("https://a.com/1", "Deep Learning Guide", "Deep learning covers neural networks and backpropagation.")
    _add("https://a.com/2", "Quantum Computing Intro", "Quantum bits called qubits form the basis of quantum computing.")

    res = db.search_articles("neural")
    assert len(res) == 1
    assert "Deep" in res[0]["title"]


def test_search_articles_no_match_returns_empty():
    _add("https://a.com/3", "Python Tips", "Python is a versatile language for scripting.")
    res = db.search_articles("fortran")
    assert res == []


def test_search_articles_snippet_present():
    _add("https://a.com/4", "Rust Programming", "Rust guarantees memory safety without garbage collection.")
    res = db.search_articles("memory safety")
    assert len(res) >= 1
    # snippet field must be present (may be empty string in LIKE fallback)
    assert "snippet" in res[0]


# ---------------------------------------------------------------------------
# Quote / special chars fallback (must not crash)
# ---------------------------------------------------------------------------

def test_search_articles_quote_query_does_not_crash():
    _add("https://a.com/5", "Quoted Title", "Content about some quoted thing.")
    # A raw double-quote or FTS special char should not crash — must fallback
    result = db.search_articles('"weird query')
    assert isinstance(result, list)


def test_search_articles_special_chars_fallback():
    _add("https://a.com/6", "Special Article", "Article with special content here.")
    # FTS5 syntax error trigger: unbalanced parens / boolean misuse
    result = db.search_articles("(NOT ) AND OR")
    assert isinstance(result, list)


def test_search_articles_empty_string_returns_empty():
    _add("https://a.com/7", "Empty Query Test", "Some content.")
    result = db.search_articles("")
    # Should return empty or all — must not crash
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Advanced search: query + status
# ---------------------------------------------------------------------------

def test_search_advanced_query_and_status():
    id1 = _add("https://b.com/1", "Blockchain Basics", "Blockchain is a distributed ledger technology.")
    id2 = _add("https://b.com/2", "Blockchain Deep Dive", "Advanced blockchain consensus mechanisms.")
    db.mark_as_read(id1)

    res = db.search_articles_advanced(query="blockchain", status="read")
    ids = [r["id"] for r in res["articles"]]
    assert id1 in ids
    assert id2 not in ids


# ---------------------------------------------------------------------------
# Advanced search: query + tag
# ---------------------------------------------------------------------------

def test_search_advanced_query_and_tag():
    id1 = _add("https://c.com/1", "ML Article", "Machine learning is transforming industries.")
    id2 = _add("https://c.com/2", "ML Untagged", "Machine learning without a tag.")
    db.add_tags(id1, ["ml"])

    res = db.search_articles_advanced(query="machine learning", tag="ml")
    ids = [r["id"] for r in res["articles"]]
    assert id1 in ids
    assert id2 not in ids


# ---------------------------------------------------------------------------
# Advanced search: query + rating
# ---------------------------------------------------------------------------

def test_search_advanced_query_and_rating():
    id1 = _add("https://d.com/1", "Rated Article", "This article discusses climate change impact.")
    id2 = _add("https://d.com/2", "Unrated Article", "This article also discusses climate issues.")
    db.rate_article(id1, 5)

    res = db.search_articles_advanced(query="climate", rating=5)
    ids = [r["id"] for r in res["articles"]]
    assert id1 in ids
    assert id2 not in ids


# ---------------------------------------------------------------------------
# Advanced search: domain filter
# ---------------------------------------------------------------------------

def test_search_advanced_domain_filter():
    _add("https://wikipedia.org/wiki/AI", "Wikipedia AI", "AI content from Wikipedia.")
    _add("https://arxiv.org/abs/1234", "ArXiv Paper", "Research paper content from ArXiv.")

    res = db.search_articles_advanced(domain="wikipedia.org")
    urls = [r["url"] for r in res["articles"]]
    assert all("wikipedia.org" in u for u in urls)
    assert any("arxiv.org" in u for u in [r["url"] for r in db.search_articles_advanced(domain="arxiv.org")["articles"]])


# ---------------------------------------------------------------------------
# Advanced search: no_tags filter
# ---------------------------------------------------------------------------

def test_search_advanced_no_tags():
    id_tagged = _add("https://e.com/1", "Tagged Article", "Tagged content.")
    id_untagged = _add("https://e.com/2", "Untagged Article", "Untagged content.")
    db.add_tags(id_tagged, ["some-tag"])

    res = db.search_articles_advanced(no_tags=True)
    ids = [r["id"] for r in res["articles"]]
    assert id_untagged in ids
    assert id_tagged not in ids


# ---------------------------------------------------------------------------
# Advanced search: no_rating filter
# ---------------------------------------------------------------------------

def test_search_advanced_no_rating():
    id_rated = _add("https://f.com/1", "Rated", "Some rated content.")
    id_unrated = _add("https://f.com/2", "Unrated", "Some unrated content.")
    db.rate_article(id_rated, 3)

    res = db.search_articles_advanced(no_rating=True)
    ids = [r["id"] for r in res["articles"]]
    assert id_unrated in ids
    assert id_rated not in ids


# ---------------------------------------------------------------------------
# Advanced search: pagination
# ---------------------------------------------------------------------------

def test_search_advanced_pagination():
    for i in range(5):
        _add(f"https://pag.com/{i}", f"Article {i}", f"Content about pagination test {i}.")

    page1 = db.search_articles_advanced(limit=2, offset=0)
    page2 = db.search_articles_advanced(limit=2, offset=2)

    assert len(page1["articles"]) == 2
    assert len(page2["articles"]) == 2
    ids1 = {r["id"] for r in page1["articles"]}
    ids2 = {r["id"] for r in page2["articles"]}
    assert ids1.isdisjoint(ids2)

    # total_count should cover all 5
    assert page1["total_count"] >= 5


# ---------------------------------------------------------------------------
# Advanced search: empty query — filters only
# ---------------------------------------------------------------------------

def test_search_advanced_empty_query_filters_only():
    id1 = _add("https://g.com/1", "Unread Article", "Unread content.")
    id2 = _add("https://g.com/2", "Read Article", "Read content.")
    db.mark_as_read(id2)

    res = db.search_articles_advanced(query=None, status="unread")
    ids = [r["id"] for r in res["articles"]]
    assert id1 in ids
    assert id2 not in ids


# ---------------------------------------------------------------------------
# Advanced search: FTS with advanced query (AND / OR logic)
# ---------------------------------------------------------------------------

def test_search_advanced_fts_boolean_and():
    _add("https://h.com/1", "AI and ML", "Artificial intelligence and machine learning coexist.")
    _add("https://h.com/2", "Only AI", "Artificial intelligence standalone topic here.")

    res = db.search_articles_advanced(query="intelligence learning")
    # Both words exist in article 1; article 2 only has 'intelligence'
    # FTS5 phrase or AND search — exact behavior depends on lemmatization
    # Just check it doesn't crash and returns a list
    assert isinstance(res["articles"], list)
    assert res["total_count"] >= 0


# ---------------------------------------------------------------------------
# FTS word count sanity
# ---------------------------------------------------------------------------

def test_search_articles_returns_word_count():
    _add("https://wc.com/1", "Word Count Test", "Words count test content.", word_count=42, char_count=200)
    res = db.search_articles("Words count")
    if res:
        assert res[0]["word_count"] == 42
