#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""SQLite + FTS5 implementation of :class:`~cpp_mcp.backend.StandardBackend`."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cpp_mcp.backend import DraftInfo, SectionRow, StandardBackend

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS standard_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_tag TEXT NOT NULL,
    stable_label TEXT NOT NULL,
    section_number TEXT,
    title TEXT NOT NULL,
    depth INTEGER NOT NULL,
    parent_label TEXT,
    chapter_file TEXT NOT NULL,
    raw_latex TEXT NOT NULL,
    cleaned_text TEXT NOT NULL,
    paragraph_count INTEGER,
    UNIQUE(draft_tag, stable_label)
);

CREATE INDEX IF NOT EXISTS idx_sections_label
    ON standard_sections(draft_tag, stable_label);
CREATE INDEX IF NOT EXISTS idx_sections_parent
    ON standard_sections(draft_tag, parent_label);

CREATE TABLE IF NOT EXISTS drafts (
    draft_tag TEXT PRIMARY KEY,
    ingested_at TEXT NOT NULL,
    section_count INTEGER NOT NULL,
    git_sha TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS sections_fts USING fts5(
    draft_tag, stable_label, title, cleaned_text,
    content='standard_sections', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS sections_ai AFTER INSERT ON standard_sections BEGIN
    INSERT INTO sections_fts(rowid, draft_tag, stable_label, title, cleaned_text)
    VALUES (new.id, new.draft_tag, new.stable_label, new.title, new.cleaned_text);
END;

CREATE TRIGGER IF NOT EXISTS sections_ad AFTER DELETE ON standard_sections BEGIN
    INSERT INTO sections_fts(sections_fts, rowid, draft_tag, stable_label, title, cleaned_text)
    VALUES ('delete', old.id, old.draft_tag, old.stable_label, old.title, old.cleaned_text);
END;

CREATE TRIGGER IF NOT EXISTS sections_au AFTER UPDATE ON standard_sections BEGIN
    INSERT INTO sections_fts(sections_fts, rowid, draft_tag, stable_label, title, cleaned_text)
    VALUES ('delete', old.id, old.draft_tag, old.stable_label, old.title, old.cleaned_text);
    INSERT INTO sections_fts(rowid, draft_tag, stable_label, title, cleaned_text)
    VALUES (new.id, new.draft_tag, new.stable_label, new.title, new.cleaned_text);
END;
"""


def _row_to_section(row: sqlite3.Row) -> SectionRow:
    return SectionRow(
        draft_tag=row["draft_tag"],
        stable_label=row["stable_label"],
        section_number=row["section_number"],
        title=row["title"],
        depth=row["depth"],
        parent_label=row["parent_label"],
        chapter_file=row["chapter_file"],
        raw_latex=row["raw_latex"],
        cleaned_text=row["cleaned_text"],
        paragraph_count=row["paragraph_count"] or 0,
    )


class SqliteStandardBackend(StandardBackend):
    """SQLite + FTS5 backend for C++ standard sections.

    The database lives at *db_path*. Pass ``":memory:"`` for tests.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                self._db_path, check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def create_schema(self) -> None:
        self.conn.executescript(_SCHEMA_SQL)

    def _resolve_draft(self, draft_tag: str | None) -> str | None:
        if draft_tag is not None:
            return draft_tag
        return self.default_draft_tag()

    def upsert_draft(
        self,
        draft_tag: str,
        sections: list[SectionRow],
        git_sha: str | None = None,
    ) -> None:
        conn = self.conn
        with conn:
            conn.execute(
                "DELETE FROM standard_sections WHERE draft_tag = ?",
                (draft_tag,),
            )
            conn.executemany(
                """\
                INSERT INTO standard_sections
                    (draft_tag, stable_label, section_number, title, depth,
                     parent_label, chapter_file, raw_latex, cleaned_text,
                     paragraph_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        s.draft_tag,
                        s.stable_label,
                        s.section_number,
                        s.title,
                        s.depth,
                        s.parent_label,
                        s.chapter_file,
                        s.raw_latex,
                        s.cleaned_text,
                        s.paragraph_count,
                    )
                    for s in sections
                ],
            )
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """\
                INSERT OR REPLACE INTO drafts (draft_tag, ingested_at, section_count, git_sha)
                VALUES (?, ?, ?, ?)
                """,
                (draft_tag, now, len(sections), git_sha),
            )

    def lookup_section(
        self, stable_label: str, draft_tag: str | None = None
    ) -> SectionRow | None:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return None
        row = self.conn.execute(
            "SELECT * FROM standard_sections WHERE draft_tag = ? AND stable_label = ?",
            (tag, stable_label),
        ).fetchone()
        return _row_to_section(row) if row else None

    def get_section_with_children(
        self, stable_label: str, draft_tag: str | None = None
    ) -> list[SectionRow]:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return []
        root = self.lookup_section(stable_label, tag)
        if root is None:
            return []

        result = [root]
        queue = [stable_label]
        while queue:
            parent = queue.pop(0)
            children = self.conn.execute(
                "SELECT * FROM standard_sections WHERE draft_tag = ? AND parent_label = ? ORDER BY id",
                (tag, parent),
            ).fetchall()
            for row in children:
                section = _row_to_section(row)
                result.append(section)
                queue.append(section.stable_label)
        return result

    def list_chapters(self, draft_tag: str | None = None) -> list[SectionRow]:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return []
        rows = self.conn.execute(
            "SELECT * FROM standard_sections WHERE draft_tag = ? AND depth = 0 ORDER BY id",
            (tag,),
        ).fetchall()
        return [_row_to_section(r) for r in rows]

    def list_sections(
        self,
        chapter: str | None = None,
        depth: int | None = None,
        draft_tag: str | None = None,
    ) -> list[SectionRow]:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return []
        clauses = ["draft_tag = ?"]
        params: list[object] = [tag]
        if chapter is not None:
            clauses.append("chapter_file = ?")
            params.append(chapter)
        if depth is not None:
            clauses.append("depth = ?")
            params.append(depth)
        where = " AND ".join(clauses)
        rows = self.conn.execute(
            f"SELECT * FROM standard_sections WHERE {where} ORDER BY id",
            params,
        ).fetchall()
        return [_row_to_section(r) for r in rows]

    def search(
        self,
        query: str,
        top_k: int = 10,
        chapter: str | None = None,
        draft_tag: str | None = None,
    ) -> list[SectionRow]:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return []

        # FTS5 query with draft_tag filter via a JOIN predicate rather than
        # MATCH syntax, because draft tags often contain hyphens which FTS5
        # parses as negation operators.
        sql = """\
            SELECT s.* FROM sections_fts f
            JOIN standard_sections s ON f.rowid = s.id
            WHERE sections_fts MATCH ?
              AND s.draft_tag = ?
            ORDER BY bm25(sections_fts)
            LIMIT ?
        """
        params: list[object] = [query, tag, top_k]

        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []

        results = [_row_to_section(r) for r in rows]
        if chapter is not None:
            results = [r for r in results if r.chapter_file == chapter]
        return results

    def list_drafts(self) -> list[DraftInfo]:
        rows = self.conn.execute(
            "SELECT * FROM drafts ORDER BY ingested_at DESC"
        ).fetchall()
        return [
            DraftInfo(
                draft_tag=r["draft_tag"],
                ingested_at=r["ingested_at"],
                section_count=r["section_count"],
                git_sha=r["git_sha"],
            )
            for r in rows
        ]

    def diff_section(
        self,
        stable_label: str,
        from_draft: str,
        to_draft: str,
    ) -> tuple[SectionRow | None, SectionRow | None]:
        return (
            self.lookup_section(stable_label, from_draft),
            self.lookup_section(stable_label, to_draft),
        )

    def default_draft_tag(self) -> str | None:
        row = self.conn.execute(
            "SELECT draft_tag FROM drafts ORDER BY ingested_at DESC LIMIT 1"
        ).fetchone()
        return row["draft_tag"] if row else None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> SqliteStandardBackend:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
