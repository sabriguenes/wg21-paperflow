# Local Development Setup

This guide walks you through running the C++ Standard MCP server on your machine. It assumes no prior knowledge of paperflow or MCP servers.

## What this does

cpp-mcp parses the LaTeX source of the C++ standard (from [cplusplus/draft](https://github.com/cplusplus/draft)), stores it in a local SQLite database, and serves it over HTTP. AI assistants (Cursor, Claude, etc.) can then search and browse the standard using MCP tools.

## Prerequisites

- **Python 3.12+** -- check with `python --version`
- **git** -- check with `git --version`
- **uv** (recommended) or pip -- install uv with:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## Install

From the paperflow monorepo root:

```bash
uv pip install -e packages/cpp-mcp
```

Verify:

```bash
cpp-mcp --help
```

## Database location

The SQLite database lives at `~/.cpp-mcp/standard.db` by default. This single file is the only state -- everything in it is derived from the C++ standard's LaTeX source and can be regenerated at any time.

To use a different location:

```bash
# Via command-line flag (takes precedence)
cpp-mcp --data-dir /path/to/dir ingest --tag n5008

# Via environment variable
export CPP_MCP_DATA_DIR=/path/to/dir
cpp-mcp ingest --tag n5008
```

The `--data-dir` flag takes precedence over the environment variable.

## Ingest the standard

Download and parse the C++ standard at a specific draft tag:

```bash
cpp-mcp ingest --tag n5008
```

This will:
1. Clone the [cplusplus/draft](https://github.com/cplusplus/draft) repository at tag `n5008` into a temporary directory
2. Parse all `.tex` files, extracting sections, hierarchy, and paragraph structure
3. Expand LaTeX macros into searchable plain text
4. Store everything in `~/.cpp-mcp/standard.db`
5. Clean up the temporary clone

Expected time: 30-60 seconds (mostly the git clone). Database size: ~20-30 MB.

### Ingesting multiple versions

You can ingest multiple versions of the standard side by side:

```bash
cpp-mcp ingest --tag n4950    # C++23 final draft
cpp-mcp ingest --tag n5008    # C++26 working draft
```

Versions coexist in the same database and do not interfere with each other. Use the `list_drafts` tool (see below) to see what's available.

## Run the server

```bash
cpp-mcp serve
```

This starts an HTTP server at `http://localhost:8001`. You should see output like:

```
2026-05-28 18:00:00 cpp_mcp INFO Starting MCP server on http://127.0.0.1:8001
```

Use `--port` if 8001 is already taken:

```bash
cpp-mcp serve --port 9090
```

Leave this terminal running while you use the tools.

## Connect from Cursor

Add the following to your `.cursor/mcp.json` file:

```json
{
  "mcpServers": {
    "cpp-standard": {
      "url": "http://localhost:8001/mcp"
    }
  }
}
```

This file lives at:
- **Windows**: `%USERPROFILE%\.cursor\mcp.json` (e.g. `C:\Users\YourName\.cursor\mcp.json`)
- **macOS**: `~/.cursor/mcp.json`
- **Linux**: `~/.cursor/mcp.json`

After saving the file, restart Cursor or reload the MCP configuration.

## Try it

With the server running and Cursor connected, try these queries in chat:

- **Structural lookup**: "What does [basic.life] say?"
- **Full-text search**: "Search the standard for 'lifetime extension'"
- **Browse chapters**: "List the chapters of the standard"
- **Version management**: "What versions of the standard are available?"
- **Cross-version comparison**: "Compare [basic.life] between n4950 and n5008"

## Default draft

When a query does not specify a version, the server uses the most recently ingested draft. To override:

```bash
# Via command-line flag
cpp-mcp serve --default-draft n5008

# Via environment variable
export CPP_MCP_DEFAULT_DRAFT=n5008
cpp-mcp serve
```

## Re-indexing

When a new draft of the standard is published:

```bash
cpp-mcp ingest --tag n5025    # or whatever the new tag is
```

Old versions remain in the database. The new version becomes the default (most recently ingested). Restart the server to pick up the new data:

```bash
# Stop the running server (Ctrl+C), then:
cpp-mcp serve
```

## Troubleshooting

**"command not found: cpp-mcp"**
Make sure you installed the package (`uv pip install -e packages/cpp-mcp`) and that your Python scripts directory is on your PATH.

**"No drafts ingested"**
Run `cpp-mcp ingest --tag n5008` before starting the server.

**Port already in use**
Use `--port` to pick a different port: `cpp-mcp serve --port 9090`. Update your `.cursor/mcp.json` URL to match.

**Git clone fails**
Check your internet connection. The clone downloads from GitHub. If you're behind a proxy, configure git: `git config --global http.proxy http://proxy:port`.

**Python version too old**
cpp-mcp requires Python 3.12+. Check with `python --version` and upgrade if needed.
