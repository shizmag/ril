# Read It Later (RIL) Rust Daemon

`ril-daemon` is a production-ready Rust runner and integration layer for the existing Python-based **Read It Later** project. It exposes the core functionality through a Telegram bot interface and a Model Context Protocol (MCP) server, bridging to Python using a fast stdio subprocess.

## Features

- **Subprocess JSON Bridge:** Runs Python script `ril.bridge_json` over stdin/stdout. No PyO3 dependency issues; decoupled and reliable.
- **Telegram Bot:** Auto-imports sent URLs with a concurrency limit of 2, manages read/unread statuses, displays statistics, gets articles, and performs secure resets.
- **Model Context Protocol (MCP) Server:** Stdio-based JSON-RPC server implementing the full suite of RIL tools for integration into AI clients (like Claude Desktop or Cursor).
- **Daemon Command:** Simple runner command that operates the Telegram Bot, ideal for launching as a `systemd` unit.
- **Mock Mode:** Run the entire daemon (`-m` or `--mock` flag) in a sandbox mode without calling the real Python environment.

---

## Configuration

The daemon loads configuration from your environment and the `.env` file located in the root of the project.

| Variable | Description | Default |
|---|---|---|
| `RIL_LIBRARY_DIR` | Folder to save parsed articles and SQLite files | `./library` |
| `RIL_DB_PATH` | Full path to the metadata SQLite database | `[library]/metadata.db` |
| `TELEGRAM_TOKEN` | Token for your Telegram Bot | - |
| `ALLOWED_TELEGRAM_USERS` | Whitelist of user IDs allowed to interact with the bot (comma-separated list of integers, e.g., `12345,67890`) | All users (if empty) |
| `RIL_DEFAULT_FORMAT` | Default format to download articles (`markdown` / `html` / `epub`) | `markdown` |
| `RIL_PYTHON_CMD` | Custom Python command prefix (e.g. `uv run python`) | Auto-resolved |
| `RIL_PYTHON_BIN` | Absolute path to the python executable | Auto-resolved |
| `RIL_PYTHON_WORKDIR` | Working directory of the Python project | Current directory |
| `RIL_BRIDGE_TIMEOUT_SECONDS` | Timeout for a single Python bridge subprocess call | `30` |
| `RUST_LOG` | Tracing logging level filter (`info`, `debug`, etc.) | `info` |

---

## Getting Started

### 1. Build the Rust Daemon
To build the daemon in release mode:
```bash
cd ril-daemon
cargo build --release
```
The binary will be generated at `ril-daemon/target/release/ril-daemon`.

### 2. Verify Config & Health
Run the config-check to inspect the parsed environment variables:
```bash
./target/release/ril-daemon config-check
```

Check the connectivity and responsiveness of the Python bridge:
```bash
./target/release/ril-daemon health
```

---

## Usage Commands

The `ril-daemon` CLI provides the following subcommands:

### Run Full Daemon Mode
Runs the daemon as a long-running service, launching background tasks like the Telegram Bot:
```bash
./target/release/ril-daemon daemon
```

### Run Telegram Bot
Launch the Telegram bot in long-polling mode separately:
```bash
./target/release/ril-daemon telegram
```

### Run MCP Server
Run the MCP server over standard input/output:
```bash
./target/release/ril-daemon mcp
```

### Mock Sandbox Mode
You can run any of the commands above in mock mode using the `-m` / `--mock` flag, which executes memory-backed operations without calling the Python subprocess:
```bash
./target/release/ril-daemon --mock telegram
./target/release/ril-daemon --mock mcp
```

---

## MCP Server Integration

To connect the `ril-daemon` MCP server to **Claude Desktop**, add the server configuration inside your Claude Desktop configuration file (typically at `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "read-it-later": {
      "command": "/Users/vladimirkasterin/python/ril/ril-daemon/target/release/ril-daemon",
      "args": ["mcp"],
      "env": {
        "RIL_PYTHON_WORKDIR": "/Users/vladimirkasterin/python/ril",
        "RIL_DB_PATH": "/Users/vladimirkasterin/python/ril/library/metadata.db",
        "RIL_LIBRARY_DIR": "/Users/vladimirkasterin/python/ril/library"
      }
    }
  }
}
```

---

## Systemd Service Configuration

A template for systemd service is provided at `docs/ril-daemon.service.example`. To run the daemon as a systemd service:

1. Copy the unit file:
   ```bash
   sudo cp docs/ril-daemon.service.example /etc/systemd/system/ril-daemon.service
   ```
2. Enable and start the service:
   ```bash
   sudo systemctl daemon-reload
   ```
   ```bash
   sudo systemctl enable ril-daemon
   ```
   ```bash
   sudo systemctl start ril-daemon
   ```
3. Check logs:
   ```bash
   journalctl -u ril-daemon -f
   ```
