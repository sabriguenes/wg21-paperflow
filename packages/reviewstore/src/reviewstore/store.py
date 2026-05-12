#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""ReviewStore: SQLite storage for review pipeline results."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from reviewstore.models import (
    ClaimRow,
    EvidenceRow,
    ExternalCitationRow,
    PaperCitationRow,
    QuestionRow,
)

_SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    paper_id         TEXT NOT NULL,
    loc_line         INTEGER NOT NULL,
    loc_start        INTEGER NOT NULL,
    loc_end          INTEGER NOT NULL,
    text             TEXT NOT NULL,
    section          TEXT DEFAULT '',
    question         TEXT DEFAULT '',
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
    claim_text       TEXT NOT NULL,
    section          TEXT DEFAULT '',
    question         TEXT NOT NULL,
    PRIMARY KEY (paper_id, claim_text)
);
"""

_QUESTIONS_DDL = (
    "CREATE TABLE IF NOT EXISTS questions ("
    "  paper_id TEXT NOT NULL,"
    "  claim_text TEXT NOT NULL,"
    "  section TEXT DEFAULT '',"
    "  question TEXT NOT NULL,"
    "  PRIMARY KEY (paper_id, claim_text)"
    ");"
)


def _normalize_pid(paper_id: str) -> str:
    return paper_id.strip().upper()


def _merged_triple(merged_into: object) -> tuple[int | None, int | None, int | None]:
    if merged_into is not None:
        return merged_into.line, merged_into.start_char, merged_into.end_char  # type: ignore[union-attr]
    return None, None, None


class ReviewStore:
    """SQLite-backed store for review pipeline results.

    One process at a time. No concurrent access concerns.
    """

    def __init__(self, workspace_dir: Path) -> None:
        self._workspace = Path(workspace_dir)
        self._workspace.mkdir(parents=True, exist_ok=True)
        db_path = self._workspace / "reviewstore.db"
        self._conn: sqlite3.Connection | None = sqlite3.connect(
            str(db_path), check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._check_version()
        self._conn.commit()

    @classmethod
    def from_env(cls) -> "ReviewStore":
        """Construct from ``$WG21_DATA_DIR``."""
        workspace = os.environ.get("WG21_DATA_DIR", "")
        if not workspace:
            raise EnvironmentError(
                "WG21_DATA_DIR environment variable is not set. "
                "Set it to the workspace directory."
            )
        return cls(Path(workspace))

    @property
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("ReviewStore is closed")
        return self._conn

    def _check_version(self) -> None:
        ver = self._db.execute("PRAGMA user_version").fetchone()[0]
        if ver == 0:
            self._db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        elif ver == 1:
            self._db.executescript(_QUESTIONS_DDL)
            self._db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        elif ver > _SCHEMA_VERSION:
            raise RuntimeError(
                f"reviewstore.db has schema version {ver}, but this tool "
                f"only supports version {_SCHEMA_VERSION}. Update your tools."
            )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "ReviewStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- Write API (delete + insert, atomic) ---------------------------------

    def store_claims(self, paper_id: str, claims) -> None:
        """Replace all claims for paper_id. Accepts review.models.Claim list."""
        pid = _normalize_pid(paper_id)
        rows = []
        for c in claims:
            ml, ms, me = _merged_triple(c.merged_into)
            rows.append((
                pid, c.loc.line, c.loc.start_char, c.loc.end_char,
                c.text, c.section, c.question, ml, ms, me,
            ))
        with self._db:
            self._db.execute("DELETE FROM claims WHERE paper_id = ?", (pid,))
            self._db.executemany(
                "INSERT INTO claims (paper_id, loc_line, loc_start, loc_end, "
                "text, section, question, merged_into_line, merged_into_start, "
                "merged_into_end) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def store_evidence(self, paper_id: str, evidence) -> None:
        """Replace all evidence for paper_id. Accepts review.models.Evidence list."""
        pid = _normalize_pid(paper_id)
        rows = []
        for e in evidence:
            ml, ms, me = _merged_triple(e.merged_into)
            rows.append((
                pid, e.loc.line, e.loc.start_char, e.loc.end_char,
                e.text, e.section, json.dumps(e.supports),
                int(e.quantitative), int(e.cited),
                int(e.verifiable), int(e.normative), ml, ms, me,
            ))
        with self._db:
            self._db.execute("DELETE FROM evidence WHERE paper_id = ?", (pid,))
            self._db.executemany(
                "INSERT INTO evidence (paper_id, loc_line, loc_start, loc_end, "
                "text, section, supports, quantitative, cited, verifiable, "
                "normative, merged_into_line, merged_into_start, merged_into_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def store_paper_citations(self, paper_id: str, citations) -> None:
        """Replace paper citations. Accepts review.models.CitationRef list."""
        pid = _normalize_pid(paper_id)
        rows = [(pid, c.paper_id, c.count) for c in citations]
        with self._db:
            self._db.execute("DELETE FROM paper_citations WHERE paper_id = ?", (pid,))
            self._db.executemany(
                "INSERT INTO paper_citations (paper_id, cited_paper_id, count) "
                "VALUES (?, ?, ?)",
                rows,
            )

    def store_external_citations(self, paper_id: str, externals) -> None:
        """Replace external citations. Accepts review.models.ExternalEvidence list."""
        pid = _normalize_pid(paper_id)
        rows = [
            (pid, ex.source_url, ex.source_title, ex.text, ex.finding, ex.stance)
            for ex in externals
        ]
        with self._db:
            self._db.execute("DELETE FROM external_citations WHERE paper_id = ?", (pid,))
            self._db.executemany(
                "INSERT INTO external_citations (paper_id, source_url, "
                "source_title, text, finding, stance) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )

    def store_questions(self, paper_id: str, claims, support_map) -> None:
        """Store questions for unsupported claims.

        Accepts review.models.Claim list and review.models.SupportLink list.
        Only non-merged claims with status 'unsupported' are stored.
        """
        pid = _normalize_pid(paper_id)
        unsupported_locs = {
            s.claim_loc for s in support_map if s.status == "unsupported"
        }
        rows = [
            (pid, c.text, c.section, c.question)
            for c in claims
            if c.merged_into is None and c.loc in unsupported_locs
        ]
        with self._db:
            self._db.execute("DELETE FROM questions WHERE paper_id = ?", (pid,))
            self._db.executemany(
                "INSERT INTO questions (paper_id, claim_text, section, question) "
                "VALUES (?, ?, ?, ?)",
                rows,
            )

    # -- Read API ------------------------------------------------------------

    def get_claims(self, paper_id: str) -> list[ClaimRow]:
        pid = _normalize_pid(paper_id)
        rows = self._db.execute(
            "SELECT * FROM claims WHERE paper_id = ?", (pid,)
        ).fetchall()
        return [ClaimRow(
            paper_id=r["paper_id"], loc_line=r["loc_line"],
            loc_start=r["loc_start"], loc_end=r["loc_end"],
            text=r["text"], section=r["section"], question=r["question"],
            merged_into_line=r["merged_into_line"],
            merged_into_start=r["merged_into_start"],
            merged_into_end=r["merged_into_end"],
        ) for r in rows]

    def get_evidence(self, paper_id: str) -> list[EvidenceRow]:
        pid = _normalize_pid(paper_id)
        rows = self._db.execute(
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
        pid = _normalize_pid(paper_id)
        rows = self._db.execute(
            "SELECT * FROM paper_citations WHERE paper_id = ? ORDER BY count DESC",
            (pid,),
        ).fetchall()
        return [PaperCitationRow(
            paper_id=r["paper_id"], cited_paper_id=r["cited_paper_id"],
            count=r["count"],
        ) for r in rows]

    def get_external_citations(self, paper_id: str) -> list[ExternalCitationRow]:
        pid = _normalize_pid(paper_id)
        rows = self._db.execute(
            "SELECT * FROM external_citations WHERE paper_id = ?", (pid,)
        ).fetchall()
        return [ExternalCitationRow(
            paper_id=r["paper_id"], source_url=r["source_url"],
            source_title=r["source_title"], text=r["text"],
            finding=r["finding"], stance=r["stance"],
        ) for r in rows]

    def get_questions(self, paper_id: str) -> list[QuestionRow]:
        pid = _normalize_pid(paper_id)
        rows = self._db.execute(
            "SELECT * FROM questions WHERE paper_id = ?", (pid,)
        ).fetchall()
        return [QuestionRow(
            paper_id=r["paper_id"], claim_text=r["claim_text"],
            section=r["section"], question=r["question"],
        ) for r in rows]
