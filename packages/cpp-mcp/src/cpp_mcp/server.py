#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""FastMCP server exposing C++ standard lookup and search tools."""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware

from cpp_mcp.backend import StandardBackend
from cpp_mcp.sqlite_backend import SqliteStandardBackend

log = logging.getLogger(__name__)

DEFAULT_PORT = 8001
DEFAULT_DATA_DIR = Path.home() / ".cpp-mcp"
DATA_DIR_ENV = "CPP_MCP_DATA_DIR"
DEFAULT_DRAFT_ENV = "CPP_MCP_DEFAULT_DRAFT"
KEYS_FILE_ENV = "CPP_MCP_KEYS_FILE"


def _load_keys(keys_path: str | Path | None) -> set[str]:
    """Load bearer tokens from a keys file (one per line, # comments)."""
    if keys_path is None:
        return set()
    path = Path(keys_path)
    if not path.is_file():
        log.warning("Keys file %s does not exist; auth disabled", path)
        return set()
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            keys.add(stripped)
    log.info("Loaded %d API key(s) from %s", len(keys), path)
    return keys


def _format_section(row: object) -> dict:
    """Convert a SectionRow to a JSON-friendly dict."""
    return {
        "stable_label": row.stable_label,
        "title": row.title,
        "depth": row.depth,
        "section_number": row.section_number,
        "parent_label": row.parent_label,
        "chapter_file": row.chapter_file,
        "draft_tag": row.draft_tag,
        "raw_latex": row.raw_latex,
        "cleaned_text": row.cleaned_text,
        "paragraph_count": row.paragraph_count,
    }


def _format_section_brief(row: object) -> dict:
    """Compact section representation for list results."""
    return {
        "stable_label": row.stable_label,
        "title": row.title,
        "depth": row.depth,
        "chapter_file": row.chapter_file,
        "draft_tag": row.draft_tag,
    }


class _BearerKeyMiddleware(Middleware):
    """Reject requests without a valid bearer token."""

    def __init__(self, keys: set[str], keys_lock: threading.Lock) -> None:
        self._keys = keys
        self._keys_lock = keys_lock

    @property
    def keys(self) -> set[str]:
        with self._keys_lock:
            return set(self._keys)

    def update_keys(self, new_keys: set[str]) -> None:
        with self._keys_lock:
            self._keys = new_keys

    async def on_request(self, context, call_next):
        try:
            from fastmcp.server.dependencies import get_http_request
            request = get_http_request()
            auth_header = request.headers.get("authorization", "")
        except Exception:
            return await call_next(context)

        if not auth_header.lower().startswith("bearer "):
            raise Exception("Unauthorized: missing or invalid Authorization header")

        token = auth_header[7:]
        if token not in self.keys:
            raise Exception("Unauthorized: invalid API key")

        return await call_next(context)


def create_server(
    backend: StandardBackend,
    default_draft: str | None = None,
    keys_file: str | Path | None = None,
    no_auth: bool = False,
) -> FastMCP:
    """Build the FastMCP server with all tools registered.

    Authentication is required unless *no_auth* is ``True``. When
    *no_auth* is ``False``, a *keys_file* containing at least one key
    must be provided or a ``ValueError`` is raised.
    """

    _keys_lock = threading.Lock()
    _auth_middleware: _BearerKeyMiddleware | None = None

    if no_auth:
        log.warning("Authentication disabled (--no-auth). Do not use in production.")
    else:
        _keys = _load_keys(keys_file)
        if not _keys:
            raise ValueError(
                "No API keys loaded. Provide a --keys-file with at least one key, "
                "or pass --no-auth to explicitly disable authentication."
            )
        _auth_middleware = _BearerKeyMiddleware(_keys, _keys_lock)

    mcp = FastMCP(
        "C++ Standard",
        instructions=(
            "Search and browse the C++ standard (ISO/IEC 14882). "
            "Use lookup_section for exact section references like [basic.life]. "
            "Use search_standard for natural-language queries. "
            "Use list_drafts to see available standard versions."
        ),
    )

    if _auth_middleware is not None:
        mcp.add_middleware(_auth_middleware)

    def _reload_keys(signum: int, frame: object) -> None:
        if _auth_middleware is None:
            return
        new_keys = _load_keys(keys_file)
        _auth_middleware.update_keys(new_keys)
        log.info("Reloaded API keys on signal %d", signum)

    if keys_file and os.name != "nt":
        signal.signal(signal.SIGHUP, _reload_keys)

    def _resolve_draft(draft: str | None) -> str | None:
        if draft is not None:
            return draft
        if default_draft is not None:
            return default_draft
        return backend.default_draft_tag()

    @mcp.tool()
    def lookup_section(stable_label: str, draft: str | None = None) -> str:
        """Look up a C++ standard section by its stable label (e.g. 'basic.life').

        Returns the section's raw LaTeX, cleaned text, and metadata.
        """
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps({"error": "No drafts ingested. Run 'cpp-mcp ingest' first."})
        row = backend.lookup_section(stable_label, tag)
        if row is None:
            return json.dumps({"error": f"Section [{stable_label}] not found in draft '{tag}'."})
        return json.dumps(_format_section(row))

    @mcp.tool()
    def search_standard(
        query: str,
        top_k: int = 10,
        chapter: str | None = None,
        draft: str | None = None,
    ) -> str:
        """Full-text search across the C++ standard.

        Returns sections matching the query, ranked by relevance.
        Use chapter (e.g. 'basic.tex') to restrict scope.
        """
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps({"error": "No drafts ingested. Run 'cpp-mcp ingest' first."})
        rows = backend.search(query, top_k=top_k, chapter=chapter, draft_tag=tag)
        return json.dumps([_format_section(r) for r in rows])

    @mcp.tool()
    def get_section_with_children(
        stable_label: str, draft: str | None = None
    ) -> str:
        """Get a section and all its sub-sections.

        Useful for retrieving an entire topic area like [basic.life] with
        all its subsections.
        """
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps({"error": "No drafts ingested."})
        rows = backend.get_section_with_children(stable_label, tag)
        if not rows:
            return json.dumps({"error": f"Section [{stable_label}] not found in draft '{tag}'."})
        return json.dumps([_format_section(r) for r in rows])

    @mcp.tool()
    def list_chapters(draft: str | None = None) -> str:
        """List all top-level chapters of the C++ standard."""
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps({"error": "No drafts ingested."})
        rows = backend.list_chapters(tag)
        return json.dumps([_format_section_brief(r) for r in rows])

    @mcp.tool()
    def list_sections(
        chapter: str | None = None,
        depth: int | None = None,
        draft: str | None = None,
    ) -> str:
        """Browse sections of the standard, optionally filtered by chapter and depth.

        Chapter is the .tex filename (e.g. 'basic.tex', 'expressions.tex').
        Depth 0 = chapters, 1 = major sections, 2+ = subsections.
        """
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps({"error": "No drafts ingested."})
        rows = backend.list_sections(chapter=chapter, depth=depth, draft_tag=tag)
        return json.dumps([_format_section_brief(r) for r in rows])

    @mcp.tool()
    def list_drafts() -> str:
        """List all ingested versions of the C++ standard.

        Returns draft tags, ingestion dates, section counts, and git SHAs.
        Use a draft tag in other tools to query a specific version.
        """
        drafts = backend.list_drafts()
        return json.dumps([
            {
                "draft_tag": d.draft_tag,
                "ingested_at": d.ingested_at,
                "section_count": d.section_count,
                "git_sha": d.git_sha,
            }
            for d in drafts
        ])

    @mcp.tool()
    def diff_section(
        stable_label: str, from_draft: str, to_draft: str
    ) -> str:
        """Compare a section across two draft versions.

        Returns both versions side by side (raw LaTeX and cleaned text).
        """
        left, right = backend.diff_section(stable_label, from_draft, to_draft)
        result: dict = {"stable_label": stable_label, "from_draft": from_draft, "to_draft": to_draft}
        result["from_section"] = _format_section(left) if left else None
        result["to_section"] = _format_section(right) if right else None
        if left is None and right is None:
            result["error"] = f"Section [{stable_label}] not found in either draft."
        return json.dumps(result)

    return mcp


def resolve_data_dir(data_dir: str | None = None) -> Path:
    """Resolve the data directory from flag, env var, or default."""
    if data_dir is not None:
        return Path(data_dir)
    env = os.environ.get(DATA_DIR_ENV, "").strip()
    if env:
        return Path(env)
    return DEFAULT_DATA_DIR


def build_default_server(
    data_dir: str | None = None,
    default_draft: str | None = None,
    keys_file: str | None = None,
    no_auth: bool = False,
) -> tuple[FastMCP, SqliteStandardBackend]:
    """Construct a server with the default SQLite backend."""
    resolved_dir = resolve_data_dir(data_dir)
    resolved_dir.mkdir(parents=True, exist_ok=True)
    db_path = resolved_dir / "standard.db"

    backend = SqliteStandardBackend(db_path)
    backend.create_schema()

    if default_draft is None:
        default_draft = os.environ.get(DEFAULT_DRAFT_ENV, "").strip() or None

    if keys_file is None:
        keys_file = os.environ.get(KEYS_FILE_ENV, "").strip() or None

    mcp = create_server(
        backend,
        default_draft=default_draft,
        keys_file=keys_file,
        no_auth=no_auth,
    )
    return mcp, backend
