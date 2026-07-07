import os
import logging
from typing import Optional
from mcp.server.fastmcp import FastMCP

from ril import db, core
from ril.config import LIBRARY_DIR

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ril-mcp")

# Initialize FastMCP Server
mcp = FastMCP("Read It Later (RIL)")

@mcp.tool()
async def process_url(url: str, format: str = "epub", force: bool = False) -> str:
    """
    Download, clean, download images, convert, and save a webpage.
    
    Args:
        url: The web link/URL of the article to capture.
        format: The format to save the article, either 'epub' (default), 'markdown', or 'html'.
        force: Force update if URL already exists.
    """
    try:
        if format not in ("markdown", "html", "epub"):
            return "Error: format must be either 'markdown', 'html', or 'epub'"
            
        from ril.converters import MarkdownConverter, HTMLConverter, EPUBConverter
        if format == "html":
            converter = HTMLConverter()
        elif format == "epub":
            converter = EPUBConverter()
        else:
            converter = MarkdownConverter()
        result = await core.process_url(url, converter=converter, force=force)
        return (
            f"Saved successfully!\n"
            f"Title: {result['title']}\n"
            f"Word count: {result['word_count']} words ({result['char_count']} chars)\n"
            f"File: {result['file_path']}"
        )
    except Exception as e:
        logger.error(f"Error in process_url tool: {e}", exc_info=True)
        return f"Failed to save URL: {str(e)}"

@mcp.tool()
def search_articles(query: str) -> str:
    """
    Search saved articles for keywords or phrases (uses SQLite FTS5).
    Returns matching articles along with text snippets containing your query.
    
    Args:
        query: Search keywords or phrases (e.g. 'quantum processors' or 'python async').
    """
    try:
        results = db.search_articles(query)
        if not results:
            return f"No articles found matching query: '{query}'"
            
        output = [f"Found {len(results)} matching article(s):\n"]
        for r in results:
            status_emoji = "✅ [Read]" if r['status'] == 'read' else "📥 [Unread]"
            output.append(
                f"ID: {r['id']} | {status_emoji} {r['title']}\n"
                f"URL: {r['url']}\n"
                f"Word Count: {r['word_count']} | Saved: {r['added_at'][:10]}\n"
                f"Excerpt: {r['snippet']}\n"
                f"---"
            )
        return "\n".join(output)
    except Exception as e:
        logger.error(f"Error searching articles: {e}")
        return f"Error searching database: {str(e)}"

@mcp.tool()
def list_articles(status: Optional[str] = None, limit: int = 20) -> str:
    """
    List recently saved articles, optionally filtered by status ('read' or 'unread').
    
    Args:
        status: Filter by status, either 'read' or 'unread'. If omitted, lists all.
        limit: Max number of articles to return (default 20).
    """
    if status and status not in ('read', 'unread'):
        return "Error: status filter must be either 'read' or 'unread'"
        
    try:
        articles = db.list_articles(status, limit)
        if not articles:
            filter_msg = f" with status '{status}'" if status else ""
            return f"No articles found{filter_msg}."
            
        output = [f"Listing {len(articles)} recent articles:\n"]
        for a in articles:
            status_emoji = "✅ [Read]" if a['status'] == 'read' else "📥 [Unread]"
            output.append(
                f"[{a['id']}] {status_emoji} {a['title']}\n"
                f"    Saved: {a['added_at'][:16].replace('T', ' ')}\n"
                f"    Words: {a['word_count']} | File: {a['file_path']}"
            )
        return "\n".join(output)
    except Exception as e:
        logger.error(f"Error listing articles: {e}")
        return f"Error: {str(e)}"

@mcp.tool()
def mark_article_read(article_id: int) -> str:
    """
    Mark an article as read.
    
    Args:
        article_id: The numeric ID of the article to mark as read.
    """
    try:
        success = db.mark_as_read(article_id, 'read')
        if success:
            article = db.get_article(article_id)
            title = article['title'] if article else f"ID {article_id}"
            return f"Successfully marked '{title}' as read."
        else:
            return f"Article with ID {article_id} not found."
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def mark_article_unread(article_id: int) -> str:
    """
    Mark a read article back as unread.
    
    Args:
        article_id: The numeric ID of the article to mark as unread.
    """
    try:
        success = db.mark_as_read(article_id, 'unread')
        if success:
            article = db.get_article(article_id)
            title = article['title'] if article else f"ID {article_id}"
            return f"Successfully marked '{title}' as unread."
        else:
            return f"Article with ID {article_id} not found."
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def get_reading_stats() -> str:
    """
    Retrieve statistics about your library, word counts, and reading progress.
    """
    try:
        stats = db.get_stats()
        if stats['total_articles'] == 0:
            return "Your library is empty. Save some articles first!"
            
        progress_pct = (
            round((stats['read_articles'] / stats['total_articles']) * 100, 1)
            if stats['total_articles'] > 0 else 0
        )
        
        # Approximate reading time: ~200 words per minute
        unread_reading_time_mins = round(stats['unread_words'] / 200)
        
        return (
            f"📚 Read It Later Stats:\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Total Articles: {stats['total_articles']}\n"
            f"  📥 Unread: {stats['unread_articles']}\n"
            f"  ✅ Read: {stats['read_articles']} ({progress_pct}% completed)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Total Words Captured: {stats['total_words']:,}\n"
            f"  Words Read: {stats['read_words']:,}\n"
            f"  Words Unread: {stats['unread_words']:,}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Estimated Reading Backlog: ~{unread_reading_time_mins} minutes\n"
            f"Avg. Words/Article: {stats['avg_words_per_article']:.0f}"
        )
    except Exception as e:
        return f"Error generating stats: {str(e)}"

@mcp.tool()
def get_article_content(article_id: int) -> str:
    """
    Retrieve the saved Markdown file content of an article by its ID.
    
    Args:
        article_id: The numeric ID of the article to read.
    """
    try:
        article = db.get_article(article_id)
        if not article:
            return f"Article with ID {article_id} not found."
            
        file_path = article['file_path']
        if not os.path.exists(file_path):
            return f"Error: The file for this article could not be found at: {file_path}"
            
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        import re
        # Clean content from base64/images for reading
        if file_path.endswith(".md"):
            # Strip reference definitions at the bottom
            content = re.sub(r'(?m)^\[img_ref_\d+\].*$', '', content)
            # Strip inline image links
            content = re.sub(r'!\[.*?\]\[img_ref_\d+\]', '', content)
            content = re.sub(r'!\[.*?\]\[.*?\]', '', content)
            content = re.sub(r'!\[.*?\]\(.*?\)', '', content)
            content = re.sub(r'\n{3,}', '\n\n', content).strip()
        elif file_path.endswith(".html"):
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content, "lxml")
            for img in soup.find_all("img"):
                img.decompose()
            content = str(soup)
            
        return (
            f"--- TITLE: {article['title']} ---\n"
            f"--- URL: {article['url']} ---\n"
            f"--- SAVED AT: {article['added_at']} ---\n\n"
            f"{content}"
        )
    except Exception as e:
        return f"Error reading article content: {str(e)}"

@mcp.tool()
def delete_article(article_id: int) -> str:
    """
    Delete an article by its ID from the library and database, clearing its markdown and images.
    
    Args:
        article_id: The numeric ID of the article to delete.
    """
    try:
        success = core.delete_article(article_id)
        if success:
            return f"Successfully deleted article with ID {article_id}."
        else:
            return f"Article with ID {article_id} not found."
    except Exception as e:
        return f"Error deleting article: {str(e)}"

@mcp.tool()
def reset_library() -> str:
    """
    Completely clear the library, deleting all saved articles, markdown files, images, and resetting the database.
    """
    try:
        core.reset_library()
        return "Library reset successfully. All articles and files deleted."
    except Exception as e:
        return f"Error resetting library: {str(e)}"

if __name__ == "__main__":
    # FastMCP run takes transport="stdio" when run as an MCP server
    mcp.run(transport="stdio")
