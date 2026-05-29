#!/usr/bin/env python
import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path to ensure proper module resolution
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ril import db, core

def handle_add(args):
    """Import a URL from the command line."""
    print(f"Importing: {args.url}")
    try:
        result = asyncio.run(core.process_url(args.url))
        print("Success!")
        print(f"Title:     {result['title']}")
        print(f"Words:     {result['word_count']}")
        print(f"Markdown:  {result['file_path']}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

def handle_search(args):
    """Search articles from the command line."""
    results = db.search_articles(args.query, limit=args.limit)
    if not results:
        print(f"No matches found for: '{args.query}'")
        return
        
    print(f"Found {len(results)} matches:\n")
    for r in results:
        status = "Read" if r['status'] == 'read' else "Unread"
        print(f"[{r['id']}] [{status}] {r['title']}")
        print(f"    URL:     {r['url']}")
        print(f"    Excerpt: {r['snippet']}")
        print("-" * 50)

def handle_stats(args):
    """Display reading stats from the command line."""
    try:
        stats = db.get_stats()
        if stats['total_articles'] == 0:
            print("Library is empty.")
            return
            
        progress = (stats['read_articles'] / stats['total_articles']) * 100
        print("Read It Later Library Stats:")
        print("=" * 40)
        print(f"Total articles:  {stats['total_articles']}")
        print(f"  Unread:        {stats['unread_articles']}")
        print(f"  Read:          {stats['read_articles']} ({progress:.1f}% completed)")
        print("-" * 40)
        print(f"Total words:     {stats['total_words']:,}")
        print(f"  Words read:    {stats['read_words']:,}")
        print(f"  Words unread:  {stats['unread_words']:,}")
        print(f"Avg words/art:   {stats['avg_words_per_article']:.0f}")
        print("=" * 40)
    except Exception as e:
        print(f"Error getting stats: {e}", file=sys.stderr)

def handle_list(args):
    """List articles from the command line."""
    articles = db.list_articles(status=args.status, limit=args.limit)
    if not articles:
        print("No articles found.")
        return
        
    for a in articles:
        status = "Read" if a['status'] == 'read' else "Unread"
        print(f"[{a['id']}] [{status}] {a['title']}")
        print(f"    Words: {a['word_count']} | Saved: {a['added_at'][:16].replace('T', ' ')}")
        print(f"    Path:  {a['file_path']}")
        print()

def handle_read(args):
    """Mark an article as read."""
    success = db.mark_as_read(args.id, 'read')
    if success:
        print(f"Article {args.id} marked as read.")
    else:
        print(f"Article {args.id} not found.")

def handle_unread(args):
    """Mark an article as unread."""
    success = db.mark_as_read(args.id, 'unread')
    if success:
        print(f"Article {args.id} marked as unread.")
    else:
        print(f"Article {args.id} not found.")

def handle_bot(args):
    """Run the Telegram Bot."""
    from ril.telegram_bot import run_bot
    run_bot()

def handle_mcp(args):
    """Run the MCP Server."""
    from ril.mcp_server import mcp
    # Expose FastMCP server over standard input/output (stdio)
    mcp.run(transport="stdio")

def main():
    parser = argparse.ArgumentParser(description="Read It Later (RIL) Command Line Tool")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # mcp command
    subparsers.add_parser("mcp", help="Run the MCP Server (stdio)")
    
    # bot command
    subparsers.add_parser("bot", help="Run the Telegram Bot")
    
    # add command
    parser_add = subparsers.add_parser("add", help="Add a webpage to the library")
    parser_add.add_argument("url", help="URL of the page to scrape")
    
    # search command
    parser_search = subparsers.add_parser("search", help="Search article content with FTS5")
    parser_search.add_argument("query", help="Keywords to search for")
    parser_search.add_argument("--limit", type=int, default=10, help="Maximum results to return")
    
    # stats command
    subparsers.add_parser("stats", help="Show reading statistics")
    
    # list command
    parser_list = subparsers.add_parser("list", help="List saved articles")
    parser_list.add_argument("--status", choices=["read", "unread"], help="Filter by read/unread status")
    parser_list.add_argument("--limit", type=int, default=20, help="Maximum number of articles to list")
    
    # read command
    parser_read = subparsers.add_parser("read", help="Mark article as read")
    parser_read.add_argument("id", type=int, help="Article ID")
    
    # unread command
    parser_unread = subparsers.add_parser("unread", help="Mark article as unread")
    parser_unread.add_argument("id", type=int, help="Article ID")
    
    args = parser.parse_args()
    
    # Ensure database is initialized before any operation
    db.init_db()
    
    # Command router
    commands = {
        "mcp": handle_mcp,
        "bot": handle_bot,
        "add": handle_add,
        "search": handle_search,
        "stats": handle_stats,
        "list": handle_list,
        "read": handle_read,
        "unread": handle_unread
    }
    
    commands[args.command](args)

if __name__ == "__main__":
    main()
