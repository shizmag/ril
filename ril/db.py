import sqlite3
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from ril import config

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
                char_count INTEGER DEFAULT 0
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
        
        # Insert into FTS5
        cursor.execute("""
            INSERT INTO articles_fts (article_id, title, content)
            VALUES (?, ?, ?)
        """, (article_id, title, content))
        
        conn.commit()
        return article_id

def get_article(article_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve an article's metadata by its ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM articles WHERE id = ?", (article_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_article_by_url(url: str) -> Optional[Dict[str, Any]]:
    """Retrieve an article's metadata by its URL."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM articles WHERE url = ?", (url,))
        row = cursor.fetchone()
        return dict(row) if row else None

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
            """, (query, limit))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.OperationalError:
            # If the query contains special character syntax that FTS5 fails to parse, 
            # we escape it by surrounding words with quotes or falling back to simple search.
            safe_query = f'"{query.replace('"', ' ')}"'
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
                """, (safe_query, limit))
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
        cursor.execute("DELETE FROM articles_fts WHERE article_id = ?", (article_id,))
        conn.commit()
        return cursor.rowcount > 0
