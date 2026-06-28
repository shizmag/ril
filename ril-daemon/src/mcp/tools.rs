use serde_json::{json, Value};

pub fn get_tools_list() -> Value {
    json!({
        "tools": [
            {
                "name": "process_url",
                "description": "Scrape and add a URL to the library",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": { "type": "string", "description": "URL to scrape and import" },
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "html", "epub"],
                            "description": "Format to save (default: markdown)"
                        }
                    },
                    "required": ["url"]
                }
            },
            {
                "name": "search_articles",
                "description": "Search article contents using SQLite FTS5",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": { "type": "string", "description": "FTS5 query terms" }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "list_articles",
                "description": "List saved articles from the library",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["read", "unread"],
                            "description": "Filter by read/unread status"
                        },
                        "limit": { "type": "integer", "description": "Max articles to return" }
                    }
                }
            },
            {
                "name": "mark_article_read",
                "description": "Mark an article as read",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "article_id": { "type": "integer", "description": "Article ID" }
                    },
                    "required": ["article_id"]
                }
            },
            {
                "name": "mark_article_unread",
                "description": "Mark an article as unread",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "article_id": { "type": "integer", "description": "Article ID" }
                    },
                    "required": ["article_id"]
                }
            },
            {
                "name": "get_reading_stats",
                "description": "Get library and reading statistics",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "get_article_content",
                "description": "Get the metadata and file contents of a saved article",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "article_id": { "type": "integer", "description": "Article ID" }
                    },
                    "required": ["article_id"]
                }
            },
            {
                "name": "delete_article",
                "description": "Delete an article and its files from the library",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "article_id": { "type": "integer", "description": "Article ID" }
                    },
                    "required": ["article_id"]
                }
            },
            {
                "name": "reset_library",
                "description": "Clear all saved articles, database metadata, and files",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            }
        ]
    })
}
