import sqlite3
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from ril import config

_nlp = None

def get_nlp():
    global _nlp
    if _nlp is None:
        import spacy
        try:
            _nlp = spacy.load("en_core_web_sm", disable=["parser", "ner"])
        except OSError:
            import contextlib
            import sys
            from spacy.cli import download
            with contextlib.redirect_stdout(sys.stderr):
                download("en_core_web_sm")
            _nlp = spacy.load("en_core_web_sm", disable=["parser", "ner"])
    return _nlp

def lemmatize_text(text: str) -> str:
    """Lemmatize English text using spaCy en_core_web_sm."""
    if not text:
        return text
    try:
        nlp = get_nlp()
        doc = nlp(text)
        lemmas = [t.lemma_ + t.whitespace_ for t in doc]
        return "".join(lemmas)
    except Exception:
        return text

def lemmatize_query(query: str) -> str:
    """Lemmatize the search query while preserving FTS5 boolean operators and syntax."""
    if not query:
        return query
    try:
        nlp = get_nlp()
        doc = nlp(query)
        lemmas = []
        for token in doc:
            if token.text in ("AND", "OR", "NOT", "NEAR"):
                lemmas.append(token.text + token.whitespace_)
            else:
                lemmas.append(token.lemma_ + token.whitespace_)
        return "".join(lemmas)
    except Exception:
        return query


def get_db_connection() -> sqlite3.Connection:
    """Establish a connection to the SQLite database."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    """Initialize the database tables if they do not exist."""
    # Ensure DB directory exists
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Metadata table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE,
                title TEXT NOT NULL,
                added_at TEXT NOT NULL,
                status TEXT DEFAULT 'unread' CHECK(status IN ('unread', 'read')),
                file_path TEXT NOT NULL,
                word_count INTEGER DEFAULT 0,
                char_count INTEGER DEFAULT 0,
                rating INTEGER DEFAULT NULL CHECK(rating IS NULL OR (rating >= 1 AND rating <= 5)),
                comment TEXT DEFAULT NULL
            )
        """)

        # Add missing columns if database was initialized with an older schema
        cursor.execute("PRAGMA table_info(articles)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'rating' not in columns:
            try:
                cursor.execute("ALTER TABLE articles ADD COLUMN rating INTEGER DEFAULT NULL CHECK(rating IS NULL OR (rating >= 1 AND rating <= 5))")
            except Exception as e:
                print(f"Error adding rating column: {e}")
        if 'comment' not in columns:
            try:
                cursor.execute("ALTER TABLE articles ADD COLUMN comment TEXT DEFAULT NULL")
            except Exception as e:
                print(f"Error adding comment column: {e}")

        # Tags table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS article_tags (
                article_id INTEGER,
                tag TEXT,
                PRIMARY KEY (article_id, tag),
                FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
            )
        """)
        
        # Full-text search table (FTS5)
        # SQLite's FTS5 is a virtual table that enables fast text search.
        # We index the title and the clean markdown content.
        try:
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
                    article_id UNINDEXED,
                    title,
                    content
                )
            """)
        except sqlite3.OperationalError as e:
            # Fallback if FTS5 is somehow not supported, though it's standard in modern Python/SQLite.
            print(f"Error initializing FTS5 table: {e}. Fallback to manual SQLite search.")
            
        conn.commit()

def add_article(
    url: str,
    title: str,
    file_path: str,
    word_count: int,
    char_count: int,
    content: str
) -> int:
    """
    Insert a new article's metadata and content.
    If the URL already exists, it updates the existing entry.
    Returns the article ID.
    """
    added_at = datetime.datetime.now().isoformat()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Check if URL already exists
        cursor.execute("SELECT id, file_path FROM articles WHERE url = ?", (url,))
        existing = cursor.fetchone()
        
        if existing:
            article_id = existing[0]
            # Delete old FTS5 entry to avoid duplicates
            cursor.execute("DELETE FROM articles_fts WHERE article_id = ?", (article_id,))
            
            # Update article metadata
            cursor.execute("""
                UPDATE articles 
                SET title = ?, added_at = ?, file_path = ?, word_count = ?, char_count = ?
                WHERE id = ?
            """, (title, added_at, file_path, word_count, char_count, article_id))
        else:
            # Insert new metadata
            cursor.execute("""
                INSERT INTO articles (url, title, added_at, file_path, word_count, char_count)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (url, title, added_at, file_path, word_count, char_count))
            article_id = cursor.lastrowid
        
        # Clean content for FTS: strip base64 image reference blocks to avoid indexing gibberish
        import re
        fts_content = re.sub(r'(?m)^\[img_ref_\d+\].*$', '', content)
        
        # Lemmatize title and content for better FTS5 matching
        lemmatized_title = lemmatize_text(title)
        lemmatized_content = lemmatize_text(fts_content)
        
        # Insert into FTS5
        cursor.execute("""
            INSERT INTO articles_fts (article_id, title, content)
            VALUES (?, ?, ?)
        """, (article_id, lemmatized_title, lemmatized_content))
        
        conn.commit()
        return article_id

def get_article_tags(article_id: int) -> List[str]:
    """Retrieve all tags associated with an article."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT tag FROM article_tags WHERE article_id = ?", (article_id,))
        return [row[0] for row in cursor.fetchall()]

def get_article(article_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve an article's metadata by its ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM articles WHERE id = ?", (article_id,))
        row = cursor.fetchone()
        if row:
            res = dict(row)
            res["tags"] = get_article_tags(article_id)
            return res
        return None

def get_article_by_url(url: str) -> Optional[Dict[str, Any]]:
    """Retrieve an article's metadata by its URL."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM articles WHERE url = ?", (url,))
        row = cursor.fetchone()
        if row:
            res = dict(row)
            res["tags"] = get_article_tags(res["id"])
            return res
        return None

def list_articles(status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """List articles, optionally filtering by status ('read' or 'unread')."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if status:
            cursor.execute(
                "SELECT * FROM articles WHERE status = ? ORDER BY added_at DESC LIMIT ?",
                (status, limit)
            )
        else:
            cursor.execute(
                "SELECT * FROM articles ORDER BY added_at DESC LIMIT ?",
                (limit,)
            )
        return [dict(row) for row in cursor.fetchall()]

def mark_as_read(article_id: int, status: str = 'read') -> bool:
    """Mark an article as read or unread."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE articles SET status = ? WHERE id = ?",
            (status, article_id)
        )
        conn.commit()
        return cursor.rowcount > 0

def get_stats() -> Dict[str, Any]:
    """Retrieve reading statistics."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM articles")
        total = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM articles WHERE status = 'read'")
        read_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM articles WHERE status = 'unread'")
        unread_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT SUM(word_count) FROM articles")
        total_words = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT SUM(word_count) FROM articles WHERE status = 'read'")
        read_words = cursor.fetchone()[0] or 0
        
        return {
            "total_articles": total,
            "read_articles": read_count,
            "unread_articles": unread_count,
            "total_words": total_words,
            "read_words": read_words,
            "unread_words": total_words - read_words,
            "avg_words_per_article": round(total_words / total, 1) if total > 0 else 0,
        }

def search_articles(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Search articles matching the search query using SQLite FTS5.
    Returns matching metadata and relevant text snippets.
    """
    lemmatized_query = lemmatize_query(query)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # We search in articles_fts, ordering by relevance (rank)
            # and extracting snippets of the content that match the search term
            cursor.execute("""
                SELECT 
                    a.id, 
                    a.url, 
                    a.title, 
                    a.added_at, 
                    a.status, 
                    a.file_path,
                    a.word_count,
                    snippet(articles_fts, 2, '***', '***', '...', 20) as snippet
                FROM articles_fts f
                JOIN articles a ON f.article_id = a.id
                WHERE articles_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (lemmatized_query, limit))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.OperationalError:
            # If the query contains special character syntax that FTS5 fails to parse, 
            # we escape it by surrounding words with quotes or falling back to simple search.
            safe_query = f'"{query.replace('"', ' ')}"'
            lemmatized_safe_query = lemmatize_query(safe_query)
            try:
                cursor.execute("""
                    SELECT 
                        a.id, 
                        a.url, 
                        a.title, 
                        a.added_at, 
                        a.status, 
                        a.file_path,
                        a.word_count,
                        snippet(articles_fts, 2, '***', '***', '...', 20) as snippet
                    FROM articles_fts f
                    JOIN articles a ON f.article_id = a.id
                    WHERE articles_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (lemmatized_safe_query, limit))
                return [dict(row) for row in cursor.fetchall()]
            except sqlite3.OperationalError:
                # Fallback to simple LIKE search if FTS5 query fails completely
                cursor.execute("""
                    SELECT id, url, title, added_at, status, file_path, word_count, '' as snippet
                    FROM articles
                    WHERE title LIKE ?
                    ORDER BY added_at DESC
                    LIMIT ?
                """, (f"%{query}%", limit))
                return [dict(row) for row in cursor.fetchall()]

def delete_article(article_id: int) -> bool:
    """Delete an article from database and remove its FTS entry."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Get file path to return for file deletion (caller will handle files)
        cursor.execute("SELECT file_path FROM articles WHERE id = ?", (article_id,))
        row = cursor.fetchone()
        
        cursor.execute("DELETE FROM articles WHERE id = ?", (article_id,))
        success = cursor.rowcount > 0
        cursor.execute("DELETE FROM articles_fts WHERE article_id = ?", (article_id,))
        cursor.execute("DELETE FROM article_tags WHERE article_id = ?", (article_id,))
        conn.commit()
        return success

def add_tags(article_id: int, tags: List[str]) -> None:
    """Add multiple tags to an article."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for tag in tags:
            tag_clean = tag.strip()
            if tag_clean:
                cursor.execute(
                    "INSERT OR IGNORE INTO article_tags (article_id, tag) VALUES (?, ?)",
                    (article_id, tag_clean)
                )
        conn.commit()

def remove_tag(article_id: int, tag: str) -> bool:
    """Remove a tag from an article."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM article_tags WHERE article_id = ? AND tag = ?",
            (article_id, tag.strip())
        )
        conn.commit()
        return cursor.rowcount > 0

def list_tags() -> List[Dict[str, Any]]:
    """List all tags and their counts."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT tag, COUNT(*) as count 
            FROM article_tags 
            GROUP BY tag 
            ORDER BY count DESC, tag ASC
        """)
        return [dict(row) for row in cursor.fetchall()]

def rate_article(article_id: int, rating: Optional[int]) -> bool:
    """Set the rating (1-5) for an article, or None to clear it."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE articles SET rating = ? WHERE id = ?",
            (rating, article_id)
        )
        conn.commit()
        return cursor.rowcount > 0

def set_article_comment(article_id: int, comment: Optional[str]) -> bool:
    """Set or update the comment for an article."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE articles SET comment = ? WHERE id = ?",
            (comment, article_id)
        )
        conn.commit()
        return cursor.rowcount > 0

def get_extended_stats() -> Dict[str, Any]:
    """Retrieve extended reading and library statistics."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        base_stats = get_stats()
        
        cursor.execute("""
            SELECT COUNT(*) FROM articles a 
            WHERE NOT EXISTS (SELECT 1 FROM article_tags WHERE article_id = a.id)
        """)
        no_tags = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM articles WHERE rating IS NULL")
        no_rating = cursor.fetchone()[0]
        
        cursor.execute("SELECT AVG(rating) FROM articles WHERE rating IS NOT NULL")
        avg_rating = cursor.fetchone()[0] or 0.0
        
        cursor.execute("""
            SELECT id, url, title, rating, status, word_count, added_at, file_path 
            FROM articles 
            WHERE rating IS NOT NULL 
            ORDER BY rating DESC, added_at DESC 
            LIMIT 5
        """)
        top_articles = [dict(row) for row in cursor.fetchall()]
        for a in top_articles:
            a["tags"] = get_article_tags(a["id"])
            
        base_stats.update({
            "no_tags_count": no_tags,
            "no_rating_count": no_rating,
            "avg_rating": round(avg_rating, 2),
            "top_articles": top_articles
        })
        return base_stats

def get_sources_stats(limit: int = 10) -> List[Dict[str, Any]]:
    """Retrieve counts of articles grouped by domain."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT url FROM articles")
        urls = [row[0] for row in cursor.fetchall()]
        
    from urllib.parse import urlparse
    domains = {}
    for url in urls:
        try:
            domain = urlparse(url).netloc
            if domain.startswith("www."):
                domain = domain[4:]
            if domain:
                domains[domain] = domains.get(domain, 0) + 1
        except Exception:
            pass
            
    sorted_domains = sorted(domains.items(), key=lambda x: x[1], reverse=True)
    return [{"domain": d, "count": c} for d, c in sorted_domains[:limit]]

def get_tags_stats() -> List[Dict[str, Any]]:
    """Retrieve stats for tags."""
    return list_tags()

def get_ratings_stats() -> Dict[str, int]:
    """Retrieve article counts grouped by rating value (1-5)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT rating, COUNT(*) as count 
            FROM articles 
            WHERE rating IS NOT NULL 
            GROUP BY rating
        """)
        rows = cursor.fetchall()
        
    counts = {str(i): 0 for i in range(1, 6)}
    for r in rows:
        counts[str(r[0])] = r[1]
    return counts

def get_dynamics_stats() -> Dict[str, int]:
    """Retrieve article count added today, this week, and this month."""
    now = datetime.datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    week_start = (now - datetime.timedelta(days=7)).isoformat()
    month_start = (now - datetime.timedelta(days=30)).isoformat()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM articles WHERE added_at >= ?", (today_start,))
        added_today = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM articles WHERE added_at >= ?", (week_start,))
        added_week = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM articles WHERE added_at >= ?", (month_start,))
        added_month = cursor.fetchone()[0]
        
    return {
        "today": added_today,
        "week": added_week,
        "month": added_month
    }

def search_articles_advanced(
    query: Optional[str] = None,
    status: Optional[str] = None,
    tag: Optional[str] = None,
    rating: Optional[int] = None,
    domain: Optional[str] = None,
    no_tags: bool = False,
    no_rating: bool = False,
    date_added: Optional[str] = None,
    limit: int = 10,
    offset: int = 0
) -> Dict[str, Any]:
    """Advanced search and filtering of articles with pagination."""
    conditions = []
    params = []
    
    if status:
        conditions.append("a.status = ?")
        params.append(status)
        
    if no_rating:
        conditions.append("a.rating IS NULL")
    elif rating is not None:
        conditions.append("a.rating = ?")
        params.append(rating)
        
    if domain:
        conditions.append("a.url LIKE ?")
        params.append(f"%{domain}%")
        
    if no_tags:
        conditions.append("NOT EXISTS (SELECT 1 FROM article_tags WHERE article_id = a.id)")
    elif tag:
        conditions.append("EXISTS (SELECT 1 FROM article_tags WHERE article_id = a.id AND tag = ?)")
        params.append(tag)
        
    if date_added:
        now = datetime.datetime.now()
        if date_added == "today":
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        elif date_added == "week":
            start_date = (now - datetime.timedelta(days=7)).isoformat()
        elif date_added == "month":
            start_date = (now - datetime.timedelta(days=30)).isoformat()
        else:
            start_date = None
        if start_date:
            conditions.append("a.added_at >= ?")
            params.append(start_date)
            
    if query:
        lemmatized_query = lemmatize_query(query)
        fts_where = "f.articles_fts MATCH ?"
        fts_params = [lemmatized_query]
        
        all_conditions = [fts_where] + conditions
        all_params = fts_params + params
        
        where_clause = "WHERE " + " AND ".join(all_conditions)
        
        count_sql = f"""
            SELECT COUNT(*) 
            FROM articles_fts f
            JOIN articles a ON f.article_id = a.id
            {where_clause}
        """
        
        data_sql = f"""
            SELECT 
                a.id, 
                a.url, 
                a.title, 
                a.added_at, 
                a.status, 
                a.file_path,
                a.word_count,
                a.char_count,
                a.rating,
                a.comment,
                snippet(articles_fts, 2, '***', '***', '...', 20) as snippet
            FROM articles_fts f
            JOIN articles a ON f.article_id = a.id
            {where_clause}
            ORDER BY rank
            LIMIT ? OFFSET ?
        """
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(count_sql, all_params)
                total_count = cursor.fetchone()[0]
                
                cursor.execute(data_sql, all_params + [limit, offset])
                rows = cursor.fetchall()
            except sqlite3.OperationalError:
                safe_query = f'"{query.replace('"', ' ')}"'
                lemmatized_safe = lemmatize_query(safe_query)
                all_params_safe = [lemmatized_safe] + params
                try:
                    cursor.execute(count_sql, all_params_safe)
                    total_count = cursor.fetchone()[0]
                    cursor.execute(data_sql, all_params_safe + [limit, offset])
                    rows = cursor.fetchall()
                except sqlite3.OperationalError:
                    like_cond = "a.title LIKE ?"
                    like_param = [f"%{query}%"]
                    
                    all_like_conds = [like_cond] + conditions
                    all_like_params = like_param + params
                    
                    where_like = "WHERE " + " AND ".join(all_like_conds)
                    
                    count_like = f"SELECT COUNT(*) FROM articles a {where_like}"
                    data_like = f"""
                        SELECT a.id, a.url, a.title, a.added_at, a.status, a.file_path,
                               a.word_count, a.char_count, a.rating, a.comment, '' as snippet
                        FROM articles a
                        {where_like}
                        ORDER BY a.added_at DESC
                        LIMIT ? OFFSET ?
                    """
                    cursor.execute(count_like, all_like_params)
                    total_count = cursor.fetchone()[0]
                    cursor.execute(data_like, all_like_params + [limit, offset])
                    rows = cursor.fetchall()
    else:
        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)
            
        count_sql = f"SELECT COUNT(*) FROM articles a {where_clause}"
        data_sql = f"""
            SELECT a.id, a.url, a.title, a.added_at, a.status, a.file_path,
                   a.word_count, a.char_count, a.rating, a.comment, '' as snippet
            FROM articles a
            {where_clause}
            ORDER BY a.added_at DESC
            LIMIT ? OFFSET ?
        """
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(count_sql, params)
            total_count = cursor.fetchone()[0]
            
            cursor.execute(data_sql, params + [limit, offset])
            rows = cursor.fetchall()
            
    articles = []
    for r in rows:
        d = dict(r)
        d["tags"] = get_article_tags(d["id"])
        articles.append(d)
        
    return {
        "articles": articles,
        "total_count": total_count
    }
