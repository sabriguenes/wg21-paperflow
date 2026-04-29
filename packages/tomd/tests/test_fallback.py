#
# Copyright (c) 2026 Sergio DuBois (sentientsergio@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/paperlint
#

"""Tests for _apply_metadata_fallback and _reorder_yaml_body in tomd.api."""

from tomd.api import (
    _apply_metadata_fallback,
    _reorder_yaml_body,
    _remove_yaml_key,
)


class TestReorderYamlBody:
    def test_already_ordered(self):
        body = "title: T\ndocument: P0001R0\ndate: 2026-01-01\nrevision: 0"
        result = _reorder_yaml_body(body)
        keys = [l.split(":")[0] for l in result.splitlines() if l]
        assert keys[0] == "title"
        assert "document" in keys
        assert "date" in keys

    def test_date_after_reply_to_reordered(self):
        body = (
            "title: T\n"
            "document: P0001R0\n"
            "revision: 0\n"
            "intent: info\n"
            "audience: EWG\n"
            "reply-to:\n"
            '  - "A <a@b.com>"\n'
            "date: 2026-01-01"
        )
        result = _reorder_yaml_body(body)
        keys = [
            l.split(":")[0]
            for l in result.splitlines()
            if l and not l.startswith((" ", "\t", "-"))
        ]
        assert keys == ["title", "document", "revision", "date",
                        "intent", "audience", "reply-to"]

    def test_unknown_keys_appended_after_canonical(self):
        body = "custom: val\ntitle: T\ndocument: D"
        result = _reorder_yaml_body(body)
        keys = [
            l.split(":")[0]
            for l in result.splitlines()
            if l and not l.startswith((" ", "\t", "-"))
        ]
        assert keys == ["title", "document", "custom"]

    def test_multiline_reply_to_stays_together(self):
        body = (
            "title: T\n"
            "reply-to:\n"
            '  - "Alice <a@b.com>"\n'
            '  - "Bob <b@b.com>"\n'
            "document: D"
        )
        result = _reorder_yaml_body(body)
        lines = result.splitlines()
        rt_idx = next(i for i, l in enumerate(lines) if l.startswith("reply-to"))
        assert lines[rt_idx + 1].strip().startswith('- "Alice')
        assert lines[rt_idx + 2].strip().startswith('- "Bob')


class TestApplyMetadataFallback:
    def test_no_mailing_meta_unchanged(self):
        md = "---\ntitle: T\n---\n\nBody\n"
        assert _apply_metadata_fallback(md, None) == md

    def test_existing_field_not_overwritten(self):
        md = "---\ntitle: Original\n---\n\nBody\n"
        result = _apply_metadata_fallback(md, {"title": "Replaced"})
        assert "Original" in result
        assert "Replaced" not in result

    def test_missing_date_injected_at_correct_position(self):
        md = (
            "---\n"
            "title: T\n"
            "document: P0001R0\n"
            "revision: 0\n"
            "intent: info\n"
            "audience: EWG\n"
            "reply-to:\n"
            '  - "A <a@b.com>"\n'
            "---\n\n"
            "Body\n"
        )
        result = _apply_metadata_fallback(md, {"document_date": "2026-03-01"})
        lines = result.splitlines()
        fm_lines = []
        in_fm = False
        for l in lines:
            if l.strip() == "---":
                if in_fm:
                    break
                in_fm = True
                continue
            if in_fm:
                fm_lines.append(l)
        keys = [
            l.split(":")[0]
            for l in fm_lines
            if l and not l.startswith((" ", "\t", "-"))
        ]
        assert keys.index("date") < keys.index("intent")
        assert keys.index("date") < keys.index("audience")
        assert keys.index("date") < keys.index("reply-to")

    def test_empty_value_not_injected(self):
        md = "---\ntitle: T\n---\n\nBody\n"
        result = _apply_metadata_fallback(md, {"document_date": ""})
        assert "date" not in result

    def test_document_overridden_d_to_p(self):
        """D-prefix document number is replaced by mailing P-number."""
        md = "---\ntitle: T\ndocument: D2583R2\ndate: 2026-03-09\n---\n\nBody\n"
        result = _apply_metadata_fallback(md, {"paper_id": "P2583R2"})
        assert "document: P2583R2" in result
        assert "D2583R2" not in result

    def test_document_overridden_different_number(self):
        """Completely different internal number is replaced (n5034 case)."""
        md = "---\ntitle: Agenda\ndocument: N5022\ndate: 2025-11-01\n---\n\nBody\n"
        result = _apply_metadata_fallback(md, {"paper_id": "N5034"})
        assert "document: N5034" in result
        assert "N5022" not in result

    def test_document_override_preserves_other_fields(self):
        """Overriding document does not touch other present fields."""
        md = (
            "---\n"
            "title: My Title\n"
            "document: D1234R0\n"
            "date: 2026-01-01\n"
            "audience: LEWG\n"
            "---\n\nBody\n"
        )
        result = _apply_metadata_fallback(md, {
            "paper_id": "P1234R0",
            "title": "Different Title",
            "subgroup": "EWG",
        })
        assert "document: P1234R0" in result
        assert "title: My Title" in result
        assert "audience: LEWG" in result
        assert "Different Title" not in result
        assert "audience: EWG" not in result

    def test_document_override_field_order_preserved(self):
        """After override, document stays in its canonical position."""
        md = (
            "---\n"
            "title: T\n"
            "document: D9999R0\n"
            "date: 2026-01-01\n"
            "---\n\nBody\n"
        )
        result = _apply_metadata_fallback(md, {"paper_id": "P9999R0"})
        lines = [
            l for l in result.splitlines()
            if l and l.strip() != "---" and not l.startswith((" ", "\t", "-"))
        ]
        keys = [l.split(":")[0] for l in lines if ":" in l]
        assert keys.index("title") < keys.index("document")
        assert keys.index("document") < keys.index("date")


class TestRemoveYamlKey:
    def test_removes_simple_key(self):
        body = "title: T\ndocument: D1234R0\ndate: 2026-01-01"
        result = _remove_yaml_key(body, "document")
        assert "document" not in result
        assert "title: T" in result
        assert "date: 2026-01-01" in result

    def test_removes_multiline_key(self):
        body = "title: T\nreply-to:\n  - Alice\n  - Bob\ndate: 2026-01-01"
        result = _remove_yaml_key(body, "reply-to")
        assert "reply-to" not in result
        assert "Alice" not in result
        assert "Bob" not in result
        assert "date: 2026-01-01" in result

    def test_noop_when_key_absent(self):
        body = "title: T\ndate: 2026-01-01"
        assert _remove_yaml_key(body, "document") == body
