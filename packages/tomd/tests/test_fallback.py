#
# Copyright (c) 2026 Sergio DuBois (sentientsergio@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Tests for _normalize_front_matter in tomd.api."""

from tomd.api import _normalize_front_matter


def _extract_fm_keys(md: str) -> list[str]:
    """Extract top-level YAML keys from front matter in order."""
    lines = md.splitlines()
    keys: list[str] = []
    in_fm = False
    for line in lines:
        if line.strip() == "---":
            if in_fm:
                break
            in_fm = True
            continue
        if in_fm and line and not line.startswith((" ", "\t", "-")):
            head, sep, _ = line.partition(":")
            if sep:
                keys.append(head.strip())
    return keys


class TestCanonicalOrdering:
    def test_already_ordered(self):
        md = "---\ntitle: T\ndocument: P0001R0\ndate: 2026-01-01\n---\n\nBody\n"
        result = _normalize_front_matter(md, None)
        keys = _extract_fm_keys(result)
        assert keys[0] == "title"
        assert "document" in keys
        assert "date" in keys

    def test_date_after_reply_to_reordered(self):
        md = (
            "---\n"
            "title: T\n"
            "document: P0001R0\n"
            "intent: info\n"
            "audience: EWG\n"
            "reply-to:\n"
            '  - "A <a@b.com>"\n'
            "date: 2026-01-01\n"
            "---\n\nBody\n"
        )
        result = _normalize_front_matter(md, None)
        keys = _extract_fm_keys(result)
        assert keys == ["title", "document", "date",
                        "intent", "audience", "reply-to"]

    def test_unknown_keys_appended_after_canonical(self):
        md = "---\ncustom: val\ntitle: T\ndocument: P0001R0\n---\n\nBody\n"
        result = _normalize_front_matter(md, None)
        keys = _extract_fm_keys(result)
        assert keys.index("title") < keys.index("custom")
        assert keys.index("document") < keys.index("custom")

    def test_multiline_reply_to_stays_together(self):
        md = (
            "---\n"
            "title: T\n"
            "reply-to:\n"
            '  - "Alice <a@b.com>"\n'
            '  - "Bob <b@b.com>"\n'
            "document: P0001R0\n"
            "---\n\nBody\n"
        )
        result = _normalize_front_matter(md, None)
        lines = result.splitlines()
        rt_idx = next(i for i, l in enumerate(lines) if l.startswith("reply-to"))
        assert lines[rt_idx + 1].strip().startswith('- "Alice')
        assert lines[rt_idx + 2].strip().startswith('- "Bob')


class TestMetadataFallback:
    def test_no_mailing_meta_preserves_content(self):
        md = "---\ntitle: T\n---\n\nBody\n"
        result = _normalize_front_matter(md, None)
        assert "title" in result
        assert "Body" in result

    def test_existing_field_not_overwritten(self):
        md = "---\ntitle: Original\n---\n\nBody\n"
        result = _normalize_front_matter(md, {"title": "Replaced"})
        assert "Original" in result
        assert "Replaced" not in result

    def test_missing_date_injected_at_correct_position(self):
        md = (
            "---\n"
            "title: T\n"
            "document: P0001R0\n"
            "intent: info\n"
            "audience: EWG\n"
            "reply-to:\n"
            '  - "A <a@b.com>"\n'
            "---\n\n"
            "Body\n"
        )
        result = _normalize_front_matter(md, {"document_date": "2026-03-01"})
        keys = _extract_fm_keys(result)
        assert keys.index("date") < keys.index("intent")
        assert keys.index("date") < keys.index("audience")
        assert keys.index("date") < keys.index("reply-to")

    def test_empty_value_not_injected(self):
        md = "---\ntitle: T\n---\n\nBody\n"
        result = _normalize_front_matter(md, {"document_date": ""})
        assert "date:" not in result

    def test_document_overridden_d_to_p(self):
        """D-prefix document number is replaced by mailing P-number."""
        md = "---\ntitle: T\ndocument: D2583R2\ndate: 2026-03-09\n---\n\nBody\n"
        result = _normalize_front_matter(md, {"paper_id": "P2583R2"})
        assert "document: P2583R2" in result
        assert "D2583R2" not in result

    def test_document_overridden_different_number(self):
        """Completely different internal number is replaced (n5034 case)."""
        md = "---\ntitle: Agenda\ndocument: N5022\ndate: 2025-11-01\n---\n\nBody\n"
        result = _normalize_front_matter(md, {"paper_id": "N5034"})
        assert "document: N5034" in result
        assert "N5022" not in result

    def test_document_override_preserves_other_fields(self):
        """Overriding document does not touch other present fields."""
        md = (
            "---\n"
            'title: "My Title"\n'
            "document: D1234R0\n"
            "date: 2026-01-01\n"
            "audience: LEWG\n"
            "---\n\nBody\n"
        )
        result = _normalize_front_matter(md, {
            "paper_id": "P1234R0",
            "title": "Different Title",
            "subgroup": "EWG",
        })
        assert "document: P1234R0" in result
        assert "My Title" in result
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
        result = _normalize_front_matter(md, {"paper_id": "P9999R0"})
        keys = _extract_fm_keys(result)
        assert keys.index("title") < keys.index("document")
        assert keys.index("document") < keys.index("date")

    def test_no_front_matter_with_mailing_meta_creates_block(self):
        """When no front matter exists but mailing meta is provided, create one."""
        md = "Body text here\n"
        result = _normalize_front_matter(md, {"title": "My Paper", "paper_id": "P1234R0"})
        assert result.startswith("---\n")
        assert "title" in result
        assert "document: P1234R0" in result
        assert "Body text here" in result

    def test_no_front_matter_no_meta_unchanged(self):
        """No front matter and no mailing meta returns input unchanged."""
        md = "Body text here\n"
        assert _normalize_front_matter(md, None) == md
