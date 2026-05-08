# Copyright 2026 C++ Alliance (test suite)
# Distributed under the Boost Software License, Version 1.0.
# https://www.boost.org/LICENSE_1_0.txt

"""Unit tests for PDF metadata extraction fixes in wg21.py and pdf/__init__.py."""


from tomd.lib.pdf.types import Span, Line, Block
from tomd.lib.pdf.wg21 import _store_field


def _line(text: str) -> Line:
    return Line(spans=[Span(text=text)])


def _block(texts: list[str], page_num: int = 0) -> Block:
    return Block(lines=[_line(t) for t in texts], page_num=page_num)


class TestStoreFieldAppend:
    """_store_field must append to reply-to, never overwrite."""

    def test_author_after_reply_to_stays_in_bucket(self):
        """Author after explicit Reply-to goes to _author_names only."""
        metadata: dict = {}
        _store_field(metadata, "Reply to", ["Daveed Vandevoorde <daveed@vandevoorde.com>"])
        _store_field(metadata, "Author", ["Directions Group"])
        assert len(metadata["reply-to"]) == 1
        assert "Daveed Vandevoorde <daveed@vandevoorde.com>" in metadata["reply-to"]
        assert "Directions Group" not in metadata["reply-to"]
        assert "Directions Group" in metadata["_author_names"]

    def test_duplicate_not_added(self):
        metadata: dict = {}
        _store_field(metadata, "Reply to", ["Alice <alice@example.com>"])
        _store_field(metadata, "Author", ["Alice <alice@example.com>"])
        assert len(metadata["reply-to"]) == 1

    def test_author_fallback_then_editor_stays_in_bucket(self):
        """Authors as fallback fill reply-to; later Editor goes to _author_names."""
        metadata: dict = {}
        _store_field(metadata, "Authors", ["Alice", "Bob"])
        assert metadata["reply-to"] == ["Alice", "Bob"]
        _store_field(metadata, "Editor", ["Charlie"])
        # Editor does not overwrite or append: goes to _author_names only.
        assert metadata["reply-to"] == ["Alice", "Bob"]
        assert "Charlie" in metadata["_author_names"]

    def test_editor_after_explicit_reply_to_stays_in_bucket(self):
        """When explicit Reply-to exists, Editor goes to _author_names only."""
        metadata: dict = {}
        _store_field(metadata, "Reply to", ["Alice <alice@example.com>"])
        _store_field(metadata, "Editor", ["Charlie"])
        assert metadata["reply-to"] == ["Alice <alice@example.com>"]
        assert "Charlie" in metadata["_author_names"]


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

    def test_adds_missing_emails_when_some_exist(self):
        from tomd.lib.pdf import _enrich_pdf_reply_to

        metadata = {"reply-to": ["Alice <alice@example.com>"]}
        blocks = [_block(["Bob <bob@example.com>"])]
        _enrich_pdf_reply_to(metadata, blocks)
        assert len(metadata["reply-to"]) == 2
        assert "Alice <alice@example.com>" in metadata["reply-to"]
        assert "Bob <bob@example.com>" in metadata["reply-to"]

    def test_skips_duplicate_emails(self):
        from tomd.lib.pdf import _enrich_pdf_reply_to

        metadata = {"reply-to": ["Alice <alice@example.com>"]}
        blocks = [_block(["Alice <alice@example.com>"])]
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
