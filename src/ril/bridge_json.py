import sys
import json
import asyncio
from pathlib import Path
from ril import db, core
from ril.converters import MarkdownConverter, HTMLConverter, EPUBConverter

async def handle_process_url(args):
    url = args.get("url")
    fmt = args.get("format", "markdown")
    if fmt == "html":
        converter = HTMLConverter()
    elif fmt == "epub":
        converter = EPUBConverter()
    else:
        converter = MarkdownConverter()
    
    result = await core.process_url(url, converter=converter)
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
            "reset_library": handle_reset_library
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
