# Read It Later (RIL)

**Read It Later (RIL)** is a local-first web article archiver for people who want to save, search, and reuse long-form content without turning their browser tabs or read-it-later queue into a graveyard.

RIL extracts clean article content from web pages, stores it locally, caches images, indexes everything with SQLite FTS5, and exposes your personal reading archive to AI assistants through MCP.

It is designed for workflows like:

* saving links quickly from Telegram;
* keeping a clean Markdown archive for Obsidian, Logseq, or plain files;
* searching and reading saved articles from the terminal;
* letting a local AI agent summarize, search, and reason over your archive.

---

## Why RIL?

Most read-it-later tools are cloud-first, product-heavy, and optimized around yet another inbox. RIL takes a different approach:

* **Local-first** — your articles, metadata, and cached assets live on your machine.
* **Clean reading archive** — pages are processed with readability extraction to remove navigation, banners, popups, cookie notices, and other clutter.
* **Image caching** — article images are downloaded locally so saved pages remain useful even if the original website changes or disappears.
* **AI-ready** — MCP support lets tools like Claude Desktop search, inspect, and summarize your saved articles.
* **Telegram-first capture** — send a link from your phone and archive it immediately.
* **Searchable by default** — SQLite FTS5 powers fast full-text search across your saved content.
* **Simple file-based output** — articles can be stored as Markdown and used with tools like Obsidian or Logseq.

---

## Features

* Save web pages as clean articles.
* Extract readable content from complex pages.
* Render JavaScript-heavy pages with Playwright.
* Cache images locally.
* Store article metadata in SQLite.
* Search article contents with SQLite FTS5.
* Use from Telegram, CLI, or MCP.
* Mark articles as read or unread.
* List saved articles.
* Retrieve article content and metadata.
* Integrate with AI agents through Model Context Protocol.

---

## How It Works

```text
[ Link from phone ] ──> [ Telegram Bot ] ──┐
                                           │
[ Link from CLI   ] ──> [ CLI          ] ──┼─> [ Playwright / Browser Rendering ]
                                           │              │
[ Link from agent ] ──> [ MCP Server   ] ──┘              v
                                                    [ Readability ]
                                                           │
                                                           v
                                                    [ Converter ]
                                                           │
                                      ┌────────────────────┴────────────────────┐
                                      v                                         v
                              [ SQLite FTS5 ]                         [ library/ files ]
                              metadata + search                       articles + images
```

RIL separates capture, processing, storage, and access:

1. A URL is submitted through Telegram, CLI, or MCP.
2. The page is rendered and extracted.
3. Readability logic removes non-article content.
4. The result is converted into a local article file.
5. Metadata and searchable text are stored in SQLite.
6. The archive can be queried from Telegram, CLI, or an AI assistant.

---

## Project Structure

```text
.
├── main.py                     # Python CLI entrypoint
├── pyproject.toml              # Python package metadata
├── src/ril/                    # Core Python implementation
│   ├── cli.py                  # CLI commands
│   ├── config.py               # Configuration and environment handling
│   ├── core.py                 # Main article processing flow
│   ├── crawler.py              # Playwright-based page fetching
│   ├── readability_utils.py    # Article extraction helpers
│   ├── converters.py           # Markdown/HTML/EPUB conversion and asset handling
│   ├── db.py                   # SQLite storage and FTS5 search
│   ├── mcp_server.py           # Python MCP server
│   └── bridge_json.py          # JSON bridge used by the Rust daemon
│
├── ril-daemon/                 # Rust daemon and Telegram/MCP runtime
│   ├── src/
│   │   ├── telegram/           # Telegram UI, callbacks, views, keyboards
│   │   ├── mcp/                # MCP protocol and tools
│   │   ├── python_bridge.rs    # Bridge to Python article processing
│   │   ├── config.rs           # Rust daemon configuration
│   │   └── lib.rs              # CLI commands and runtime modes
│   └── Cargo.toml
│
├── tests/                      # Python tests
├── ril-daemon/tests/           # Rust integration and regression tests
├── docs/                       # Documentation
└── library/                    # Default local article archive
```

---

## Requirements

* Python 3.12+
* `uv`
* Playwright with Chromium
* SQLite
* Telegram bot token, if you want Telegram capture
* Claude Desktop or another MCP client, if you want AI-agent integration
* Rust toolchain, if you want to run the Rust daemon

---

## Installation

Clone the repository:

```bash
git clone <repository-url>
cd ril
```

Create a Python environment:

```bash
uv venv --python 3.12
source .venv/bin/activate
```

Install the package:

```bash
uv pip install -e .
```

Install the Playwright browser:

```bash
.venv/bin/playwright install chromium
```

---

## Configuration

Create a `.env` file:

```bash
cp .env.example .env
```

Minimal configuration:

```ini
TELEGRAM_TOKEN=your_token_from_botfather
ALLOWED_TELEGRAM_USERS=your_telegram_user_id
```

Optional configuration:

```ini
RIL_LIBRARY_DIR=./library
RIL_DB_PATH=./library/metadata.db
RIL_DEFAULT_FORMAT=markdown
```

The Telegram user allowlist is strongly recommended. Without it, anyone who can access your bot may be able to interact with your archive.

---

## Usage

### Telegram Bot (Rust Daemon)

The Telegram bot is run via the Rust daemon (`ril-daemon`), which delegates processing to the Python backend.

From the `ril-daemon` directory:

```bash
cargo run -- telegram
```

Send a URL to the bot:

```text
https://example.com/article
```

The bot will process the page, extract the article, save it to your local library, and store metadata in SQLite.

Common commands:

```text
/stats              Show reading statistics
/list               Show recently saved articles
/search <query>     Search the archive
```

---

### CLI

Add an article:

```bash
.venv/bin/python main.py add "https://example.com/article"
```

Search saved articles:

```bash
.venv/bin/python main.py search "distributed systems"
```

Show reading statistics:

```bash
.venv/bin/python main.py stats
```

---

### MCP Server

RIL can expose your local archive to AI assistants through MCP.

Example Claude Desktop configuration:

```json
{
  "mcpServers": {
    "read-it-later": {
      "command": "/absolute/path/to/ril/.venv/bin/python",
      "args": [
        "/absolute/path/to/ril/main.py",
        "mcp"
      ],
      "env": {
        "RIL_LIBRARY_DIR": "/absolute/path/to/ril/library",
        "RIL_DB_PATH": "/absolute/path/to/ril/library/metadata.db"
      }
    }
  }
}
```

After restarting Claude Desktop, the assistant can use RIL tools to save URLs, search your archive, list articles, and read article content.

---

## Rust Daemon

The repository also includes a Rust daemon that can run the Telegram bot, MCP server, or both while delegating article processing to the Python bridge.

From the `ril-daemon` directory:

```bash
cd ril-daemon
cargo run -- telegram
```

Other runtime modes:

```bash
cargo run -- mcp
cargo run -- daemon
cargo run -- health
cargo run -- config-check
```

Mock mode is available for local testing without calling the real Python process:

```bash
cargo run -- --mock telegram
```

---

## MCP Tools

The MCP interface exposes tools for working with the local archive:

* `process_url` — scrape and save a URL.
* `search_articles` — search article contents using SQLite FTS5.
* `list_articles` — list saved articles.
* `mark_article_read` — mark an article as read.
* `mark_article_unread` — mark an article as unread.
* `get_reading_stats` — get archive and reading statistics.
* `get_article_content` — retrieve article metadata and file contents.
* `delete_article` — delete an article and its files.
* `reset_library` — clear the local archive.

---

## Development

Install the project in editable mode:

```bash
uv pip install -e .
```

Run Python tests:

```bash
pytest
```

Run Rust tests:

```bash
cd ril-daemon
cargo test
```

Run a Rust health check:

```bash
cd ril-daemon
cargo run -- health
```

---

## Data Storage

By default, RIL stores data in the local `library/` directory.

Typical contents include:

* article files;
* cached images;
* SQLite metadata database;
* full-text search index.

RIL is designed to keep the archive portable and inspectable. You should be able to back up, sync, or version your archive using normal file-system tools.

---

## Security Notes

RIL is intended for personal use.

Recommended practices:

* restrict Telegram access with `ALLOWED_TELEGRAM_USERS`;
* keep your Telegram token private;
* avoid exposing the MCP server to untrusted clients;
* back up your `library/` directory regularly;
* review saved content before sharing your archive with an AI assistant.

---

## Roadmap Ideas

Potential future improvements:

* cleaner two-message Telegram UI: persistent hub plus current state;
* on-demand export to Markdown, HTML, or EPUB;
* download-time format conversion based on user settings;
* better article deduplication;
* richer tagging, comments, and ratings;
* improved search filters;
* import/export tools;
* background daemon mode improvements;
* more MCP tools for AI-assisted reading workflows.

---

## Contributing

Contributions are welcome.

Good first areas to explore:

* improving extraction quality for difficult websites;
* adding tests for converters and Telegram flows;
* improving MCP tool coverage;
* refining Telegram UX;
* improving documentation;
* adding packaging or deployment examples.

Before opening a large pull request, consider opening an issue to discuss the proposed change.

---

## License

Add the project license here.

For example:

```text
MIT License
```

---


The project is inspired by the need for a simpler, more durable, AI-friendly alternative to tab hoarding and cloud-based read-it-later services.
