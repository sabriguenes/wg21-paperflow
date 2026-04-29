# Copyright 2026 C++ Alliance (test suite)
# Distributed under the Boost Software License, Version 1.0.
# https://www.boost.org/LICENSE_1_0.txt

"""Unit tests for PDF metadata extraction fixes in wg21.py and pdf/__init__.py."""

import pytest

from tomd.lib.pdf.types import Span, Line, Block
from tomd.lib.pdf.wg21 import _store_field


def _line(text: str) -> Line:
    return Line(spans=[Span(text=text)])


def _block(texts: list[str], page_num: int = 0) -> Block:
    return Block(lines=[_line(t) for t in texts], page_num=page_num)


class TestStoreFieldAppend:
    """_store_field must append to reply-to, never overwrite."""

    def test_author_after_reply_to_appends(self):
        metadata: dict = {}
        _store_field(metadata, "Reply to", ["Daveed Vandevoorde <daveed@vandevoorde.com>"])
        _store_field(metadata, "Author", ["Directions Group"])
        assert len(metadata["reply-to"]) == 2
        assert "Daveed Vandevoorde <daveed@vandevoorde.com>" in metadata["reply-to"]
        assert "Directions Group" in metadata["reply-to"]

    def test_duplicate_not_added(self):
        metadata: dict = {}
        _store_field(metadata, "Reply to", ["Alice <alice@example.com>"])
        _store_field(metadata, "Author", ["Alice <alice@example.com>"])
        assert len(metadata["reply-to"]) == 1

    def test_bare_names_overwritten_by_later_label(self):
        """When existing has no emails, later label overwrites (same-field re-parse)."""
        metadata: dict = {}
        _store_field(metadata, "Authors", ["Alice", "Bob"])
        _store_field(metadata, "Editor", ["Charlie"])
        assert metadata["reply-to"] == ["Charlie"]

    def test_append_when_existing_has_emails(self):
        """When existing has emails, later bare-name label appends."""
        metadata: dict = {}
        _store_field(metadata, "Reply to", ["Alice <alice@example.com>"])
        _store_field(metadata, "Editor", ["Charlie"])
        assert metadata["reply-to"] == ["Alice <alice@example.com>", "Charlie"]


class TestStoreFieldEmail:
    """_store_field must handle separate Email: labels."""

    def test_email_pairs_with_bare_names(self):
        metadata: dict = {}
        _store_field(metadata, "Authors", ["Alice", "Bob", "Charlie", "Dave"])
        _store_field(metadata, "Email", [
            "alice@example.com, bob@example.com, charlie@example.com, dave@example.com"
        ])
        assert len(metadata["reply-to"]) == 4
        assert "Alice <alice@example.com>" in metadata["reply-to"]
        assert "Bob <bob@example.com>" in metadata["reply-to"]
        assert "Charlie <charlie@example.com>" in metadata["reply-to"]
        assert "Dave <dave@example.com>" in metadata["reply-to"]

    def test_email_without_matching_names(self):
        metadata: dict = {}
        _store_field(metadata, "Email", ["test@example.com, other@example.com"])
        assert "<test@example.com>" in metadata["reply-to"]
        assert "<other@example.com>" in metadata["reply-to"]

    def test_email_skip_duplicates(self):
        metadata: dict = {}
        _store_field(metadata, "Reply to", ["Alice <alice@example.com>"])
        _store_field(metadata, "Email", ["alice@example.com"])
        assert len(metadata["reply-to"]) == 1


class TestEnrichPdfReplyTo:
    """_enrich_pdf_reply_to post-pass picks up emails missed by label extractors."""

    def test_bare_name_gets_email_from_page0(self):
        from tomd.lib.pdf import _enrich_pdf_reply_to

        metadata = {"reply-to": ["Hans Boehm"]}
        blocks = [_block(["Hans Boehm <hboehm@google.com>"])]
        _enrich_pdf_reply_to(metadata, blocks)
        assert metadata["reply-to"] == ["Hans Boehm <hboehm@google.com>"]

    def test_skips_when_emails_already_present(self):
        from tomd.lib.pdf import _enrich_pdf_reply_to

        metadata = {"reply-to": ["Alice <alice@example.com>"]}
        blocks = [_block(["Bob <bob@example.com>"])]
        _enrich_pdf_reply_to(metadata, blocks)
        assert len(metadata["reply-to"]) == 1
        assert "Alice <alice@example.com>" in metadata["reply-to"]

    def test_missing_email_appended_with_name(self):
        from tomd.lib.pdf import _enrich_pdf_reply_to

        metadata = {"reply-to": []}
        blocks = [_block(["Daveed Vandevoorde <daveed@vandevoorde.com>"])]
        _enrich_pdf_reply_to(metadata, blocks)
        assert metadata["reply-to"] == [
            "Daveed Vandevoorde <daveed@vandevoorde.com>"
        ]

    def test_no_emails_on_page0_noop(self):
        from tomd.lib.pdf import _enrich_pdf_reply_to

        metadata = {"reply-to": ["Alice"]}
        blocks = [_block(["Some text without emails"])]
        _enrich_pdf_reply_to(metadata, blocks)
        assert metadata["reply-to"] == ["Alice"]

    def test_page1_emails_ignored(self):
        from tomd.lib.pdf import _enrich_pdf_reply_to

        metadata = {"reply-to": []}
        blocks = [_block(["No emails here"], page_num=0),
                  _block(["hidden@example.com"], page_num=1)]
        _enrich_pdf_reply_to(metadata, blocks)
        assert metadata["reply-to"] == []
