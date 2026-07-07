import sys
import json
import asyncio
from pathlib import Path
from ril import db, core
from ril.converters import MarkdownConverter, HTMLConverter, EPUBConverter

async def handle_process_url(args):
    url = args.get("url")
    fmt = args.get("format", "epub")
    force = bool(args.get("force", False))
    if fmt == "html":
        converter = HTMLConverter()
    elif fmt == "epub":
        converter = EPUBConverter()
    else:
        converter = MarkdownConverter()
    
    result = await core.process_url(url, converter=converter, force=force)
    return result

async def handle_search_articles(args):
    query = args.get("query")
    limit = args.get("limit", 10)
    results = db.search_articles(query, limit=limit)
    return results

async def handle_list_articles(args):
    status = args.get("status")
    limit = args.get("limit", 50)
    if status == "":
        status = None
    results = db.list_articles(status=status, limit=limit)
    return results

async def handle_mark_article_read(args):
    article_id = int(args.get("article_id"))
    success = db.mark_as_read(article_id, 'read')
    return {"success": success}

async def handle_mark_article_unread(args):
    article_id = int(args.get("article_id"))
    success = db.mark_as_read(article_id, 'unread')
    return {"success": success}

async def handle_get_reading_stats(args):
    stats = db.get_stats()
    return stats

async def handle_get_article_content(args):
    article_id = int(args.get("article_id"))
    article = db.get_article(article_id)
    if not article:
        raise ValueError(f"Article with ID {article_id} not found")
    
    file_path = Path(article["file_path"])
    content = ""
    if file_path.exists():
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    return {
        "article": dict(article),
        "content": content
    }

async def handle_delete_article(args):
    article_id = int(args.get("article_id"))
    success = core.delete_article(article_id)
    return {"success": success}

async def handle_reset_library(args):
    core.reset_library()
    return {"success": True}

async def handle_export_article(args):
    article_id = int(args.get("article_id"))
    export_format = args.get("format")
    return await core.export_article(article_id, export_format)

async def handle_search_articles_advanced(args):
    query = args.get("query")
    status = args.get("status")
    tag = args.get("tag")
    rating = args.get("rating")
    if rating is not None:
        rating = int(rating)
    domain = args.get("domain")
    no_tags = bool(args.get("no_tags", False))
    no_rating = bool(args.get("no_rating", False))
    date_added = args.get("date_added")
    limit = int(args.get("limit", 10))
    offset = int(args.get("offset", 0))
    
    return db.search_articles_advanced(
        query=query,
        status=status,
        tag=tag,
        rating=rating,
        domain=domain,
        no_tags=no_tags,
        no_rating=no_rating,
        date_added=date_added,
        limit=limit,
        offset=offset
    )

async def handle_add_tags(args):
    article_id = int(args.get("article_id"))
    tags = args.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    db.add_tags(article_id, tags)
    return {"success": True}

async def handle_remove_tag(args):
    article_id = int(args.get("article_id"))
    tag = args.get("tag")
    success = db.remove_tag(article_id, tag)
    return {"success": success}

async def handle_list_tags(args):
    return db.list_tags()

async def handle_rate_article(args):
    article_id = int(args.get("article_id"))
    rating = args.get("rating")
    if rating is not None:
        rating = int(rating)
    success = db.rate_article(article_id, rating)
    return {"success": success}

async def handle_set_article_comment(args):
    article_id = int(args.get("article_id"))
    comment = args.get("comment")
    success = db.set_article_comment(article_id, comment)
    return {"success": success}

async def handle_get_extended_stats(args):
    return db.get_extended_stats()

async def handle_get_sources_stats(args):
    limit = int(args.get("limit", 10))
    return db.get_sources_stats(limit=limit)

async def handle_get_tags_stats(args):
    return db.get_tags_stats()

async def handle_get_ratings_stats(args):
    return db.get_ratings_stats()

async def handle_get_dynamics_stats(args):
    return db.get_dynamics_stats()

async def main():
    try:
        # Read from stdin
        input_data = sys.stdin.read()
        if not input_data.strip():
            print(json.dumps({
                "ok": False,
                "data": None,
                "error": {
                    "code": "EMPTY_INPUT",
                    "message": "No input data provided",
                    "details": ""
                }
            }))
            return

        payload = json.loads(input_data)
        command = payload.get("command")
        args = payload.get("args", {})
        
        commands = {
            "process_url": handle_process_url,
            "search_articles": handle_search_articles,
            "list_articles": handle_list_articles,
            "mark_article_read": handle_mark_article_read,
            "mark_article_unread": handle_mark_article_unread,
            "get_reading_stats": handle_get_reading_stats,
            "get_article_content": handle_get_article_content,
            "delete_article": handle_delete_article,
            "reset_library": handle_reset_library,
            "search_articles_advanced": handle_search_articles_advanced,
            "add_tags": handle_add_tags,
            "remove_tag": handle_remove_tag,
            "list_tags": handle_list_tags,
            "rate_article": handle_rate_article,
            "set_article_comment": handle_set_article_comment,
            "get_extended_stats": handle_get_extended_stats,
            "get_sources_stats": handle_get_sources_stats,
            "get_tags_stats": handle_get_tags_stats,
            "get_ratings_stats": handle_get_ratings_stats,
            "get_dynamics_stats": handle_get_dynamics_stats,
            "export_article": handle_export_article
        }
        
        if command not in commands:
            print(json.dumps({
                "ok": False,
                "data": None,
                "error": {
                    "code": "UNKNOWN_COMMAND",
                    "message": f"Command '{command}' is not supported",
                    "details": ""
                }
            }))
            return
            
        handler = commands[command]
        if asyncio.iscoroutinefunction(handler):
            result = await handler(args)
        else:
            result = handler(args)
            
        print(json.dumps({
            "ok": True,
            "data": result,
            "error": None
        }))
        
    except json.JSONDecodeError as e:
        print(json.dumps({
            "ok": False,
            "data": None,
            "error": {
                "code": "INVALID_JSON",
                "message": "Failed to parse input as JSON",
                "details": str(e)
            }
        }))
    except Exception as e:
        import traceback
        print(json.dumps({
            "ok": False,
            "data": None,
            "error": {
                "code": "PROCESSING_FAILED",
                "message": str(e),
                "details": traceback.format_exc()
            }
        }))

if __name__ == "__main__":
    db.init_db()
    asyncio.run(main())
