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
from paperstore.extract_rows import (
    CaputCausaeRow,
    CitationAuditRow,
    ClaimRow,
    EvidenceRow,
    ExternalCitationRow,
    MarkerRow,
    PaperCitationRow,
    QuestionRow,
)
from paperstore.errors import (
    MissingAdvocatusError,
    MissingAgoraError,
    MissingMailingIndexError,
    MissingMetaError,
    MissingPaperMdError,
    MissingDissectError,
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
    source_file    TEXT DEFAULT '',
    markdown_path  TEXT DEFAULT '',
    dissect_path   TEXT DEFAULT '',
    advocatus_path TEXT DEFAULT '',
    agora_path     TEXT DEFAULT '',
    line_count     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS years (
    year   TEXT PRIMARY KEY,
    added  TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS claims (
    paper_id         TEXT NOT NULL,
    loc_line         INTEGER NOT NULL,
    loc_start        INTEGER NOT NULL,
    loc_end          INTEGER NOT NULL,
    text             TEXT NOT NULL,
    section          TEXT DEFAULT '',
    question         TEXT DEFAULT '',
    kind             TEXT DEFAULT 'normative',
    merged_into_line INTEGER,
    merged_into_start INTEGER,
    merged_into_end  INTEGER,
    PRIMARY KEY (paper_id, loc_line, loc_start, loc_end)
);

CREATE TABLE IF NOT EXISTS evidence (
    paper_id         TEXT NOT NULL,
    loc_line         INTEGER NOT NULL,
    loc_start        INTEGER NOT NULL,
    loc_end          INTEGER NOT NULL,
    text             TEXT NOT NULL,
    section          TEXT DEFAULT '',
    supports         TEXT DEFAULT '[]',
    quantitative     INTEGER DEFAULT 0,
    cited            INTEGER DEFAULT 0,
    verifiable       INTEGER DEFAULT 0,
    normative        INTEGER DEFAULT 0,
    merged_into_line INTEGER,
    merged_into_start INTEGER,
    merged_into_end  INTEGER,
    PRIMARY KEY (paper_id, loc_line, loc_start, loc_end)
);

CREATE TABLE IF NOT EXISTS paper_citations (
    paper_id         TEXT NOT NULL,
    cited_paper_id   TEXT NOT NULL,
    count            INTEGER DEFAULT 1,
    PRIMARY KEY (paper_id, cited_paper_id)
);

CREATE TABLE IF NOT EXISTS external_citations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id         TEXT NOT NULL,
    source_url       TEXT DEFAULT '',
    source_title     TEXT DEFAULT '',
    text             TEXT DEFAULT '',
    finding          TEXT DEFAULT '',
    stance           TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS questions (
    paper_id         TEXT NOT NULL,
    loc_line         INTEGER NOT NULL,
    loc_start        INTEGER NOT NULL,
    loc_end          INTEGER NOT NULL,
    claim_text       TEXT NOT NULL,
    section          TEXT DEFAULT '',
    question         TEXT NOT NULL,
    kind             TEXT DEFAULT 'normative',
    PRIMARY KEY (paper_id, loc_line, loc_start, loc_end)
);

CREATE TABLE IF NOT EXISTS rhetorical_markers (
    paper_id         TEXT NOT NULL,
    loc_line         INTEGER NOT NULL,
    loc_start        INTEGER NOT NULL,
    loc_end          INTEGER NOT NULL,
    text             TEXT NOT NULL,
    section          TEXT DEFAULT '',
    marker_type      TEXT DEFAULT '',
    target           TEXT DEFAULT '',
    intensity        TEXT DEFAULT 'moderate',
    PRIMARY KEY (paper_id, loc_line, loc_start, loc_end)
);

CREATE TABLE IF NOT EXISTS caput_causae (
    paper_id TEXT PRIMARY KEY,
    thesis TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS citation_audit (
    paper_id TEXT NOT NULL,
    cited_paper_id TEXT NOT NULL,
    resolution_method TEXT NOT NULL,
    resolved INTEGER NOT NULL,
    source_url TEXT DEFAULT '',
    quote_match TEXT DEFAULT 'not_checked',
    discrepancy TEXT DEFAULT '',
    PRIMARY KEY (paper_id, cited_paper_id)
);

"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial schema."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(papers)").fetchall()}
    if "review_path" in cols and "dissect_path" not in cols:
        conn.execute("ALTER TABLE papers RENAME COLUMN review_path TO dissect_path")
    elif "dissect_path" not in cols:
        conn.execute("ALTER TABLE papers ADD COLUMN dissect_path TEXT DEFAULT ''")
    if "advocatus_path" not in cols:
        conn.execute("ALTER TABLE papers ADD COLUMN advocatus_path TEXT DEFAULT ''")
    if "agora_path" not in cols:
        conn.execute("ALTER TABLE papers ADD COLUMN agora_path TEXT DEFAULT ''")
    if "line_count" not in cols:
        conn.execute("ALTER TABLE papers ADD COLUMN line_count INTEGER DEFAULT 0")

    claim_cols = {r[1] for r in conn.execute("PRAGMA table_info(claims)").fetchall()}
    if "kind" not in claim_cols:
        conn.execute("ALTER TABLE claims ADD COLUMN kind TEXT DEFAULT 'normative'")

    # questions: re-keyed from (paper_id, claim_text, kind) to the loc
    # triple to match the other extract tables. Drop and recreate -
    # the data is rebuilt by the next dissect run, so no migration is
    # needed beyond that.
    question_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(questions)").fetchall()
    }
    if question_cols and "loc_line" not in question_cols:
        conn.execute("DROP TABLE questions")
        conn.execute("""
            CREATE TABLE questions (
                paper_id         TEXT NOT NULL,
                loc_line         INTEGER NOT NULL,
                loc_start        INTEGER NOT NULL,
                loc_end          INTEGER NOT NULL,
                claim_text       TEXT NOT NULL,
                section          TEXT DEFAULT '',
                question         TEXT NOT NULL,
                kind             TEXT DEFAULT 'normative',
                PRIMARY KEY (paper_id, loc_line, loc_start, loc_end)
            )
        """)


def _atomic_replace(src: Path, dst: Path) -> None:
    """Rename ``src`` to ``dst``, retrying on PermissionError (Windows AV/EDR)."""
    for _ in range(10):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            time.sleep(0.1)
    os.replace(src, dst)


def _merged_triple(merged_into: object) -> tuple[int | None, int | None, int | None]:
    if merged_into is not None:
        return merged_into.line, merged_into.start_char, merged_into.end_char  # type: ignore[union-attr]
    return None, None, None


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

        Delegates to :func:`paperstore.factory.default_workspace_dir` for
        env-var resolution. Raises :class:`EnvironmentError` with an
        actionable message when the variable is unset or empty.
        """
        from paperstore.factory import default_workspace_dir

        return cls(default_workspace_dir())

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

    def _row_to_paper(self, row: sqlite3.Row) -> PaperRow:
        d = dict(row)
        return PaperRow(
            paper_id=d.get("paper_id", ""),
            year=d.get("year", ""),
            title=d.get("title", ""),
            authors=parse_authors_raw(d.get("authors", "")),
            target_group=d.get("target_group", ""),
            intent=d.get("intent", ""),
            url=d.get("url", ""),
            document_date=d.get("document_date", ""),
            mailing_date=d.get("mailing_date", ""),
            source_file=d.get("source_file", ""),
            markdown_path=d.get("markdown_path", ""),
            dissect_path=d.get("dissect_path", ""),
            advocatus_path=d.get("advocatus_path", ""),
            agora_path=d.get("agora_path", ""),
            line_count=d.get("line_count", 0),
        )

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
                target_group = p.get("subgroup") or p.get("target_group") or ""
                self._conn.execute(
                    """
                    INSERT INTO papers
                        (paper_id, year, title, authors, target_group, intent,
                         url, document_date, mailing_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(paper_id) DO UPDATE SET
                        year = excluded.year,
                        title = excluded.title,
                        authors = excluded.authors,
                        target_group = excluded.target_group,
                        intent = CASE WHEN papers.intent = ''
                                      THEN excluded.intent
                                      ELSE papers.intent END,
                        url = excluded.url,
                        document_date = excluded.document_date,
                        mailing_date = excluded.mailing_date
                    """,
                    (
                        pid,
                        year,
                        p.get("title") or "",
                        authors_json,
                        target_group,
                        p.get("intent") or "",
                        p.get("url") or "",
                        p.get("document_date") or "",
                        p.get("mailing_date") or "",
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
        return [self._row_to_paper(r) for r in rows]

    def list_all_paper_ids(self) -> list[str]:
        rows = self._conn.execute("SELECT paper_id FROM papers").fetchall()
        return [r["paper_id"] for r in rows]

    def resolve_year_for_paper(self, paper_id: str) -> tuple[str, PaperRow] | None:
        row = self._conn.execute(
            "SELECT * FROM papers WHERE paper_id = ?", (paper_id.strip().upper(),)
        ).fetchone()
        if row is None:
            return None
        paper = self._row_to_paper(row)
        return paper.year, paper

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
        line_count = markdown.count("\n") + 1
        self.record_markdown(pid, final_path, line_count=line_count)
        return final_path

    def write_dissect_md(self, paper_id: str, markdown: str) -> Path:
        """Write dissect markdown atomically and record the path in the DB."""
        pid = paper_id.strip().upper()
        final_path = self._atomic_write_text(
            self._papers_dir / f"{pid.lower()}.dissect.md", markdown
        )
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO papers (paper_id) VALUES (?)", (pid,)
            )
            self._conn.execute(
                "UPDATE papers SET dissect_path = ? WHERE paper_id = ?",
                (str(final_path), pid),
            )
        return final_path

    def clear_dissect(self, paper_id: str) -> None:
        """Delete the dissect file and clear ``dissect_path`` in the DB."""
        pid = paper_id.strip().upper()
        row = self._conn.execute(
            "SELECT dissect_path FROM papers WHERE paper_id = ?", (pid,)
        ).fetchone()
        if row and row["dissect_path"]:
            path = Path(row["dissect_path"])
            if path.exists():
                path.unlink()
        with self._conn:
            self._conn.execute(
                "UPDATE papers SET dissect_path = '' WHERE paper_id = ?",
                (pid,),
            )

    def write_advocatus_md(self, paper_id: str, markdown: str) -> Path:
        """Write advocatus markdown (Relatio) atomically and record the path."""
        pid = paper_id.strip().upper()
        final_path = self._atomic_write_text(
            self._papers_dir / f"{pid.lower()}.advocatus.md", markdown
        )
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO papers (paper_id) VALUES (?)", (pid,)
            )
            self._conn.execute(
                "UPDATE papers SET advocatus_path = ? WHERE paper_id = ?",
                (str(final_path), pid),
            )
        return final_path

    def clear_advocatus(self, paper_id: str) -> None:
        """Delete the advocatus file and clear ``advocatus_path`` in the DB."""
        pid = paper_id.strip().upper()
        row = self._conn.execute(
            "SELECT advocatus_path FROM papers WHERE paper_id = ?", (pid,)
        ).fetchone()
        if row and row["advocatus_path"]:
            path = Path(row["advocatus_path"])
            if path.exists():
                path.unlink()
        with self._conn:
            self._conn.execute(
                "UPDATE papers SET advocatus_path = '' WHERE paper_id = ?",
                (pid,),
            )

    def write_agora_json(self, paper_id: str, payload: Any) -> Path:
        """Write the agora thread blueprint as JSON atomically; record the path."""
        pid = paper_id.strip().upper()
        final_path = self._atomic_write_text(
            self._papers_dir / f"{pid.lower()}.agora.json",
            json.dumps(payload, indent=2, ensure_ascii=False),
        )
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO papers (paper_id) VALUES (?)", (pid,)
            )
            self._conn.execute(
                "UPDATE papers SET agora_path = ? WHERE paper_id = ?",
                (str(final_path), pid),
            )
        return final_path

    def read_agora_json(self, paper_id: str) -> Any:
        """Read the agora JSON for ``paper_id`` and return parsed Python objects."""
        path = self.get_agora_path(paper_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def clear_agora(self, paper_id: str) -> None:
        """Delete the agora file and clear ``agora_path`` in the DB."""
        pid = paper_id.strip().upper()
        row = self._conn.execute(
            "SELECT agora_path FROM papers WHERE paper_id = ?", (pid,)
        ).fetchone()
        if row and row["agora_path"]:
            path = Path(row["agora_path"])
            if path.exists():
                path.unlink()
        with self._conn:
            self._conn.execute(
                "UPDATE papers SET agora_path = '' WHERE paper_id = ?",
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
        self,
        paper_id: str,
        path: Path | str,
        *,
        intent: str | None = None,
        line_count: int | None = None,
    ) -> None:
        """Stamp ``path`` as ``markdown_path`` (and optionally ``intent`` / ``line_count``)."""
        pid = paper_id.strip().upper()
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO papers (paper_id) VALUES (?)", (pid,)
            )
            sets = ["markdown_path = ?"]
            params: list[str | int] = [str(path)]
            if intent:
                sets.append("intent = ?")
                params.append(intent)
            if line_count is not None:
                sets.append("line_count = ?")
                params.append(line_count)
            params.append(pid)
            self._conn.execute(
                f"UPDATE papers SET {', '.join(sets)} WHERE paper_id = ?",
                params,
            )

    _SOURCE_SUFFIXES = (".pdf", ".html", ".htm")

    def reconcile(self) -> dict[str, int]:
        """Backfill DB rows from on-disk artifacts. See ABC for semantics."""
        sources: list[tuple[str, Path]] = []
        markdowns: list[tuple[str, Path]] = []
        dissections: list[tuple[str, Path]] = []
        advocati: list[tuple[str, Path]] = []
        agorae: list[tuple[str, Path]] = []

        for path in sorted(self._papers_dir.iterdir()):
            if not path.is_file():
                continue
            name = path.name
            if name.endswith(".partial"):
                continue
            if name.endswith(".agora.json"):
                agorae.append((name[: -len(".agora.json")].upper(), path))
                continue
            if name.endswith(".json"):
                continue
            # Per-tool debug/trace artifacts are scratch outputs; never
            # reconcile them as paper-level artifacts. This must come
            # before the .md branches below or e.g. ``<pid>.dissect.debug.md``
            # would be mis-classified as markdown.
            if name.endswith(".debug.md") or name.endswith(".trace.md"):
                continue
            if name.endswith(".dissect.md"):
                dissections.append((name[: -len(".dissect.md")].upper(), path))
                continue
            if name.endswith(".advocatus.md"):
                advocati.append((name[: -len(".advocatus.md")].upper(), path))
                continue
            if name.endswith(".md"):
                markdowns.append((name[: -len(".md")].upper(), path))
                continue
            for suffix in self._SOURCE_SUFFIXES:
                if name.endswith(suffix):
                    sources.append((name[: -len(suffix)].upper(), path))
                    break

        counts = {
            "sources": 0,
            "markdowns": 0,
            "dissections": 0,
            "advocati": 0,
            "agorae": 0,
            "line_counts": 0,
        }
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
            for pid, path in dissections:
                self._conn.execute(
                    "INSERT OR IGNORE INTO papers (paper_id) VALUES (?)", (pid,)
                )
                cursor = self._conn.execute(
                    "UPDATE papers SET dissect_path = ? "
                    "WHERE paper_id = ? AND dissect_path = ''",
                    (str(path), pid),
                )
                if cursor.rowcount > 0:
                    counts["dissections"] += 1
            for pid, path in advocati:
                self._conn.execute(
                    "INSERT OR IGNORE INTO papers (paper_id) VALUES (?)", (pid,)
                )
                cursor = self._conn.execute(
                    "UPDATE papers SET advocatus_path = ? "
                    "WHERE paper_id = ? AND advocatus_path = ''",
                    (str(path), pid),
                )
                if cursor.rowcount > 0:
                    counts["advocati"] += 1
            for pid, path in agorae:
                self._conn.execute(
                    "INSERT OR IGNORE INTO papers (paper_id) VALUES (?)", (pid,)
                )
                cursor = self._conn.execute(
                    "UPDATE papers SET agora_path = ? "
                    "WHERE paper_id = ? AND agora_path = ''",
                    (str(path), pid),
                )
                if cursor.rowcount > 0:
                    counts["agorae"] += 1

            # Backfill line_count for rows with markdown but no count
            rows_needing_count = self._conn.execute(
                "SELECT paper_id, markdown_path FROM papers "
                "WHERE markdown_path != '' AND line_count = 0"
            ).fetchall()
            for row in rows_needing_count:
                md_path = Path(row["markdown_path"])
                if md_path.exists():
                    lc = md_path.read_text(encoding="utf-8").count("\n") + 1
                    self._conn.execute(
                        "UPDATE papers SET line_count = ? WHERE paper_id = ?",
                        (lc, row["paper_id"]),
                    )
                    counts["line_counts"] += 1
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
        return self._row_to_paper(row)

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

    def get_dissect_path(self, paper_id: str) -> Path:
        row = self._conn.execute(
            "SELECT dissect_path FROM papers WHERE paper_id = ?",
            (paper_id.strip().upper(),),
        ).fetchone()
        if row is None or not row["dissect_path"]:
            raise MissingDissectError(
                f"No dissect for {paper_id!r}. "
                f"Run 'paperflow dissect {paper_id}' first."
            )
        path = Path(row["dissect_path"])
        if not path.exists():
            raise MissingDissectError(
                f"Dissect file missing for {paper_id!r}: {path}. "
                f"Run 'paperflow dissect {paper_id}' again."
            )
        return path

    def get_advocatus_path(self, paper_id: str) -> Path:
        row = self._conn.execute(
            "SELECT advocatus_path FROM papers WHERE paper_id = ?",
            (paper_id.strip().upper(),),
        ).fetchone()
        if row is None or not row["advocatus_path"]:
            raise MissingAdvocatusError(
                f"No advocatus for {paper_id!r}. "
                f"Run 'paperflow advocatus {paper_id}' first."
            )
        path = Path(row["advocatus_path"])
        if not path.exists():
            raise MissingAdvocatusError(
                f"Advocatus file missing for {paper_id!r}: {path}. "
                f"Run 'paperflow advocatus {paper_id}' again."
            )
        return path

    def get_agora_path(self, paper_id: str) -> Path:
        row = self._conn.execute(
            "SELECT agora_path FROM papers WHERE paper_id = ?",
            (paper_id.strip().upper(),),
        ).fetchone()
        if row is None or not row["agora_path"]:
            raise MissingAgoraError(
                f"No agora JSON for {paper_id!r}. "
                f"Run 'paperflow agora {paper_id}' first."
            )
        path = Path(row["agora_path"])
        if not path.exists():
            raise MissingAgoraError(
                f"Agora file missing for {paper_id!r}: {path}. "
                f"Run 'paperflow agora {paper_id}' again."
            )
        return path

    def get_debug_md_path(self, paper_id: str, tool: str) -> Path:
        return self._tool_artifact_path(paper_id, tool, ".debug.md")

    def get_trace_md_path(self, paper_id: str, tool: str) -> Path:
        return self._tool_artifact_path(paper_id, tool, ".trace.md")

    def _tool_artifact_path(self, paper_id: str, tool: str, suffix: str) -> Path:
        """Compose ``paperstore/<pid>.<tool><suffix>``.

        ``tool`` is normalized to lowercase. Empty / whitespace-only ``tool``
        raises ``ValueError`` to keep the convention enforceable across
        every consuming pipeline.
        """
        normalized_tool = tool.strip().lower()
        if not normalized_tool:
            raise ValueError("tool must be a non-empty identifier")
        pid = paper_id.strip().upper().lower()
        return self._papers_dir / f"{pid}.{normalized_tool}{suffix}"

    def list_years(self) -> list[tuple[str, int]]:
        """Return ``[(year, paper_count)]`` sorted by year."""
        rows = self._conn.execute(
            "SELECT year, COUNT(*) AS n FROM papers "
            "WHERE year != '' GROUP BY year ORDER BY year"
        ).fetchall()
        return [(r["year"], r["n"]) for r in rows]

    def list_papers_since(self, month: str) -> list[PaperRow]:
        rows = self._conn.execute(
            "SELECT * FROM papers WHERE mailing_date >= ? ORDER BY mailing_date",
            (month,),
        ).fetchall()
        return [self._row_to_paper(r) for r in rows]

    # ---- extract writes ---------------------------------------------------

    def store_claims(self, paper_id: str, claims) -> None:
        pid = paper_id.strip().upper()
        rows = []
        for c in claims:
            ml, ms, me = _merged_triple(c.merged_into)
            rows.append((
                pid, c.loc.line, c.loc.start_char, c.loc.end_char,
                c.text, c.section, c.question,
                getattr(c, "kind", "normative"), ml, ms, me,
            ))
        with self._conn:
            self._conn.execute("DELETE FROM claims WHERE paper_id = ?", (pid,))
            self._conn.executemany(
                "INSERT INTO claims (paper_id, loc_line, loc_start, loc_end, "
                "text, section, question, kind, merged_into_line, "
                "merged_into_start, merged_into_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def store_evidence(self, paper_id: str, evidence) -> None:
        pid = paper_id.strip().upper()
        rows = []
        for e in evidence:
            ml, ms, me = _merged_triple(e.merged_into)
            rows.append((
                pid, e.loc.line, e.loc.start_char, e.loc.end_char,
                e.text, e.section, json.dumps(e.supports),
                int(e.quantitative), int(e.cited),
                int(e.verifiable), int(e.normative), ml, ms, me,
            ))
        with self._conn:
            self._conn.execute("DELETE FROM evidence WHERE paper_id = ?", (pid,))
            self._conn.executemany(
                "INSERT INTO evidence (paper_id, loc_line, loc_start, loc_end, "
                "text, section, supports, quantitative, cited, verifiable, "
                "normative, merged_into_line, merged_into_start, merged_into_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def store_paper_citations(self, paper_id: str, citations) -> None:
        pid = paper_id.strip().upper()
        rows = [(pid, c.paper_id, c.count) for c in citations]
        with self._conn:
            self._conn.execute("DELETE FROM paper_citations WHERE paper_id = ?", (pid,))
            self._conn.executemany(
                "INSERT INTO paper_citations (paper_id, cited_paper_id, count) "
                "VALUES (?, ?, ?)",
                rows,
            )

    def store_external_citations(self, paper_id: str, externals) -> None:
        pid = paper_id.strip().upper()
        rows = [
            (pid, ex.source_url, ex.source_title, ex.text, ex.finding, ex.stance)
            for ex in externals
        ]
        with self._conn:
            self._conn.execute("DELETE FROM external_citations WHERE paper_id = ?", (pid,))
            self._conn.executemany(
                "INSERT INTO external_citations (paper_id, source_url, "
                "source_title, text, finding, stance) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )

    def store_questions(self, paper_id: str, claims, support_map) -> None:
        pid = paper_id.strip().upper()
        unsupported_locs = {
            s.claim_loc for s in support_map if s.status == "unsupported"
        }
        rows = [
            (
                pid,
                c.loc.line, c.loc.start_char, c.loc.end_char,
                c.text, c.section, c.question,
                getattr(c, "kind", "normative"),
            )
            for c in claims
            if c.merged_into is None and c.loc in unsupported_locs
        ]
        with self._conn:
            self._conn.execute("DELETE FROM questions WHERE paper_id = ?", (pid,))
            self._conn.executemany(
                "INSERT INTO questions (paper_id, loc_line, loc_start, loc_end, "
                "claim_text, section, question, kind) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def store_markers(self, paper_id: str, markers) -> None:
        pid = paper_id.strip().upper()
        rows = [
            (pid, m.loc.line, m.loc.start_char, m.loc.end_char,
             m.text, m.section, m.marker_type, m.target, m.intensity)
            for m in markers
        ]
        with self._conn:
            self._conn.execute("DELETE FROM rhetorical_markers WHERE paper_id = ?", (pid,))
            self._conn.executemany(
                "INSERT INTO rhetorical_markers (paper_id, loc_line, loc_start, loc_end, "
                "text, section, marker_type, target, intensity) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def store_caput_causae(self, paper_id: str, thesis: str) -> None:
        pid = paper_id.strip().upper()
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO caput_causae (paper_id, thesis) "
                "VALUES (?, ?)",
                (pid, thesis),
            )

    def store_citation_audit(self, paper_id: str, audits) -> None:
        pid = paper_id.strip().upper()
        rows = [
            (pid, a.cited_paper_id, a.resolution_method,
             int(a.resolved), a.source_url, a.quote_match, a.discrepancy)
            for a in audits
        ]
        with self._conn:
            self._conn.execute("DELETE FROM citation_audit WHERE paper_id = ?", (pid,))
            self._conn.executemany(
                "INSERT INTO citation_audit (paper_id, cited_paper_id, "
                "resolution_method, resolved, source_url, quote_match, "
                "discrepancy) VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    # ---- extract reads ----------------------------------------------------

    def get_claims(self, paper_id: str) -> list[ClaimRow]:
        pid = paper_id.strip().upper()
        rows = self._conn.execute(
            "SELECT * FROM claims WHERE paper_id = ?", (pid,)
        ).fetchall()
        return [ClaimRow(
            paper_id=r["paper_id"], loc_line=r["loc_line"],
            loc_start=r["loc_start"], loc_end=r["loc_end"],
            text=r["text"], section=r["section"], question=r["question"],
            kind=r["kind"], merged_into_line=r["merged_into_line"],
            merged_into_start=r["merged_into_start"],
            merged_into_end=r["merged_into_end"],
        ) for r in rows]

    def get_evidence(self, paper_id: str) -> list[EvidenceRow]:
        pid = paper_id.strip().upper()
        rows = self._conn.execute(
            "SELECT * FROM evidence WHERE paper_id = ?", (pid,)
        ).fetchall()
        return [EvidenceRow(
            paper_id=r["paper_id"], loc_line=r["loc_line"],
            loc_start=r["loc_start"], loc_end=r["loc_end"],
            text=r["text"], section=r["section"], supports=r["supports"],
            quantitative=bool(r["quantitative"]), cited=bool(r["cited"]),
            verifiable=bool(r["verifiable"]), normative=bool(r["normative"]),
            merged_into_line=r["merged_into_line"],
            merged_into_start=r["merged_into_start"],
            merged_into_end=r["merged_into_end"],
        ) for r in rows]

    def get_paper_citations(self, paper_id: str) -> list[PaperCitationRow]:
        pid = paper_id.strip().upper()
        rows = self._conn.execute(
            "SELECT * FROM paper_citations WHERE paper_id = ? ORDER BY count DESC",
            (pid,),
        ).fetchall()
        return [PaperCitationRow(
            paper_id=r["paper_id"], cited_paper_id=r["cited_paper_id"],
            count=r["count"],
        ) for r in rows]

    def get_external_citations(self, paper_id: str) -> list[ExternalCitationRow]:
        pid = paper_id.strip().upper()
        rows = self._conn.execute(
            "SELECT * FROM external_citations WHERE paper_id = ?", (pid,)
        ).fetchall()
        return [ExternalCitationRow(
            paper_id=r["paper_id"], source_url=r["source_url"],
            source_title=r["source_title"], text=r["text"],
            finding=r["finding"], stance=r["stance"],
        ) for r in rows]

    def get_questions(self, paper_id: str) -> list[QuestionRow]:
        pid = paper_id.strip().upper()
        rows = self._conn.execute(
            "SELECT * FROM questions WHERE paper_id = ?", (pid,)
        ).fetchall()
        return [QuestionRow(
            paper_id=r["paper_id"],
            loc_line=r["loc_line"],
            loc_start=r["loc_start"],
            loc_end=r["loc_end"],
            claim_text=r["claim_text"],
            section=r["section"], question=r["question"],
            kind=r["kind"],
        ) for r in rows]

    def get_markers(self, paper_id: str) -> list[MarkerRow]:
        pid = paper_id.strip().upper()
        rows = self._conn.execute(
            "SELECT * FROM rhetorical_markers WHERE paper_id = ?", (pid,)
        ).fetchall()
        return [MarkerRow(
            paper_id=r["paper_id"], loc_line=r["loc_line"],
            loc_start=r["loc_start"], loc_end=r["loc_end"],
            text=r["text"], section=r["section"],
            marker_type=r["marker_type"], target=r["target"],
            intensity=r["intensity"],
        ) for r in rows]

    def get_caput_causae(self, paper_id: str) -> CaputCausaeRow | None:
        pid = paper_id.strip().upper()
        row = self._conn.execute(
            "SELECT * FROM caput_causae WHERE paper_id = ?", (pid,)
        ).fetchone()
        if row is None:
            return None
        return CaputCausaeRow(paper_id=row["paper_id"], thesis=row["thesis"])

    def get_citation_audit(self, paper_id: str) -> list[CitationAuditRow]:
        pid = paper_id.strip().upper()
        rows = self._conn.execute(
            "SELECT * FROM citation_audit WHERE paper_id = ?", (pid,)
        ).fetchall()
        return [CitationAuditRow(
            paper_id=r["paper_id"], cited_paper_id=r["cited_paper_id"],
            resolution_method=r["resolution_method"],
            resolved=bool(r["resolved"]), source_url=r["source_url"],
            quote_match=r["quote_match"], discrepancy=r["discrepancy"],
        ) for r in rows]
