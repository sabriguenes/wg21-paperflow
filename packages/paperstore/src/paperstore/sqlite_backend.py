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
import re
import sqlite3
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paperstore.backend import ClearedSet, PaperRow, StorageBackend, parse_authors_raw
from paperstore.extract_rows import (
    CandidateRow,
    CaputCausaeRow,
    CitationAuditRow,
    ClaimRow,
    EvidenceRow,
    ExternalCitationRow,
    FindingRow,
    RhetoricRow,
    PaperCitationRow,
    QuestionRow,
)
from paperstore.errors import (
    InvalidSuffixError,
    MissingAdvocatusError,
    MissingAgoraError,
    MissingMailingIndexError,
    MissingMetaError,
    MissingPaperMdError,
    MissingSourceError,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    paper_id         TEXT PRIMARY KEY,
    year             TEXT DEFAULT '',
    title            TEXT DEFAULT '',
    authors          TEXT DEFAULT '',
    target_group     TEXT DEFAULT '',
    intent           TEXT DEFAULT '',
    url              TEXT DEFAULT '',
    document_date    TEXT DEFAULT '',
    mailing_date     TEXT DEFAULT '',
    disposition      TEXT DEFAULT '',
    previous_version TEXT DEFAULT '',
    source_file      TEXT DEFAULT '',
    markdown_path    TEXT DEFAULT '',
    dissect_path     TEXT DEFAULT '',  -- deprecated, kept for migration safety
    advocatus_path   TEXT DEFAULT '',
    agora_path       TEXT DEFAULT '',
    line_count       INTEGER DEFAULT 0,
    status           INTEGER NOT NULL DEFAULT 0,
    error            TEXT DEFAULT '',
    updated_at       TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS years (
    year   TEXT PRIMARY KEY,
    added  TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS mailings (
    mailing_id TEXT PRIMARY KEY,
    label      TEXT DEFAULT '',
    added      TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS claims (
    paper_id         TEXT NOT NULL,
    uid              INTEGER NOT NULL,
    loc_line         INTEGER NOT NULL,
    loc_start        INTEGER NOT NULL,
    loc_end          INTEGER NOT NULL,
    text             TEXT NOT NULL,
    section          TEXT DEFAULT '',
    question         TEXT DEFAULT '',
    kind             TEXT DEFAULT 'normative',
    merged_into      INTEGER,
    PRIMARY KEY (paper_id, uid)
);

CREATE TABLE IF NOT EXISTS evidence (
    paper_id         TEXT NOT NULL,
    uid              INTEGER NOT NULL,
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
    merged_into      INTEGER,
    PRIMARY KEY (paper_id, uid)
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
    uid              INTEGER NOT NULL,
    loc_line         INTEGER NOT NULL,
    loc_start        INTEGER NOT NULL,
    loc_end          INTEGER NOT NULL,
    claim_text       TEXT NOT NULL,
    section          TEXT DEFAULT '',
    question         TEXT NOT NULL,
    kind             TEXT DEFAULT 'normative',
    PRIMARY KEY (paper_id, uid)
);

CREATE TABLE IF NOT EXISTS rhetoric (
    paper_id         TEXT NOT NULL,
    uid              INTEGER NOT NULL,
    loc_line         INTEGER NOT NULL,
    loc_start        INTEGER NOT NULL,
    loc_end          INTEGER NOT NULL,
    text             TEXT NOT NULL,
    section          TEXT DEFAULT '',
    marker_type      TEXT DEFAULT '',
    target           TEXT DEFAULT '',
    intensity        TEXT DEFAULT 'medium',
    PRIMARY KEY (paper_id, uid)
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

CREATE TABLE IF NOT EXISTS candidates (
    paper_id TEXT NOT NULL,
    rule     TEXT NOT NULL,
    label    TEXT NOT NULL,
    detail   TEXT DEFAULT '',
    data     TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS findings (
    paper_id    TEXT NOT NULL,
    id          TEXT NOT NULL,
    lens        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    title       TEXT NOT NULL,
    quoted_text TEXT DEFAULT '',
    source_line INTEGER DEFAULT 0,
    explanation TEXT DEFAULT '',
    PRIMARY KEY (paper_id, id)
);

CREATE TABLE IF NOT EXISTS signals (
    paper_id     TEXT NOT NULL,
    section_idx  INTEGER NOT NULL,
    heading      TEXT DEFAULT '',
    loc_line     INTEGER NOT NULL,
    loc_start    INTEGER DEFAULT 0,
    loc_end      INTEGER DEFAULT 0,
    signal_type  TEXT NOT NULL,
    quote        TEXT DEFAULT '',
    observation  TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS assay_claims (
    paper_id     TEXT NOT NULL,
    uid          INTEGER NOT NULL,
    loc_line     INTEGER NOT NULL,
    quote        TEXT NOT NULL,
    section      TEXT DEFAULT '',
    kind         TEXT DEFAULT 'normative',
    load_bearing INTEGER DEFAULT 0,
    PRIMARY KEY (paper_id, uid)
);
CREATE TABLE IF NOT EXISTS assay_evidence (
    paper_id     TEXT NOT NULL,
    uid          INTEGER NOT NULL,
    loc_line     INTEGER NOT NULL,
    quote        TEXT NOT NULL,
    section      TEXT DEFAULT '',
    subtype      TEXT DEFAULT '',
    quality_tier TEXT DEFAULT '',
    supports     TEXT DEFAULT '[]',
    PRIMARY KEY (paper_id, uid)
);
CREATE TABLE IF NOT EXISTS assay_concessions (
    paper_id TEXT NOT NULL,
    uid      INTEGER NOT NULL,
    loc_line INTEGER NOT NULL,
    quote    TEXT NOT NULL,
    section  TEXT DEFAULT '',
    subtype  TEXT DEFAULT '',
    PRIMARY KEY (paper_id, uid)
);
CREATE TABLE IF NOT EXISTS assay_breadcrumbs (
    paper_id       TEXT NOT NULL,
    uid            INTEGER NOT NULL,
    chunk_index    INTEGER NOT NULL,
    loc_line       INTEGER NOT NULL,
    gap            TEXT NOT NULL,
    why_important  TEXT DEFAULT '',
    primary_lens   TEXT DEFAULT '',
    secondary_lens TEXT DEFAULT '',
    severity       TEXT DEFAULT 'minor',
    PRIMARY KEY (paper_id, uid)
);
CREATE TABLE IF NOT EXISTS assay_thesis (
    paper_id          TEXT PRIMARY KEY,
    central_claim     TEXT NOT NULL,
    problem_statement TEXT DEFAULT '',
    scope_boundary    TEXT DEFAULT '',
    ask_calibration   TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS assay_findings (
    paper_id    TEXT NOT NULL,
    uid         INTEGER NOT NULL,
    title       TEXT NOT NULL,
    lens        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    quote       TEXT DEFAULT '',
    loc_line    INTEGER DEFAULT 0,
    explanation TEXT DEFAULT '',
    test        TEXT DEFAULT '',
    survived    INTEGER DEFAULT 1,
    major       INTEGER DEFAULT 0,
    challenge   TEXT DEFAULT '',
    reasoning   TEXT DEFAULT '',
    PRIMARY KEY (paper_id, uid)
);
CREATE TABLE IF NOT EXISTS assay_asks (
    paper_id TEXT NOT NULL,
    uid      INTEGER NOT NULL,
    target   TEXT NOT NULL,
    quote    TEXT NOT NULL,
    type     TEXT NOT NULL,
    PRIMARY KEY (paper_id, uid)
);
CREATE TABLE IF NOT EXISTS assay_references (
    paper_id       TEXT NOT NULL,
    uid            INTEGER NOT NULL,
    ref_label      TEXT NOT NULL,
    relationship   TEXT DEFAULT 'citation',
    url            TEXT DEFAULT '',
    mention_count  INTEGER DEFAULT 0,
    PRIMARY KEY (paper_id, uid)
);
CREATE TABLE IF NOT EXISTS assay_strengths (
    paper_id    TEXT NOT NULL,
    uid         INTEGER NOT NULL,
    title       TEXT NOT NULL,
    quote       TEXT DEFAULT '',
    loc_line    INTEGER DEFAULT 0,
    explanation TEXT DEFAULT '',
    PRIMARY KEY (paper_id, uid)
);
CREATE TABLE IF NOT EXISTS assay_checklist (
    paper_id TEXT NOT NULL,
    item_id  TEXT NOT NULL,
    name     TEXT NOT NULL,
    passed   INTEGER DEFAULT 0,
    location TEXT DEFAULT '',
    note     TEXT DEFAULT '',
    PRIMARY KEY (paper_id, item_id)
);
CREATE TABLE IF NOT EXISTS assay_compounds (
    paper_id      TEXT NOT NULL,
    uid           INTEGER NOT NULL,
    name          TEXT NOT NULL,
    constituents  TEXT DEFAULT '[]',
    mechanism     TEXT DEFAULT '',
    cross_lens    INTEGER DEFAULT 0,
    emergent_risk TEXT DEFAULT '',
    PRIMARY KEY (paper_id, uid)
);
CREATE TABLE IF NOT EXISTS assay_synthesis (
    paper_id           TEXT PRIMARY KEY,
    verdict            TEXT NOT NULL,
    verdict_confidence TEXT DEFAULT 'Medium',
    thesis_statement   TEXT DEFAULT '',
    thesis_survives    INTEGER DEFAULT 0,
    central_thesis     TEXT DEFAULT '',
    dominant_dynamic   TEXT DEFAULT '',
    critical_count     INTEGER DEFAULT 0,
    significant_count  INTEGER DEFAULT 0
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

    if "status" not in cols:
        conn.execute(
            "ALTER TABLE papers ADD COLUMN status INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute(
            "ALTER TABLE papers ADD COLUMN error TEXT DEFAULT ''"
        )
        conn.execute(
            "ALTER TABLE papers ADD COLUMN updated_at TEXT DEFAULT ''"
        )
        conn.execute("""
            UPDATE papers SET status =
                CASE
                    WHEN agora_path != ''     THEN 5
                    WHEN advocatus_path != '' THEN 4
                    WHEN dissect_path != ''   THEN 3
                    WHEN markdown_path != ''  THEN 2
                    WHEN source_file != ''    THEN 1
                    ELSE 0
                END
        """)

    if "disposition" not in cols:
        conn.execute("ALTER TABLE papers ADD COLUMN disposition TEXT DEFAULT ''")
    if "previous_version" not in cols:
        conn.execute(
            "ALTER TABLE papers ADD COLUMN previous_version TEXT DEFAULT ''"
        )

    conn.executescript(
        "CREATE TABLE IF NOT EXISTS settings ("
        "    key   TEXT PRIMARY KEY,"
        "    value TEXT NOT NULL"
        ");"
    )
    if not conn.execute(
        "SELECT 1 FROM settings WHERE key = 'process_since'"
    ).fetchone():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('process_since', '2020-01')"
        )

    claim_cols = {r[1] for r in conn.execute("PRAGMA table_info(claims)").fetchall()}
    if "kind" not in claim_cols:
        conn.execute("ALTER TABLE claims ADD COLUMN kind TEXT DEFAULT 'normative'")

    # SourceLoc-to-uid migration: PK changes from the loc triple to
    # (paper_id, uid).
    def _needs_uid(table: str) -> bool:
        cols = {r[1] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()}
        return bool(cols) and "uid" not in cols

    for tbl in ("claims", "evidence", "questions"):
        if _needs_uid(tbl):
            conn.execute(f"DROP TABLE {tbl}")

    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "rhetorical_markers" in tables:
        conn.execute("DROP TABLE rhetorical_markers")

    # assay_path column
    cols = {r[1] for r in conn.execute("PRAGMA table_info(papers)").fetchall()}
    if "assay_path" not in cols:
        conn.execute("ALTER TABLE papers ADD COLUMN assay_path TEXT DEFAULT ''")

    # assay_findings: add challenge/reasoning columns for killed finding details
    finding_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(assay_findings)"
    ).fetchall()}
    if finding_cols and "challenge" not in finding_cols:
        conn.execute(
            "ALTER TABLE assay_findings ADD COLUMN challenge TEXT DEFAULT ''"
        )
        conn.execute(
            "ALTER TABLE assay_findings ADD COLUMN reasoning TEXT DEFAULT ''"
        )

    conn.executescript(_SCHEMA)


# Extracted image filenames are ``<pid>-fig{page}-{index}.{ext}`` with the
# pid lowercased. The mandatory ``-fig`` separator prevents pid-prefix
# collisions (e.g. ``p30-fig...`` cannot match ``p301-fig...`` because the
# greedy ``\d+`` consumes the full pid run before the ``-fig`` literal).
# Used by ``iter_paper_image_paths`` / ``delete_paper_images`` to authoritatively
# filter candidate filenames returned by ``Path.iterdir()``.
_IMAGE_FILENAME_RE = re.compile(
    r"^(?P<pid>[a-z]\d+(?:r\d+)?)-fig(?P<page>\d+)-(?P<index>\d+)\.(?P<ext>[a-z0-9]+)$"
)


_ASSAY_TABLES = [
    "assay_claims", "assay_evidence", "assay_concessions",
    "assay_breadcrumbs", "assay_thesis", "assay_findings",
    "assay_asks", "assay_references", "assay_strengths",
    "assay_checklist", "assay_compounds", "assay_synthesis",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        self._conn.execute("PRAGMA busy_timeout = 5000")
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
            disposition=d.get("disposition", ""),
            previous_version=d.get("previous_version", ""),
            source_file=d.get("source_file", ""),
            markdown_path=d.get("markdown_path", ""),
            dissect_path=d.get("dissect_path", ""),
            advocatus_path=d.get("advocatus_path", ""),
            agora_path=d.get("agora_path", ""),
            assay_path=d.get("assay_path", ""),
            line_count=d.get("line_count", 0),
            status=d.get("status", 0),
            error=d.get("error", ""),
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
        mailing_labels: dict[str, str] = {}
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
                         url, document_date, mailing_date, disposition,
                         previous_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        mailing_date = excluded.mailing_date,
                        disposition = excluded.disposition,
                        previous_version = excluded.previous_version
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
                        p.get("disposition") or "",
                        p.get("previous_version") or "",
                    ),
                )
                mid = (p.get("mailing_date") or "").strip()
                ml = (p.get("mailing_label") or "").strip()
                if mid and ml:
                    mailing_labels[mid] = ml
            for mid, label in mailing_labels.items():
                self._conn.execute(
                    "INSERT INTO mailings (mailing_id, label, added) "
                    "VALUES (?, ?, ?) "
                    "ON CONFLICT(mailing_id) DO UPDATE SET label = excluded.label",
                    (mid, label, now),
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

    def upsert_mailing_label(self, mailing_id: str, label: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT INTO mailings (mailing_id, label, added) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(mailing_id) DO UPDATE SET label = excluded.label",
                (mailing_id.strip(), label, now),
            )

    def get_mailing_label(self, mailing_id: str) -> str:
        row = self._conn.execute(
            "SELECT label FROM mailings WHERE mailing_id = ?",
            (mailing_id.strip(),),
        ).fetchone()
        return str(row["label"]) if row else ""

    def find_latest_revision(self, base_id: str) -> str | None:
        """Find the latest revision for a paper number without revision suffix.

        ``base_id`` is e.g. ``P4003`` (no R suffix). Returns the full
        paper_id of the highest revision (e.g. ``P4003R3``), or None.
        """
        import re
        base = base_id.strip().upper()
        rows = self._conn.execute(
            "SELECT paper_id FROM papers WHERE paper_id LIKE ?",
            (f"{base}R%",),
        ).fetchall()
        if not rows:
            return None
        rev_re = re.compile(rf"^{re.escape(base)}R(\d+)$")
        best_pid = None
        best_rev = -1
        for row in rows:
            m = rev_re.match(row["paper_id"])
            if m and int(m.group(1)) > best_rev:
                best_rev = int(m.group(1))
                best_pid = row["paper_id"]
        return best_pid

    # ---- writes -----------------------------------------------------------

    def put_source(self, paper_id: str, content: bytes, *, suffix: str) -> Path:
        """Write source bytes atomically and record the path in the DB."""
        if not suffix.startswith("."):
            raise InvalidSuffixError(
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
            # before the .md branches below or e.g. ``<pid>.debug.assay.md``
            # would be mis-classified as markdown.
            if name.endswith(".debug.md") or name.endswith(".trace.md"):
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

    # ---- image artifacts and downstream invalidation ---------------------

    def get_paper_image_path(
        self, paper_id: str, page: int, index: int, ext: str
    ) -> Path:
        pid = paper_id.strip().lower()
        ext = ext.lstrip(".").lower()
        return self._papers_dir / f"{pid}-fig{page}-{index}.{ext}"

    def write_paper_image(
        self,
        paper_id: str,
        page: int,
        index: int,
        ext: str,
        data: bytes,
    ) -> Path:
        final_path = self.get_paper_image_path(paper_id, page, index, ext)
        return self._atomic_write_bytes(final_path, data)

    def iter_paper_image_paths(self, paper_id: str) -> Iterator[Path]:
        target = paper_id.strip().lower()
        # Materialize before yielding so a concurrent ``delete_paper_images``
        # call can safely consume this iterator without racing iterdir().
        matches: list[Path] = []
        for p in self._papers_dir.iterdir():
            if not p.is_file():
                continue
            m = _IMAGE_FILENAME_RE.match(p.name)
            if m and m.group("pid") == target:
                matches.append(p)
        matches.sort(key=lambda path: path.name)
        return iter(matches)

    def delete_paper_images(self, paper_id: str) -> int:
        count = 0
        for path in self.iter_paper_image_paths(paper_id):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            count += 1
        return count

    def get_html_images_manifest_path(self, paper_id: str) -> Path:
        pid = paper_id.strip().lower()
        return self._papers_dir / f"{pid}.html-images.json"

    def clear_downstream_outputs(self, paper_id: str) -> ClearedSet:
        pid = paper_id.strip().upper()
        try:
            meta = self.get_meta(pid)
        except MissingMetaError:
            return ClearedSet()

        advocatus_present = bool(meta.advocatus_path)
        agora_present = bool(meta.agora_path)
        assay_present = bool(meta.assay_path)

        if advocatus_present:
            self.clear_advocatus(pid)
        if agora_present:
            self.clear_agora(pid)
        if assay_present:
            # clear_assay also wipes the 12 assay_* tables, whose loc_line
            # offsets would otherwise point at stale lines after re-convert.
            self.clear_assay(pid)

        return ClearedSet(
            advocatus=advocatus_present,
            agora=agora_present,
            assay=assay_present,
        )

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

    def try_read_paper_md(self, paper_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT markdown_path FROM papers WHERE paper_id = ?",
            (paper_id.strip().upper(),),
        ).fetchone()
        if row is None or not row["markdown_path"]:
            return None
        path = Path(row["markdown_path"])
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def get_paper_md_path(self, paper_id: str) -> Path:
        pid = paper_id.strip().upper()
        return self._papers_dir / f"{pid.lower()}.md"

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

    def get_debug_md_path(self, paper_id: str, tool: str = "") -> Path:
        pid = paper_id.strip().upper().lower()
        suffix = f".debug.{tool}.md" if tool else ".debug.md"
        return self._papers_dir / f"{pid}{suffix}"

    def get_trace_md_path(self, paper_id: str, tool: str = "") -> Path:
        pid = paper_id.strip().upper().lower()
        suffix = f".trace.{tool}.md" if tool else ".trace.md"
        return self._papers_dir / f"{pid}{suffix}"

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

    # ---- status / settings ------------------------------------------------

    def advance_status(self, paper_id: str, from_status: int, to_status: int) -> bool:
        """CAS: advance only if current status matches from_status. Clears error."""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE papers SET status = ?, error = '', updated_at = ? "
                "WHERE paper_id = ? AND status = ?",
                (to_status, _now_iso(), paper_id.strip().upper(), from_status),
            )
            return cur.rowcount == 1

    def fail_paper(self, paper_id: str, stage: int, error: str) -> None:
        """Mark paper as failed at the given stage with the latest error."""
        with self._conn:
            self._conn.execute(
                "UPDATE papers SET status = ?, error = ?, updated_at = ? WHERE paper_id = ?",
                (-(stage + 1), error, _now_iso(), paper_id.strip().upper()),
            )

    def get_setting(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )

    # ---- extract writes ---------------------------------------------------

    def store_claims(self, paper_id: str, claims) -> None:
        pid = paper_id.strip().upper()
        rows = [
            (pid, c.uid, c.loc.line, c.loc.start_char, c.loc.end_char,
             c.text, c.section, c.question,
             getattr(c, "kind", "normative"), c.merged_into)
            for c in claims
        ]
        with self._conn:
            self._conn.execute("DELETE FROM claims WHERE paper_id = ?", (pid,))
            self._conn.executemany(
                "INSERT INTO claims (paper_id, uid, loc_line, loc_start, loc_end, "
                "text, section, question, kind, merged_into) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def store_evidence(self, paper_id: str, evidence) -> None:
        pid = paper_id.strip().upper()
        rows = [
            (pid, e.uid, e.loc.line, e.loc.start_char, e.loc.end_char,
             e.text, e.section, json.dumps(e.supports),
             int(e.quantitative), int(e.cited),
             int(e.verifiable), int(e.normative), e.merged_into)
            for e in evidence
        ]
        with self._conn:
            self._conn.execute("DELETE FROM evidence WHERE paper_id = ?", (pid,))
            self._conn.executemany(
                "INSERT INTO evidence (paper_id, uid, loc_line, loc_start, loc_end, "
                "text, section, supports, quantitative, cited, verifiable, "
                "normative, merged_into) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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

    def store_questions(self, paper_id: str, claims, verdicts) -> None:
        pid = paper_id.strip().upper()
        unsupported_uids = {
            v.claim_uid for v in verdicts if v.status == "unproven"
        }
        rows = [
            (
                pid, c.uid,
                c.loc.line, c.loc.start_char, c.loc.end_char,
                c.text, c.section, c.question,
                getattr(c, "kind", "normative"),
            )
            for c in claims
            if c.merged_into is None and c.uid in unsupported_uids
        ]
        with self._conn:
            self._conn.execute("DELETE FROM questions WHERE paper_id = ?", (pid,))
            self._conn.executemany(
                "INSERT INTO questions (paper_id, uid, loc_line, loc_start, loc_end, "
                "claim_text, section, question, kind) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def store_rhetoric(self, paper_id: str, markers) -> None:
        pid = paper_id.strip().upper()
        rows = [
            (pid, m.uid, m.loc.line, m.loc.start_char, m.loc.end_char,
             m.text, m.section, m.marker_type, m.target, m.intensity)
            for m in markers
        ]
        with self._conn:
            self._conn.execute("DELETE FROM rhetoric WHERE paper_id = ?", (pid,))
            self._conn.executemany(
                "INSERT INTO rhetoric (paper_id, uid, loc_line, loc_start, loc_end, "
                "text, section, marker_type, target, intensity) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            paper_id=r["paper_id"], uid=r["uid"],
            loc_line=r["loc_line"],
            loc_start=r["loc_start"], loc_end=r["loc_end"],
            text=r["text"], section=r["section"], question=r["question"],
            kind=r["kind"], merged_into=r["merged_into"],
        ) for r in rows]

    def get_evidence(self, paper_id: str) -> list[EvidenceRow]:
        pid = paper_id.strip().upper()
        rows = self._conn.execute(
            "SELECT * FROM evidence WHERE paper_id = ?", (pid,)
        ).fetchall()
        return [EvidenceRow(
            paper_id=r["paper_id"], uid=r["uid"],
            loc_line=r["loc_line"],
            loc_start=r["loc_start"], loc_end=r["loc_end"],
            text=r["text"], section=r["section"], supports=r["supports"],
            quantitative=bool(r["quantitative"]), cited=bool(r["cited"]),
            verifiable=bool(r["verifiable"]), normative=bool(r["normative"]),
            merged_into=r["merged_into"],
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
            paper_id=r["paper_id"], uid=r["uid"],
            loc_line=r["loc_line"],
            loc_start=r["loc_start"],
            loc_end=r["loc_end"],
            claim_text=r["claim_text"],
            section=r["section"], question=r["question"],
            kind=r["kind"],
        ) for r in rows]

    def get_rhetoric(self, paper_id: str) -> list[RhetoricRow]:
        pid = paper_id.strip().upper()
        rows = self._conn.execute(
            "SELECT * FROM rhetoric WHERE paper_id = ?", (pid,)
        ).fetchall()
        return [RhetoricRow(
            paper_id=r["paper_id"], uid=r["uid"],
            loc_line=r["loc_line"],
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

    # ---- candidates / findings -----------------------------------------------

    def store_candidates(self, paper_id: str, candidates) -> None:
        pid = paper_id.strip().upper()
        rows = [
            (pid, c.rule, c.label, c.detail,
             json.dumps(c.data) if not isinstance(c.data, str) else c.data)
            for c in candidates
        ]
        with self._conn:
            self._conn.execute("DELETE FROM candidates WHERE paper_id = ?", (pid,))
            self._conn.executemany(
                "INSERT INTO candidates (paper_id, rule, label, detail, data) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )

    def store_findings(self, paper_id: str, findings) -> None:
        pid = paper_id.strip().upper()
        rows = [
            (pid, f.id, f.lens, f.severity, f.title,
             f.quoted_text, f.source_line, f.explanation)
            for f in findings
        ]
        with self._conn:
            self._conn.execute("DELETE FROM findings WHERE paper_id = ?", (pid,))
            self._conn.executemany(
                "INSERT INTO findings (paper_id, id, lens, severity, title, "
                "quoted_text, source_line, explanation) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def get_candidates(self, paper_id: str) -> list[CandidateRow]:
        pid = paper_id.strip().upper()
        rows = self._conn.execute(
            "SELECT * FROM candidates WHERE paper_id = ?", (pid,)
        ).fetchall()
        return [CandidateRow(
            paper_id=r["paper_id"], rule=r["rule"], label=r["label"],
            detail=r["detail"], data=r["data"],
        ) for r in rows]

    def get_findings(self, paper_id: str) -> list[FindingRow]:
        pid = paper_id.strip().upper()
        rows = self._conn.execute(
            "SELECT * FROM findings WHERE paper_id = ?", (pid,)
        ).fetchall()
        return [FindingRow(
            paper_id=r["paper_id"], id=r["id"], lens=r["lens"],
            severity=r["severity"], title=r["title"],
            quoted_text=r["quoted_text"], source_line=r["source_line"],
            explanation=r["explanation"],
        ) for r in rows]

    def store_signals(self, paper_id: str, signals) -> None:
        pid = paper_id.strip().upper()
        rows = [
            (pid, s.section_idx, s.heading, s.loc_line, s.loc_start,
             s.loc_end, s.signal_type, s.quote, s.observation)
            for s in signals
        ]
        with self._conn:
            self._conn.execute("DELETE FROM signals WHERE paper_id = ?", (pid,))
            self._conn.executemany(
                "INSERT INTO signals (paper_id, section_idx, heading, "
                "loc_line, loc_start, loc_end, signal_type, quote, observation) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def get_signals(self, paper_id: str) -> list[dict]:
        pid = paper_id.strip().upper()
        rows = self._conn.execute(
            "SELECT * FROM signals WHERE paper_id = ?", (pid,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- assay ----------------------------------------------------------------

    def store_assay_claims(self, paper_id: str, claims) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM assay_claims WHERE paper_id = ?", (paper_id,))
            self._conn.executemany(
                "INSERT INTO assay_claims (paper_id, uid, loc_line, quote, section, kind, load_bearing) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(paper_id, c.uid, c.loc_line, c.quote, c.section, c.kind, int(c.load_bearing)) for c in claims],
            )

    def store_assay_evidence(self, paper_id: str, evidence) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM assay_evidence WHERE paper_id = ?", (paper_id,))
            self._conn.executemany(
                "INSERT INTO assay_evidence (paper_id, uid, loc_line, quote, section, subtype, quality_tier, supports) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(paper_id, e.uid, e.loc_line, e.quote, e.section, e.subtype, e.quality_tier, e.supports) for e in evidence],
            )

    def store_assay_concessions(self, paper_id: str, concessions) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM assay_concessions WHERE paper_id = ?", (paper_id,))
            self._conn.executemany(
                "INSERT INTO assay_concessions (paper_id, uid, loc_line, quote, section, subtype) VALUES (?, ?, ?, ?, ?, ?)",
                [(paper_id, c.uid, c.loc_line, c.quote, c.section, c.subtype) for c in concessions],
            )

    def get_assay_concessions(self, paper_id: str) -> list:
        from paperstore.extract_rows import AssayConcessionRow
        rows = self._conn.execute(
            "SELECT paper_id, uid, loc_line, quote, section, subtype FROM assay_concessions WHERE paper_id = ?",
            (paper_id,),
        ).fetchall()
        return [AssayConcessionRow(*r) for r in rows]

    def store_assay_breadcrumbs(self, paper_id: str, breadcrumbs) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM assay_breadcrumbs WHERE paper_id = ?", (paper_id,))
            self._conn.executemany(
                "INSERT INTO assay_breadcrumbs (paper_id, uid, chunk_index, loc_line, gap, why_important, primary_lens, secondary_lens, severity) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(paper_id, b.uid, b.chunk_index, b.loc_line, b.gap, b.why_important, b.primary_lens, b.secondary_lens or "", b.severity) for b in breadcrumbs],
            )

    def store_assay_thesis(self, paper_id: str, thesis) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO assay_thesis (paper_id, central_claim, problem_statement, scope_boundary, ask_calibration) VALUES (?, ?, ?, ?, ?)",
                (paper_id, thesis.central_claim, thesis.problem_statement, thesis.scope_boundary, thesis.ask_calibration),
            )

    def store_assay_findings(self, paper_id: str, findings) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM assay_findings WHERE paper_id = ?", (paper_id,))
            self._conn.executemany(
                "INSERT INTO assay_findings (paper_id, uid, title, lens, severity, quote, loc_line, explanation, test, survived, major, challenge, reasoning) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(paper_id, f.uid, f.title, f.lens, f.severity, f.quote, f.loc_line, f.explanation, f.test, int(f.survived), int(f.major), getattr(f, 'challenge', ''), getattr(f, 'reasoning', '')) for f in findings],
            )

    def get_assay_claims(self, paper_id: str) -> list:
        from paperstore.extract_rows import AssayClaimRow
        rows = self._conn.execute(
            "SELECT paper_id, uid, loc_line, quote, section, kind, load_bearing FROM assay_claims WHERE paper_id = ?",
            (paper_id,),
        ).fetchall()
        return [AssayClaimRow(r[0], r[1], r[2], r[3], r[4], r[5], bool(r[6])) for r in rows]

    def get_assay_evidence(self, paper_id: str) -> list:
        from paperstore.extract_rows import AssayEvidenceRow
        rows = self._conn.execute(
            "SELECT paper_id, uid, loc_line, quote, section, subtype, quality_tier, supports FROM assay_evidence WHERE paper_id = ?",
            (paper_id,),
        ).fetchall()
        return [AssayEvidenceRow(*r) for r in rows]

    def get_assay_breadcrumbs(self, paper_id: str) -> list:
        from paperstore.extract_rows import AssayBreadcrumbRow
        rows = self._conn.execute(
            "SELECT paper_id, uid, chunk_index, loc_line, gap, why_important, primary_lens, secondary_lens, severity FROM assay_breadcrumbs WHERE paper_id = ?",
            (paper_id,),
        ).fetchall()
        return [AssayBreadcrumbRow(*r) for r in rows]

    def get_assay_thesis(self, paper_id: str):
        from paperstore.extract_rows import AssayThesisRow
        row = self._conn.execute(
            "SELECT paper_id, central_claim, problem_statement, scope_boundary, ask_calibration FROM assay_thesis WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
        return AssayThesisRow(*row) if row else None

    def get_assay_findings(self, paper_id: str) -> list:
        from paperstore.extract_rows import AssayFindingRow
        rows = self._conn.execute(
            "SELECT paper_id, uid, title, lens, severity, quote, loc_line, explanation, test, survived, major, challenge, reasoning FROM assay_findings WHERE paper_id = ?",
            (paper_id,),
        ).fetchall()
        return [AssayFindingRow(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], bool(r[9]), bool(r[10]), r[11], r[12]) for r in rows]

    def store_assay_asks(self, paper_id: str, asks) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM assay_asks WHERE paper_id = ?", (paper_id,))
            self._conn.executemany(
                "INSERT INTO assay_asks (paper_id, uid, target, quote, type) VALUES (?, ?, ?, ?, ?)",
                [(paper_id, i, a.get("target", ""), a.get("quote", ""), a.get("type", "")) for i, a in enumerate(asks, 1)],
            )

    def get_assay_asks(self, paper_id: str) -> list:
        from paperstore.extract_rows import AssayAskRow
        rows = self._conn.execute(
            "SELECT paper_id, uid, target, quote, type FROM assay_asks WHERE paper_id = ?",
            (paper_id,),
        ).fetchall()
        return [AssayAskRow(*r) for r in rows]

    def store_assay_references(self, paper_id: str, refs) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM assay_references WHERE paper_id = ?", (paper_id,))
            self._conn.executemany(
                "INSERT INTO assay_references (paper_id, uid, ref_label, relationship, url, mention_count) VALUES (?, ?, ?, ?, ?, ?)",
                [(paper_id, i, r.get("ref_label", r.get("ref_id", "")), r.get("relationship", "citation"), r.get("url", ""), r.get("mention_count", 0)) for i, r in enumerate(refs, 1)],
            )

    def get_assay_references(self, paper_id: str) -> list:
        from paperstore.extract_rows import AssayReferenceRow
        rows = self._conn.execute(
            "SELECT paper_id, uid, ref_label, relationship, url, mention_count FROM assay_references WHERE paper_id = ?",
            (paper_id,),
        ).fetchall()
        return [AssayReferenceRow(*r) for r in rows]

    def store_assay_strengths(self, paper_id: str, strengths) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM assay_strengths WHERE paper_id = ?", (paper_id,))
            self._conn.executemany(
                "INSERT INTO assay_strengths (paper_id, uid, title, quote, loc_line, explanation) VALUES (?, ?, ?, ?, ?, ?)",
                [(paper_id, i, s.get("title", ""), s.get("quote", ""), s.get("line", 0), s.get("explanation", "")) for i, s in enumerate(strengths, 1)],
            )

    def get_assay_strengths(self, paper_id: str) -> list:
        from paperstore.extract_rows import AssayStrengthRow
        rows = self._conn.execute(
            "SELECT paper_id, uid, title, quote, loc_line, explanation FROM assay_strengths WHERE paper_id = ?",
            (paper_id,),
        ).fetchall()
        return [AssayStrengthRow(*r) for r in rows]

    def store_assay_checklist(self, paper_id: str, items) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM assay_checklist WHERE paper_id = ?", (paper_id,))
            self._conn.executemany(
                "INSERT INTO assay_checklist (paper_id, item_id, name, passed, location, note) VALUES (?, ?, ?, ?, ?, ?)",
                [(paper_id, c.get("id", ""), c.get("name", ""), int(c.get("passed", False)), c.get("location", "") or "", c.get("note", "") or "") for c in items],
            )

    def get_assay_checklist(self, paper_id: str) -> list:
        from paperstore.extract_rows import AssayChecklistRow
        rows = self._conn.execute(
            "SELECT paper_id, item_id, name, passed, location, note FROM assay_checklist WHERE paper_id = ?",
            (paper_id,),
        ).fetchall()
        return [AssayChecklistRow(r[0], r[1], r[2], bool(r[3]), r[4], r[5]) for r in rows]

    def store_assay_compounds(self, paper_id: str, compounds) -> None:
        import json
        with self._conn:
            self._conn.execute("DELETE FROM assay_compounds WHERE paper_id = ?", (paper_id,))
            self._conn.executemany(
                "INSERT INTO assay_compounds (paper_id, uid, name, constituents, mechanism, cross_lens, emergent_risk) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(paper_id, i, c.get("name", ""), json.dumps(c.get("constituents", [])), c.get("mechanism", ""), int(c.get("cross_lens", False)), c.get("emergent_risk", "") or "") for i, c in enumerate(compounds, 1)],
            )

    def get_assay_compounds(self, paper_id: str) -> list:
        import json
        from paperstore.extract_rows import AssayCompoundRow
        rows = self._conn.execute(
            "SELECT paper_id, uid, name, constituents, mechanism, cross_lens, emergent_risk FROM assay_compounds WHERE paper_id = ?",
            (paper_id,),
        ).fetchall()
        return [AssayCompoundRow(r[0], r[1], r[2], json.loads(r[3]), r[4], bool(r[5]), r[6]) for r in rows]

    def store_assay_synthesis(self, paper_id: str, synthesis) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO assay_synthesis (paper_id, verdict, verdict_confidence, thesis_statement, thesis_survives, central_thesis, dominant_dynamic, critical_count, significant_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (paper_id, synthesis.get("verdict", "Insufficient"), synthesis.get("verdict_confidence", "Medium"), synthesis.get("thesis_statement", ""), int(synthesis.get("thesis_survives", False)), synthesis.get("central_thesis", ""), synthesis.get("dominant_dynamic", "") or "", synthesis.get("critical_count", 0), synthesis.get("significant_count", 0)),
            )

    def get_assay_synthesis(self, paper_id: str):
        from paperstore.extract_rows import AssaySynthesisRow
        row = self._conn.execute(
            "SELECT paper_id, verdict, verdict_confidence, thesis_statement, thesis_survives, central_thesis, dominant_dynamic, critical_count, significant_count FROM assay_synthesis WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
        return AssaySynthesisRow(row[0], row[1], row[2], row[3], bool(row[4]), row[5], row[6], row[7], row[8]) if row else None

    def write_assay_md(self, paper_id: str, markdown: str) -> Path:
        pid = paper_id.strip().upper().lower()
        path = self._papers_dir / f"{pid}.assay.md"
        partial = path.with_suffix(".partial")
        partial.write_text(markdown, encoding="utf-8")
        bak = path.with_suffix(".bak.md")
        if path.exists():
            bak.unlink(missing_ok=True)
            path.rename(bak)
        _atomic_replace(partial, path)
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO papers (paper_id) VALUES (?)", (paper_id,)
            )
            self._conn.execute(
                "UPDATE papers SET assay_path = ? WHERE paper_id = ?",
                (str(path), paper_id),
            )
        return path

    def clear_assay(self, paper_id: str) -> None:
        meta = self._conn.execute(
            "SELECT assay_path FROM papers WHERE paper_id = ?", (paper_id,)
        ).fetchone()
        if meta and meta[0]:
            p = Path(meta[0])
            p.unlink(missing_ok=True)
        with self._conn:
            self._conn.execute(
                "UPDATE papers SET assay_path = '' WHERE paper_id = ?", (paper_id,)
            )
            for table in _ASSAY_TABLES:
                self._conn.execute(f"DELETE FROM {table} WHERE paper_id = ?", (paper_id,))
