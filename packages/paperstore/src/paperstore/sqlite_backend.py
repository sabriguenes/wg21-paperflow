#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""SQLite-backed storage implementation.

All metadata lives in ``paperstore.db`` (two tables: ``papers``, ``years``).
Source files and markdown live under the ``paperstore/`` subdirectory;
the DB stores paths to them.

Designed for single-threaded access from the main coroutine in ``jobs.py``.
No WAL or connection pool is needed because workers never touch the DB.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paperstore.backend import PaperRow, StorageBackend, parse_authors_raw
from paperstore.errors import (
    MissingMailingIndexError,
    MissingMetaError,
    MissingPaperMdError,
    MissingReviewError,
    MissingSourceError,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    paper_id      TEXT PRIMARY KEY,
    year          TEXT DEFAULT '',
    title         TEXT DEFAULT '',
    authors       TEXT DEFAULT '',
    target_group  TEXT DEFAULT '',
    intent        TEXT DEFAULT '',
    url           TEXT DEFAULT '',
    document_date TEXT DEFAULT '',
    mailing_date  TEXT DEFAULT '',
    source_file   TEXT DEFAULT '',
    markdown_path TEXT DEFAULT '',
    review_path   TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS years (
    year   TEXT PRIMARY KEY,
    added  TEXT DEFAULT ''
);

"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial schema."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(papers)").fetchall()}
    if "review_path" not in cols:
        conn.execute("ALTER TABLE papers ADD COLUMN review_path TEXT DEFAULT ''")


def _atomic_replace(src: Path, dst: Path) -> None:
    """Rename ``src`` to ``dst``, retrying on PermissionError (Windows AV/EDR)."""
    for _ in range(10):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            time.sleep(0.1)
    os.replace(src, dst)


class SqliteBackend(StorageBackend):
    """Filesystem-backed paperstore using a SQLite database for metadata.

    Constructor creates ``workspace_dir``, the ``paperstore/`` artifact
    subdirectory, and ``paperstore.db`` on first use. All read/write methods
    are synchronous and not thread-safe; call only from the main event-loop
    coroutine.

    Atomicity model: files are the source of truth, the DB is an index.
    Each writer first lands the artifact via an atomic ``.partial`` rename,
    then commits the matching DB row inside a single transaction
    (``with self._conn:``). The window between those two steps is brief but
    non-zero: a crash there leaves a complete file with a stale or absent
    row. Recovery is simply to re-run the operation; the pipeline is
    idempotent and the next call rewrites both file and row cleanly.
    """

    def __init__(self, workspace_dir: Path) -> None:
        self._workspace = Path(workspace_dir)
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._papers_dir = self._workspace / "paperstore"
        self._papers_dir.mkdir(exist_ok=True)
        db_path = self._workspace / "paperstore.db"
        # check_same_thread=False so long-lived servers (preview) can hand
        # the backend off to Werkzeug worker threads. Python's sqlite3 is
        # built SERIALIZED, so concurrent reads are safe; writes still go
        # through `with self._conn:` blocks and a single process.
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        _migrate(self._conn)
        self._conn.commit()

    @classmethod
    def from_env(cls) -> "SqliteBackend":
        """Construct from ``$WG21_DATA_DIR``.

        Raises :class:`EnvironmentError` with an actionable message when
        the variable is unset or empty.
        """
        env_var = "WG21_DATA_DIR"
        raw = os.environ.get(env_var, "").strip()
        if not raw:
            raise EnvironmentError(
                f"{env_var} is not set. "
                "Set it to the directory where paperflow stores its data.\n"
                f"  export {env_var}=/path/to/wg21-data"
            )
        return cls(Path(raw))

    @property
    def workspace_dir(self) -> Path:
        return self._workspace

    def close(self) -> None:
        """Close the underlying sqlite3 connection. Idempotent."""
        conn = getattr(self, "_conn", None)
        if conn is not None:
            conn.close()
            self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> "SqliteBackend":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ---- internal helpers -------------------------------------------------

    def _atomic_write_bytes(self, path: Path, content: bytes) -> Path:
        """Write ``content`` to ``path`` via a sibling ``.partial`` file.

        Uses ``<name>.partial`` (not ``<stem>.tmp.<suffix>``) so a stale
        temp file isn't mistaken for a real artifact by workspace-scanning
        callers. Cleans up the temp file on failure.
        """
        temp_path = path.with_name(path.name + ".partial")
        try:
            temp_path.write_bytes(content)
            _atomic_replace(temp_path, path)
        except Exception:  # Cleanup firewall: remove partial file on any failure
            try:
                temp_path.unlink()
            except OSError:
                pass
            raise
        return path

    def _atomic_write_text(self, path: Path, content: str) -> Path:
        """UTF-8 text counterpart to :meth:`_atomic_write_bytes`."""
        temp_path = path.with_name(path.name + ".partial")
        try:
            temp_path.write_text(content, encoding="utf-8")
            _atomic_replace(temp_path, path)
        except Exception:  # Cleanup firewall: remove partial file on any failure
            try:
                temp_path.unlink()
            except OSError:
                pass
            raise
        return path

    def _row_to_dict(self, row: sqlite3.Row) -> PaperRow:
        d = dict(row)
        d["authors"] = parse_authors_raw(d.get("authors", ""))
        return d

    # ---- year-based mailing index -----------------------------------------

    def has_year(self, year: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM papers WHERE year = ? LIMIT 1", (year,)
        ).fetchone()
        return row is not None

    def upsert_year(self, year: str, papers: list[dict]) -> list[PaperRow]:
        """Insert or update all papers for year. Returns merged list."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO years (year, added) VALUES (?, ?)",
                (year, now),
            )
            for p in papers:
                pid = (p.get("paper_id") or "").strip().upper()
                if not pid:
                    continue
                authors_raw = p.get("authors") or []
                if isinstance(authors_raw, list):
                    authors_json = json.dumps(authors_raw)
                else:
                    authors_json = str(authors_raw)
                # INSERT OR IGNORE keeps existing source_file/markdown_path on updates.
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO papers
                        (paper_id, year, title, authors, target_group, intent,
                         url, document_date, mailing_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pid,
                        year,
                        p.get("title") or "",
                        authors_json,
                        p.get("subgroup") or "",
                        p.get("intent") or "",
                        p.get("url") or "",
                        p.get("document_date") or "",
                        p.get("mailing_date") or "",
                    ),
                )
                # Update non-completion fields without clobbering source_file/markdown_path.
                self._conn.execute(
                    """
                    UPDATE papers SET
                        year = ?, title = ?, authors = ?, target_group = ?,
                        intent = CASE WHEN intent = '' THEN ? ELSE intent END,
                        url = ?, document_date = ?, mailing_date = ?
                    WHERE paper_id = ?
                    """,
                    (
                        year,
                        p.get("title") or "",
                        authors_json,
                        p.get("subgroup") or "",
                        p.get("intent") or "",
                        p.get("url") or "",
                        p.get("document_date") or "",
                        p.get("mailing_date") or "",
                        pid,
                    ),
                )
        return self.list_papers_for_year(year)

    def list_papers_for_year(self, year: str) -> list[PaperRow]:
        rows = self._conn.execute(
            "SELECT * FROM papers WHERE year = ?", (year,)
        ).fetchall()
        if not rows:
            raise MissingMailingIndexError(
                f"No papers found for year {year!r}. "
                f"Run 'paperflow mailing {year}' first."
            )
        return [self._row_to_dict(r) for r in rows]

    def list_all_paper_ids(self) -> list[str]:
        rows = self._conn.execute("SELECT paper_id FROM papers").fetchall()
        return [r["paper_id"] for r in rows]

    def resolve_year_for_paper(self, paper_id: str) -> tuple[str, PaperRow] | None:
        row = self._conn.execute(
            "SELECT * FROM papers WHERE paper_id = ?", (paper_id.strip().upper(),)
        ).fetchone()
        if row is None:
            return None
        d = self._row_to_dict(row)
        return d["year"], d

    # ---- writes -----------------------------------------------------------

    def put_source(self, paper_id: str, content: bytes, *, suffix: str) -> Path:
        """Write source bytes atomically and record the path in the DB."""
        if not suffix.startswith("."):
            raise ValueError(
                f"put_source: suffix must start with '.' (got {suffix!r})"
            )
        pid = paper_id.strip().upper()
        final_path = self._atomic_write_bytes(
            self._papers_dir / f"{pid.lower()}{suffix}", content
        )
        self.record_source(pid, final_path)
        return final_path

    def write_paper_md(self, paper_id: str, markdown: str) -> Path:
        """Write markdown atomically and record the path in the DB."""
        pid = paper_id.strip().upper()
        final_path = self._atomic_write_text(
            self._papers_dir / f"{pid.lower()}.md", markdown
        )
        self.record_markdown(pid, final_path)
        return final_path

    def write_review_md(self, paper_id: str, markdown: str) -> Path:
        """Write review markdown atomically and record the path in the DB."""
        pid = paper_id.strip().upper()
        final_path = self._atomic_write_text(
            self._papers_dir / f"{pid.lower()}.review.md", markdown
        )
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO papers (paper_id) VALUES (?)", (pid,)
            )
            self._conn.execute(
                "UPDATE papers SET review_path = ? WHERE paper_id = ?",
                (str(final_path), pid),
            )
        return final_path

    def clear_review(self, paper_id: str) -> None:
        """Delete the review file and clear ``review_path`` in the DB."""
        pid = paper_id.strip().upper()
        row = self._conn.execute(
            "SELECT review_path FROM papers WHERE paper_id = ?", (pid,)
        ).fetchone()
        if row and row["review_path"]:
            path = Path(row["review_path"])
            if path.exists():
                path.unlink()
        with self._conn:
            self._conn.execute(
                "UPDATE papers SET review_path = '' WHERE paper_id = ?",
                (pid,),
            )

    def write_intermediate(self, paper_id: str, name: str, payload: Any) -> Path:
        """Write an intermediate artifact JSON to disk atomically."""
        pid = paper_id.strip().upper()
        return self._atomic_write_text(
            self._papers_dir / f"{pid.lower()}.{name}.json",
            json.dumps(payload, indent=2, ensure_ascii=False),
        )

    def record_source(self, paper_id: str, path: Path | str) -> None:
        """Stamp ``path`` as ``source_file`` for ``paper_id``."""
        pid = paper_id.strip().upper()
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO papers (paper_id) VALUES (?)", (pid,)
            )
            self._conn.execute(
                "UPDATE papers SET source_file = ? WHERE paper_id = ?",
                (str(path), pid),
            )

    def record_markdown(
        self, paper_id: str, path: Path | str, *, intent: str | None = None
    ) -> None:
        """Stamp ``path`` as ``markdown_path`` (and optionally ``intent``)."""
        pid = paper_id.strip().upper()
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO papers (paper_id) VALUES (?)", (pid,)
            )
            if intent:
                self._conn.execute(
                    "UPDATE papers SET markdown_path = ?, intent = ? "
                    "WHERE paper_id = ?",
                    (str(path), intent, pid),
                )
            else:
                self._conn.execute(
                    "UPDATE papers SET markdown_path = ? WHERE paper_id = ?",
                    (str(path), pid),
                )

    _SOURCE_SUFFIXES = (".pdf", ".html", ".htm")

    def reconcile(self) -> dict[str, int]:
        """Backfill DB rows from on-disk artifacts. See ABC for semantics."""
        sources: list[tuple[str, Path]] = []
        markdowns: list[tuple[str, Path]] = []
        reviews: list[tuple[str, Path]] = []

        for path in sorted(self._papers_dir.iterdir()):
            if not path.is_file():
                continue
            name = path.name
            if name.endswith(".partial"):
                continue
            if name.endswith(".json"):
                continue
            if name.endswith(".review.md"):
                reviews.append((name[: -len(".review.md")].upper(), path))
                continue
            if name.endswith(".md"):
                markdowns.append((name[: -len(".md")].upper(), path))
                continue
            for suffix in self._SOURCE_SUFFIXES:
                if name.endswith(suffix):
                    sources.append((name[: -len(suffix)].upper(), path))
                    break

        counts = {"sources": 0, "markdowns": 0, "reviews": 0}
        with self._conn:
            for pid, path in sources:
                self._conn.execute(
                    "INSERT OR IGNORE INTO papers (paper_id) VALUES (?)", (pid,)
                )
                cursor = self._conn.execute(
                    "UPDATE papers SET source_file = ? "
                    "WHERE paper_id = ? AND source_file = ''",
                    (str(path), pid),
                )
                if cursor.rowcount > 0:
                    counts["sources"] += 1
            for pid, path in markdowns:
                self._conn.execute(
                    "INSERT OR IGNORE INTO papers (paper_id) VALUES (?)", (pid,)
                )
                cursor = self._conn.execute(
                    "UPDATE papers SET markdown_path = ? "
                    "WHERE paper_id = ? AND markdown_path = ''",
                    (str(path), pid),
                )
                if cursor.rowcount > 0:
                    counts["markdowns"] += 1
            for pid, path in reviews:
                self._conn.execute(
                    "INSERT OR IGNORE INTO papers (paper_id) VALUES (?)", (pid,)
                )
                cursor = self._conn.execute(
                    "UPDATE papers SET review_path = ? "
                    "WHERE paper_id = ? AND review_path = ''",
                    (str(path), pid),
                )
                if cursor.rowcount > 0:
                    counts["reviews"] += 1
        return counts

    # ---- reads ------------------------------------------------------------

    def get_meta(self, paper_id: str) -> PaperRow:
        row = self._conn.execute(
            "SELECT * FROM papers WHERE paper_id = ?",
            (paper_id.strip().upper(),),
        ).fetchone()
        if row is None:
            raise MissingMetaError(
                f"No metadata for {paper_id!r}. "
                f"Run 'paperflow mailing' then 'paperflow download' first."
            )
        return self._row_to_dict(row)

    def get_source_path(self, paper_id: str) -> Path:
        row = self._conn.execute(
            "SELECT source_file FROM papers WHERE paper_id = ?",
            (paper_id.strip().upper(),),
        ).fetchone()
        if row is None or not row["source_file"]:
            raise MissingSourceError(
                f"No staged source for {paper_id!r}. "
                f"Run 'paperflow download {paper_id}' first."
            )
        path = Path(row["source_file"])
        if not path.exists():
            raise MissingSourceError(
                f"Source file missing for {paper_id!r}: {path}. "
                f"Run 'paperflow download {paper_id}' again."
            )
        return path

    def get_paper_md(self, paper_id: str) -> str:
        row = self._conn.execute(
            "SELECT markdown_path FROM papers WHERE paper_id = ?",
            (paper_id.strip().upper(),),
        ).fetchone()
        if row is None or not row["markdown_path"]:
            raise MissingPaperMdError(
                f"No converted markdown for {paper_id!r}. "
                f"Run 'paperflow convert {paper_id}' first."
            )
        path = Path(row["markdown_path"])
        if not path.exists():
            raise MissingPaperMdError(
                f"Markdown file missing for {paper_id!r}: {path}. "
                f"Run 'paperflow convert {paper_id}' again."
            )
        return path.read_text(encoding="utf-8")

    def get_paper_md_path(self, paper_id: str) -> Path:
        pid = paper_id.strip().upper()
        return self._papers_dir / f"{pid.lower()}.md"

    def get_review_path(self, paper_id: str) -> Path:
        row = self._conn.execute(
            "SELECT review_path FROM papers WHERE paper_id = ?",
            (paper_id.strip().upper(),),
        ).fetchone()
        if row is None or not row["review_path"]:
            raise MissingReviewError(
                f"No review for {paper_id!r}. "
                f"Run 'paperflow review {paper_id}' first."
            )
        path = Path(row["review_path"])
        if not path.exists():
            raise MissingReviewError(
                f"Review file missing for {paper_id!r}: {path}. "
                f"Run 'paperflow review {paper_id}' again."
            )
        return path

    def list_years(self) -> list[tuple[str, int]]:
        """Return ``[(year, paper_count)]`` sorted by year."""
        rows = self._conn.execute(
            "SELECT year, COUNT(*) AS n FROM papers GROUP BY year ORDER BY year"
        ).fetchall()
        return [(r["year"], r["n"]) for r in rows]
