#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""SQLite + FTS5 implementation of :class:`~cpp_mcp.backend.StandardBackend`."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cpp_mcp.backend import (
    DefinedTermRow,
    DraftInfo,
    GrammarRuleRow,
    IndexTermRow,
    LibraryDeclRow,
    MechanismRow,
    ParagraphRow,
    SectionRow,
    StandardBackend,
)

log = logging.getLogger(__name__)

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
    is_deprecated INTEGER DEFAULT 0,
    is_synopsis INTEGER DEFAULT 0,
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
    git_sha TEXT,
    standard_version TEXT,
    version_note TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS sections_fts USING fts5(
    draft_tag, stable_label, title, cleaned_text,
    content='standard_sections', content_rowid='id',
    tokenize="unicode61 tokenchars ':_<>'"
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

CREATE TABLE IF NOT EXISTS section_xrefs (
    draft_tag TEXT NOT NULL,
    from_label TEXT NOT NULL,
    to_label TEXT NOT NULL,
    PRIMARY KEY (draft_tag, from_label, to_label)
);
CREATE INDEX IF NOT EXISTS idx_xrefs_to ON section_xrefs(draft_tag, to_label);

CREATE TABLE IF NOT EXISTS section_index_terms (
    draft_tag TEXT NOT NULL,
    stable_label TEXT NOT NULL,
    category TEXT NOT NULL,
    term TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_index_terms ON section_index_terms(draft_tag, term);
CREATE INDEX IF NOT EXISTS idx_index_terms_cat ON section_index_terms(draft_tag, category, term);

CREATE TABLE IF NOT EXISTS mechanisms (
    draft_tag TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    stable_label TEXT NOT NULL,
    UNIQUE(draft_tag, name, category, stable_label)
);
CREATE INDEX IF NOT EXISTS idx_mechanisms_name ON mechanisms(draft_tag, name);
CREATE INDEX IF NOT EXISTS idx_mechanisms_cat ON mechanisms(draft_tag, category);

CREATE TABLE IF NOT EXISTS grammar_rules (
    draft_tag TEXT NOT NULL,
    nonterminal TEXT NOT NULL,
    stable_label TEXT NOT NULL,
    raw_rule TEXT NOT NULL,
    PRIMARY KEY (draft_tag, nonterminal)
);

CREATE TABLE IF NOT EXISTS defined_terms (
    draft_tag TEXT NOT NULL,
    term TEXT NOT NULL,
    stable_label TEXT NOT NULL,
    definition_text TEXT NOT NULL,
    PRIMARY KEY (draft_tag, term, stable_label)
);

CREATE TABLE IF NOT EXISTS library_declarations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_tag TEXT NOT NULL,
    stable_label TEXT NOT NULL,
    declaration TEXT NOT NULL,
    description TEXT NOT NULL,
    preconditions TEXT,
    effects TEXT,
    postconditions TEXT,
    returns TEXT,
    throws TEXT,
    mandates TEXT,
    constraints TEXT,
    complexity TEXT,
    remarks TEXT
);
CREATE INDEX IF NOT EXISTS idx_libdecl_section ON library_declarations(draft_tag, stable_label);

CREATE TABLE IF NOT EXISTS section_paragraphs (
    draft_tag TEXT NOT NULL,
    stable_label TEXT NOT NULL,
    paragraph_number INTEGER NOT NULL,
    raw_latex TEXT NOT NULL,
    cleaned_text TEXT NOT NULL,
    normative_force TEXT,
    PRIMARY KEY (draft_tag, stable_label, paragraph_number)
);
"""

# Tables that carry per-draft data and must be cleaned during
# atomic_replace_draft and upsert operations.
_DRAFT_TABLES = (
    "standard_sections",
    "section_xrefs",
    "section_index_terms",
    "mechanisms",
    "grammar_rules",
    "defined_terms",
    "library_declarations",
    "section_paragraphs",
)


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
        is_deprecated=bool(row["is_deprecated"]),
        is_synopsis=bool(row["is_synopsis"]),
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

    # ------------------------------------------------------------------
    # Draft ingestion
    # ------------------------------------------------------------------

    def upsert_draft(
        self,
        draft_tag: str,
        sections: list[SectionRow],
        git_sha: str | None = None,
        standard_version: str | None = None,
        version_note: str | None = None,
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
                     paragraph_count, is_deprecated, is_synopsis)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        int(s.is_deprecated),
                        int(s.is_synopsis),
                    )
                    for s in sections
                ],
            )
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """\
                INSERT OR REPLACE INTO drafts
                    (draft_tag, ingested_at, section_count, git_sha,
                     standard_version, version_note)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (draft_tag, now, len(sections), git_sha,
                 standard_version, version_note),
            )

    def upsert_xrefs(
        self, draft_tag: str, xrefs: list[tuple[str, str]]
    ) -> None:
        conn = self.conn
        with conn:
            conn.execute(
                "DELETE FROM section_xrefs WHERE draft_tag = ?",
                (draft_tag,),
            )
            conn.executemany(
                "INSERT INTO section_xrefs (draft_tag, from_label, to_label) "
                "VALUES (?, ?, ?)",
                [(draft_tag, from_l, to_l) for from_l, to_l in xrefs],
            )

    def upsert_index_terms(
        self, draft_tag: str, terms: list[IndexTermRow]
    ) -> None:
        conn = self.conn
        with conn:
            conn.execute(
                "DELETE FROM section_index_terms WHERE draft_tag = ?",
                (draft_tag,),
            )
            conn.executemany(
                "INSERT INTO section_index_terms "
                "(draft_tag, stable_label, category, term) "
                "VALUES (?, ?, ?, ?)",
                [
                    (t.draft_tag, t.stable_label, t.category, t.term)
                    for t in terms
                ],
            )

    def upsert_mechanisms(
        self, draft_tag: str, mechanisms: list[MechanismRow]
    ) -> None:
        conn = self.conn
        with conn:
            conn.execute(
                "DELETE FROM mechanisms WHERE draft_tag = ?",
                (draft_tag,),
            )
            conn.executemany(
                "INSERT INTO mechanisms "
                "(draft_tag, name, category, stable_label) "
                "VALUES (?, ?, ?, ?)",
                [
                    (m.draft_tag, m.name, m.category, m.stable_label)
                    for m in mechanisms
                ],
            )

    def upsert_grammar_rules(
        self, draft_tag: str, rules: list[GrammarRuleRow]
    ) -> None:
        conn = self.conn
        with conn:
            conn.execute(
                "DELETE FROM grammar_rules WHERE draft_tag = ?",
                (draft_tag,),
            )
            conn.executemany(
                "INSERT INTO grammar_rules "
                "(draft_tag, nonterminal, stable_label, raw_rule) "
                "VALUES (?, ?, ?, ?)",
                [
                    (r.draft_tag, r.nonterminal, r.stable_label, r.raw_rule)
                    for r in rules
                ],
            )

    def upsert_defined_terms(
        self, draft_tag: str, terms: list[DefinedTermRow]
    ) -> None:
        conn = self.conn
        with conn:
            conn.execute(
                "DELETE FROM defined_terms WHERE draft_tag = ?",
                (draft_tag,),
            )
            conn.executemany(
                "INSERT INTO defined_terms "
                "(draft_tag, term, stable_label, definition_text) "
                "VALUES (?, ?, ?, ?)",
                [
                    (t.draft_tag, t.term, t.stable_label, t.definition_text)
                    for t in terms
                ],
            )

    def upsert_library_declarations(
        self, draft_tag: str, decls: list[LibraryDeclRow]
    ) -> None:
        conn = self.conn
        with conn:
            conn.execute(
                "DELETE FROM library_declarations WHERE draft_tag = ?",
                (draft_tag,),
            )
            conn.executemany(
                """\
                INSERT INTO library_declarations
                    (draft_tag, stable_label, declaration, description,
                     preconditions, effects, postconditions, returns,
                     throws, mandates, constraints, complexity, remarks)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        d.draft_tag,
                        d.stable_label,
                        d.declaration,
                        d.description,
                        d.preconditions,
                        d.effects,
                        d.postconditions,
                        d.returns,
                        d.throws,
                        d.mandates,
                        d.constraints,
                        d.complexity,
                        d.remarks,
                    )
                    for d in decls
                ],
            )

    def upsert_paragraphs(
        self, draft_tag: str, paragraphs: list[ParagraphRow]
    ) -> None:
        conn = self.conn
        with conn:
            conn.execute(
                "DELETE FROM section_paragraphs WHERE draft_tag = ?",
                (draft_tag,),
            )
            conn.executemany(
                """\
                INSERT INTO section_paragraphs
                    (draft_tag, stable_label, paragraph_number,
                     raw_latex, cleaned_text, normative_force)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        p.draft_tag,
                        p.stable_label,
                        p.paragraph_number,
                        p.raw_latex,
                        p.cleaned_text,
                        p.normative_force,
                    )
                    for p in paragraphs
                ],
            )

    def atomic_replace_draft(
        self, staging_tag: str, real_tag: str
    ) -> None:
        conn = self.conn
        with conn:
            for table in _DRAFT_TABLES:
                conn.execute(
                    f"DELETE FROM {table} WHERE draft_tag = ?",
                    (real_tag,),
                )
                conn.execute(
                    f"UPDATE {table} SET draft_tag = ? WHERE draft_tag = ?",
                    (real_tag, staging_tag),
                )
            conn.execute(
                "DELETE FROM drafts WHERE draft_tag = ?", (real_tag,)
            )
            conn.execute(
                "UPDATE drafts SET draft_tag = ? WHERE draft_tag = ?",
                (real_tag, staging_tag),
            )

    # ------------------------------------------------------------------
    # Section queries
    # ------------------------------------------------------------------

    def lookup_section(
        self, stable_label: str, draft_tag: str | None = None
    ) -> SectionRow | None:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return None
        row = self.conn.execute(
            "SELECT * FROM standard_sections "
            "WHERE draft_tag = ? AND stable_label = ?",
            (tag, stable_label),
        ).fetchone()
        return _row_to_section(row) if row else None

    def lookup_sections(
        self, labels: list[str], draft_tag: str | None = None
    ) -> list[SectionRow]:
        tag = self._resolve_draft(draft_tag)
        if tag is None or not labels:
            return []
        placeholders = ",".join("?" for _ in labels)
        rows = self.conn.execute(
            f"SELECT * FROM standard_sections "
            f"WHERE draft_tag = ? AND stable_label IN ({placeholders})",
            [tag, *labels],
        ).fetchall()
        row_map = {r["stable_label"]: _row_to_section(r) for r in rows}
        return [row_map[lbl] for lbl in labels if lbl in row_map]

    def get_section_with_children(
        self, stable_label: str, draft_tag: str | None = None
    ) -> list[SectionRow]:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return []
        root = self.lookup_section(stable_label, tag)
        if root is None:
            return []

        from collections import deque as _deque
        result = [root]
        queue = _deque([stable_label])
        while queue:
            parent = queue.popleft()
            children = self.conn.execute(
                "SELECT * FROM standard_sections "
                "WHERE draft_tag = ? AND parent_label = ? ORDER BY id",
                (tag, parent),
            ).fetchall()
            for row in children:
                section = _row_to_section(row)
                result.append(section)
                queue.append(section.stable_label)
        return result

    def get_ancestors(
        self, stable_label: str, draft_tag: str | None = None
    ) -> list[SectionRow]:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return []
        ancestors: list[SectionRow] = []
        current = self.lookup_section(stable_label, tag)
        if current is None:
            return []
        label = current.parent_label
        while label is not None:
            parent = self.lookup_section(label, tag)
            if parent is None:
                break
            ancestors.append(parent)
            label = parent.parent_label
        ancestors.reverse()
        return ancestors

    def list_chapters(self, draft_tag: str | None = None) -> list[SectionRow]:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return []
        rows = self.conn.execute(
            "SELECT * FROM standard_sections "
            "WHERE draft_tag = ? AND depth = 0 ORDER BY id",
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

    # ------------------------------------------------------------------
    # Full-text search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 10,
        chapter: str | None = None,
        draft_tag: str | None = None,
        snippet: bool = False,
    ) -> list[SectionRow]:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return []

        clauses = ["sections_fts MATCH ?", "s.draft_tag = ?"]
        params: list[object] = [query, tag]

        if chapter is not None:
            clauses.append("s.chapter_file = ?")
            params.append(chapter)

        where = " AND ".join(clauses)
        params.append(top_k)

        if snippet:
            sql = (
                f"SELECT s.*, "
                f"snippet(sections_fts, 3, '<b>', '</b>', '...', 64) "
                f"AS snippet_text "
                f"FROM sections_fts f "
                f"JOIN standard_sections s ON f.rowid = s.id "
                f"WHERE {where} "
                f"ORDER BY bm25(sections_fts, 0, 10.0, 5.0, 1.0) "
                f"LIMIT ?"
            )
        else:
            sql = (
                f"SELECT s.* FROM sections_fts f "
                f"JOIN standard_sections s ON f.rowid = s.id "
                f"WHERE {where} "
                f"ORDER BY bm25(sections_fts, 0, 10.0, 5.0, 1.0) "
                f"LIMIT ?"
            )

        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            log.warning("FTS5 search failed for query %r: %s", query, exc)
            return []

        return [_row_to_section(r) for r in rows]

    # ------------------------------------------------------------------
    # Cross-references
    # ------------------------------------------------------------------

    def get_references_from(
        self, label: str, draft_tag: str | None = None
    ) -> list[str]:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return []
        rows = self.conn.execute(
            "SELECT to_label FROM section_xrefs "
            "WHERE draft_tag = ? AND from_label = ?",
            (tag, label),
        ).fetchall()
        return [r["to_label"] for r in rows]

    def get_references_to(
        self, label: str, draft_tag: str | None = None
    ) -> list[str]:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return []
        rows = self.conn.execute(
            "SELECT from_label FROM section_xrefs "
            "WHERE draft_tag = ? AND to_label = ?",
            (tag, label),
        ).fetchall()
        return [r["from_label"] for r in rows]

    # ------------------------------------------------------------------
    # Index, mechanisms, grammar, definitions
    # ------------------------------------------------------------------

    def search_index(
        self,
        term: str,
        category: str | None = None,
        draft_tag: str | None = None,
    ) -> list[IndexTermRow]:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return []
        clauses = ["draft_tag = ?", "term LIKE ?"]
        params: list[object] = [tag, f"%{term}%"]
        if category is not None:
            clauses.append("category = ?")
            params.append(category)
        where = " AND ".join(clauses)
        rows = self.conn.execute(
            f"SELECT * FROM section_index_terms WHERE {where}",
            params,
        ).fetchall()
        return [
            IndexTermRow(
                draft_tag=r["draft_tag"],
                stable_label=r["stable_label"],
                category=r["category"],
                term=r["term"],
            )
            for r in rows
        ]

    def verify_mechanism(
        self, name: str, draft_tag: str | None = None
    ) -> list[MechanismRow]:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return []
        rows = self.conn.execute(
            "SELECT * FROM mechanisms "
            "WHERE draft_tag = ? AND name LIKE ?",
            (tag, f"%{name}%"),
        ).fetchall()
        return [
            MechanismRow(
                draft_tag=r["draft_tag"],
                name=r["name"],
                category=r["category"],
                stable_label=r["stable_label"],
            )
            for r in rows
        ]

    def search_grammar(
        self, nonterminal: str, draft_tag: str | None = None
    ) -> GrammarRuleRow | None:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return None
        row = self.conn.execute(
            "SELECT * FROM grammar_rules "
            "WHERE draft_tag = ? AND nonterminal = ?",
            (tag, nonterminal),
        ).fetchone()
        if row is None:
            return None
        return GrammarRuleRow(
            draft_tag=row["draft_tag"],
            nonterminal=row["nonterminal"],
            stable_label=row["stable_label"],
            raw_rule=row["raw_rule"],
        )

    def lookup_definition(
        self, term: str, draft_tag: str | None = None
    ) -> list[DefinedTermRow]:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return []
        rows = self.conn.execute(
            "SELECT * FROM defined_terms WHERE draft_tag = ? AND term = ? ORDER BY rowid",
            (tag, term),
        ).fetchall()
        return [
            DefinedTermRow(
                draft_tag=r["draft_tag"],
                term=r["term"],
                stable_label=r["stable_label"],
                definition_text=r["definition_text"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Library declarations
    # ------------------------------------------------------------------

    def lookup_declarations(
        self, pattern: str, draft_tag: str | None = None
    ) -> list[LibraryDeclRow]:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return []
        rows = self.conn.execute(
            "SELECT * FROM library_declarations "
            "WHERE draft_tag = ? AND declaration LIKE ?",
            (tag, f"%{pattern}%"),
        ).fetchall()
        return [
            LibraryDeclRow(
                draft_tag=r["draft_tag"],
                stable_label=r["stable_label"],
                declaration=r["declaration"],
                description=r["description"],
                preconditions=r["preconditions"],
                effects=r["effects"],
                postconditions=r["postconditions"],
                returns=r["returns"],
                throws=r["throws"],
                mandates=r["mandates"],
                constraints=r["constraints"],
                complexity=r["complexity"],
                remarks=r["remarks"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Paragraphs
    # ------------------------------------------------------------------

    def lookup_paragraph(
        self,
        stable_label: str,
        paragraph_number: int,
        draft_tag: str | None = None,
    ) -> ParagraphRow | None:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return None
        row = self.conn.execute(
            "SELECT * FROM section_paragraphs "
            "WHERE draft_tag = ? AND stable_label = ? "
            "AND paragraph_number = ?",
            (tag, stable_label, paragraph_number),
        ).fetchone()
        if row is None:
            return None
        return ParagraphRow(
            draft_tag=row["draft_tag"],
            stable_label=row["stable_label"],
            paragraph_number=row["paragraph_number"],
            raw_latex=row["raw_latex"],
            cleaned_text=row["cleaned_text"],
            normative_force=row["normative_force"],
        )

    def get_paragraphs(
        self, stable_label: str, draft_tag: str | None = None
    ) -> list[ParagraphRow]:
        tag = self._resolve_draft(draft_tag)
        if tag is None:
            return []
        rows = self.conn.execute(
            "SELECT * FROM section_paragraphs "
            "WHERE draft_tag = ? AND stable_label = ? "
            "ORDER BY paragraph_number",
            (tag, stable_label),
        ).fetchall()
        return [
            ParagraphRow(
                draft_tag=r["draft_tag"],
                stable_label=r["stable_label"],
                paragraph_number=r["paragraph_number"],
                raw_latex=r["raw_latex"],
                cleaned_text=r["cleaned_text"],
                normative_force=r["normative_force"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Draft metadata
    # ------------------------------------------------------------------

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
                standard_version=r["standard_version"],
                version_note=r["version_note"],
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
        """Return the most recently *published* draft tag.

        Tags like ``n5046`` sort lexicographically in publication order,
        so the highest tag value is the newest standard. This ensures
        that ingesting an older standard (e.g. n4950 after n5046) does
        not change the default.
        """
        row = self.conn.execute(
            "SELECT draft_tag FROM drafts ORDER BY draft_tag DESC LIMIT 1"
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
