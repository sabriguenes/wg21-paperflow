#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/paperlint
#

"""Tests for ``tomd.api.convert_paper`` and the YAML-fallback helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from paperstore import SqliteBackend, MissingSourceError
from tomd import api


def _stage(store: SqliteBackend, paper_id: str, *, suffix: str, mailing_row: dict) -> None:
    """Populate paperstore with a minimal source file + mailing-index row."""
    row = {"paper_id": paper_id.upper(), **mailing_row}
    store.upsert_year("2026", [row])
    store.put_source(paper_id, b"ignored-by-monkeypatched-converter", suffix=suffix)


def _patch_html(monkeypatch, md: str, prompts: list[str] | None = None):
    monkeypatch.setattr(api, "convert_html", lambda _p: (md, prompts))


def _patch_pdf(monkeypatch, md: str, prompts: list[str] | None = None):
    monkeypatch.setattr(api, "convert_pdf", lambda _p: (md, prompts))


def _convert_and_read(store: SqliteBackend, paper_id: str) -> str:
    source_path = store.get_source_path(paper_id)
    meta = store.get_meta(paper_id)
    markdown, _prompts, _intent = api.convert_paper(paper_id, source_path, meta)
    store.write_paper_md(paper_id, markdown)
    return markdown


class TestDispatch:
    def test_html_path_calls_convert_html(self, tmp_path: Path, monkeypatch):
        store = SqliteBackend(tmp_path)
        _stage(store, "P1", suffix=".html", mailing_row={"title": "T"})
        _patch_html(monkeypatch, "# Body\n\ntext\n")
        _patch_pdf(monkeypatch, "PDF SHOULD NOT BE CALLED")
        md = _convert_and_read(store, "P1")
        assert "PDF SHOULD NOT BE CALLED" not in md
        assert "text" in md
        assert store.get_paper_md("P1") == md

    def test_pdf_path_calls_convert_pdf(self, tmp_path: Path, monkeypatch):
        store = SqliteBackend(tmp_path)
        _stage(store, "P1", suffix=".pdf", mailing_row={"title": "T"})
        _patch_html(monkeypatch, "HTML SHOULD NOT BE CALLED")
        _patch_pdf(monkeypatch, "# Body\n\ntext\n")
        md = _convert_and_read(store, "P1")
        assert "HTML SHOULD NOT BE CALLED" not in md
        assert "text" in md


class TestEmptyMarkdownRaises:
    def test_empty_string_raises(self, tmp_path: Path, monkeypatch):
        store = SqliteBackend(tmp_path)
        _stage(store, "P1", suffix=".pdf", mailing_row={"title": "T"})
        _patch_pdf(monkeypatch, "", ["slide-deck detected"])
        with pytest.raises(RuntimeError, match="empty markdown"):
            _convert_and_read(store, "P1")

    def test_whitespace_only_raises(self, tmp_path: Path, monkeypatch):
        store = SqliteBackend(tmp_path)
        _stage(store, "P1", suffix=".pdf", mailing_row={"title": "T"})
        _patch_pdf(monkeypatch, "   \n\n\t\n")
        with pytest.raises(RuntimeError):
            _convert_and_read(store, "P1")


class TestStripTocSafetyNet:
    def test_short_toc_block_is_stripped(self, tmp_path: Path, monkeypatch):
        store = SqliteBackend(tmp_path)
        _stage(store, "P1", suffix=".html", mailing_row={"title": "T"})
        body = (
            "# Paper\n\n"
            "## Contents\n"
            "1. Intro .... 1\n"
            "2. Body .... 2\n\n"
            "## Real Section\n\nText.\n"
        )
        _patch_html(monkeypatch, body)
        md = _convert_and_read(store, "P1")
        assert "Real Section" in md
        assert "1. Intro" not in md


class TestMetadataFallback:
    def test_no_front_matter_inserts_block(self, tmp_path: Path, monkeypatch):
        store = SqliteBackend(tmp_path)
        _stage(
            store, "P3642R4", suffix=".html",
            mailing_row={
                "title": "Carry-less product",
                "subgroup": "LEWG",
                "authors": ["Alice <a@x>", "Bob <b@x>"],
                "document_date": "2026-02-15",
            },
        )
        _patch_html(monkeypatch, "Body only.\n")
        md = _convert_and_read(store, "P3642R4")
        assert md.startswith("---\n")
        assert "title:" in md
        assert "Carry-less product" in md
        assert "document:" in md and "P3642R4" in md.upper()
        assert "audience: LEWG" in md
        assert "reply-to:" in md
        assert "paper-type" not in md
        assert "Body only." in md

    def test_existing_field_wins_over_fallback(self, tmp_path: Path, monkeypatch):
        store = SqliteBackend(tmp_path)
        _stage(
            store, "P1", suffix=".html",
            mailing_row={"title": "Mailing Title", "subgroup": "LWG"},
        )
        body = (
            "---\n"
            'title: "Source Title"\n'
            "---\n\n"
            "Body.\n"
        )
        _patch_html(monkeypatch, body)
        md = _convert_and_read(store, "P1")
        assert 'title: "Source Title"' in md
        assert "Mailing Title" not in md
        assert "audience: LWG" in md

    def test_empty_meta_value_skipped(self, tmp_path: Path, monkeypatch):
        store = SqliteBackend(tmp_path)
        _stage(
            store, "P1", suffix=".html",
            mailing_row={"title": "T", "subgroup": "", "authors": []},
        )
        _patch_html(monkeypatch, "Body.\n")
        md = _convert_and_read(store, "P1")
        assert 'title: "T"' in md
        assert "audience:" not in md
        assert "reply-to:" not in md


class TestCanonicalFrontMatterOrder:
    def test_source_order_is_normalized(self, tmp_path: Path, monkeypatch):
        """Source front matter in arbitrary order is rewritten in canonical order."""
        store = SqliteBackend(tmp_path)
        _stage(store, "P1", suffix=".html", mailing_row={"title": "Mailing T"})
        body = (
            "---\n"
            "reply-to:\n"
            '  - "Alice <a@x>"\n'
            "audience: LEWG\n"
            "intent: info\n"
            "date: 2026-04-28\n"
            "document: P1R0\n"
            "title: Out Of Order\n"
            "---\n\n"
            "Body.\n"
        )
        _patch_html(monkeypatch, body)
        md = _convert_and_read(store, "P1")
        front, _, _ = md.partition("\n---\n")
        front += "\n---"
        lines = front.splitlines()
        keys = [l.split(":")[0] for l in lines if l and not l.startswith((" ", "\t", "-")) and l != "---"]
        assert keys[0] == "title"
        assert "reply-to" in keys
        assert keys[-1] == "reply-to", "reply-to must be last"
        assert keys.index("title") < keys.index("date") < keys.index("audience")

    def test_fallback_lands_in_canonical_order(self, tmp_path: Path, monkeypatch):
        """Mailing-row fallback fields land in canonical positions, not at the end."""
        store = SqliteBackend(tmp_path)
        _stage(
            store, "P9999R0", suffix=".html",
            mailing_row={
                "title": "Carry-less product",
                "subgroup": "LEWG",
                "authors": ["Alice <a@x>"],
                "document_date": "2026-02-15",
            },
        )
        body = (
            "---\n"
            "intent: info\n"
            "---\n\n"
            "Body.\n"
        )
        _patch_html(monkeypatch, body)
        md = _convert_and_read(store, "P9999R0")
        title_pos = md.index("title:")
        document_pos = md.index("document:")
        date_pos = md.index("date:")
        intent_pos = md.index("intent:")
        audience_pos = md.index("audience:")
        reply_to_pos = md.index("reply-to:")
        assert title_pos < document_pos < date_pos < intent_pos < audience_pos < reply_to_pos


class TestErrors:
    def test_missing_source_raises(self, tmp_path: Path):
        store = SqliteBackend(tmp_path)
        store.upsert_year("2026", [{"paper_id": "P1", "title": "T"}])
        with pytest.raises(MissingSourceError):
            store.get_source_path("P1")  # no source staged

    def test_convert_with_empty_meta_succeeds(self, tmp_path: Path, monkeypatch):
        # put_source creates a minimal row; conversion succeeds with empty metadata.
        store = SqliteBackend(tmp_path)
        store.put_source("P1", b"x", suffix=".pdf")
        source_path = store.get_source_path("P1")
        meta = store.get_meta("P1")
        _patch_pdf(monkeypatch, "# Body\n\ntext\n")
        markdown, _prompts, _intent = api.convert_paper("P1", source_path, meta)
        assert "text" in markdown
