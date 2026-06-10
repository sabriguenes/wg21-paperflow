# Copyright (c) 2026 C++ Alliance, Inc. (https://cppalliance.org)
# Distributed under the Boost Software License, Version 1.0.
# https://www.boost.org/LICENSE_1_0.txt

"""Unit tests for body/abstract.py functions."""

from conftest import make_section
from tomd.lib.pdf.types import SectionKind, Line, Span
from tomd.lib.body.abstract import (
    dedup_abstract,
    promote_abstract_from_uncertain,
    reorder_abstract_in_uncertain,
    rescue_stranded_abstract_body,
    strip_metadata_from_uncertain,
)


class TestDedupAbstract:
    """Tests for dedup_abstract: removes duplicate Abstract headings."""

    def test_single_abstract_unchanged(self):
        sections = [
            make_section("Abstract", kind=SectionKind.HEADING),
            make_section("This is the abstract body."),
        ]
        dedup_abstract(sections)
        assert len(sections) == 2

    def test_no_abstract_unchanged(self):
        sections = [
            make_section("Introduction", kind=SectionKind.HEADING),
            make_section("Some text."),
        ]
        dedup_abstract(sections)
        assert len(sections) == 2

    def test_duplicate_keeps_one_with_body(self):
        sections = [
            make_section("Abstract", kind=SectionKind.HEADING),
            make_section("Introduction", kind=SectionKind.HEADING),
            make_section("Abstract", kind=SectionKind.HEADING),
            make_section("Real abstract body text here."),
        ]
        dedup_abstract(sections)
        abstract_count = sum(
            1 for s in sections
            if s.kind == SectionKind.HEADING
            and s.text.strip().lower() == "abstract"
        )
        assert abstract_count == 1
        assert any("Real abstract" in s.text for s in sections)

    def test_two_empty_abstracts_keeps_first(self):
        sections = [
            make_section("Abstract", kind=SectionKind.HEADING),
            make_section("Abstract", kind=SectionKind.HEADING),
            make_section("Introduction", kind=SectionKind.HEADING),
        ]
        dedup_abstract(sections)
        abstract_count = sum(
            1 for s in sections
            if s.kind == SectionKind.HEADING
            and s.text.strip().lower() == "abstract"
        )
        assert abstract_count == 1

    def test_empty_sections_list(self):
        sections = []
        dedup_abstract(sections)
        assert sections == []


class TestPromoteAbstractFromUncertain:
    """Tests for promote_abstract_from_uncertain."""

    def test_promotes_abstract_from_uncertain_section(self):
        sections = [
            make_section(
                "Abstract\nThis is a long enough abstract body with more than "
                "ten words to pass the minimum word count threshold.",
                kind=SectionKind.UNCERTAIN, page_num=0,
            ),
        ]
        promote_abstract_from_uncertain(sections)
        kinds = [s.kind for s in sections]
        assert SectionKind.HEADING in kinds

    def test_skips_when_content_heading_exists_on_page0(self):
        sections = [
            make_section("1. Introduction", kind=SectionKind.HEADING, page_num=0),
            make_section(
                "Abstract\nThis is a long enough abstract body with more than "
                "ten words to pass the minimum word count.",
                kind=SectionKind.UNCERTAIN, page_num=0,
            ),
        ]
        original_len = len(sections)
        promote_abstract_from_uncertain(sections)
        assert len(sections) == original_len

    def test_skips_short_abstract_body(self):
        sections = [
            make_section(
                "Abstract\nToo short.",
                kind=SectionKind.UNCERTAIN, page_num=0,
            ),
        ]
        promote_abstract_from_uncertain(sections)
        assert all(s.kind != SectionKind.HEADING for s in sections)

    def test_skips_non_page0(self):
        sections = [
            make_section(
                "Abstract\nThis is a long enough abstract body with more than "
                "ten words to pass the minimum word count threshold.",
                kind=SectionKind.UNCERTAIN, page_num=1,
            ),
        ]
        promote_abstract_from_uncertain(sections)
        assert all(s.kind == SectionKind.UNCERTAIN for s in sections)


class TestStripMetadataFromUncertain:
    """Tests for strip_metadata_from_uncertain."""

    def test_removes_metadata_echo_lines(self):
        metadata = {
            "title": "My Paper",
            "document": "P1234R0",
            "reply-to": ["Author Name"],
        }
        sections = [
            make_section(
                "Document: P1234R0\nDate: 2026-01-01\nActual content here.",
                kind=SectionKind.UNCERTAIN, page_num=0,
            ),
        ]
        strip_metadata_from_uncertain(sections, metadata)
        remaining_text = " ".join(s.text for s in sections)
        assert "Actual content" in remaining_text

    def test_skips_non_uncertain(self):
        metadata = {"title": "Test", "document": "P0001R0"}
        sections = [
            make_section("Document: P0001R0", kind=SectionKind.PARAGRAPH),
        ]
        original_text = sections[0].text
        strip_metadata_from_uncertain(sections, metadata)
        assert sections[0].text == original_text


class TestReorderAbstractInUncertain:
    """Tests for reorder_abstract_in_uncertain."""

    def test_empty_sections(self):
        sections = []
        reorder_abstract_in_uncertain(sections)
        assert sections == []

    def test_no_uncertain_unchanged(self):
        sections = [
            make_section("Introduction", kind=SectionKind.HEADING),
            make_section("Some text."),
        ]
        original = list(sections)
        reorder_abstract_in_uncertain(sections)
        assert len(sections) == len(original)

    def test_abstract_moved_to_top_of_uncertain(self):
        """UNCERTAIN section with Abstract buried after other content."""
        text = "CONTENTS\nIntroduction....1\nAbstract\nThis paper proposes."
        sections = [
            make_section(text, kind=SectionKind.UNCERTAIN, page_num=0),
        ]
        reorder_abstract_in_uncertain(sections)
        result_lines = sections[0].text.split("\n")
        assert result_lines[0].strip().lower() == "abstract"


class TestRescueStrandedAbstractBody:
    """Tests for rescue_stranded_abstract_body."""

    def _make_sec_with_y(self, text, kind, page_num, top_y):
        """Helper: section with a specific top-y coordinate."""
        line = Line(
            spans=[Span(text=text)],
            bbox=(72.0, top_y, 500.0, top_y + 12.0),
        )
        from dataclasses import replace as dc_replace
        sec = make_section(text, kind=kind, page_num=page_num)
        return dc_replace(sec, lines=[line])

    def test_rescues_stranded_body(self):
        """Abstract heading + next heading with no body => paragraph rescued."""
        abstract_hdr = self._make_sec_with_y(
            "Abstract", SectionKind.HEADING, 0, 100.0)
        next_hdr = self._make_sec_with_y(
            "Background", SectionKind.HEADING, 0, 200.0)
        stranded_para = self._make_sec_with_y(
            "This paper proposes a new approach to memory management.",
            SectionKind.PARAGRAPH, 0, 110.0)

        sections = [abstract_hdr, next_hdr, stranded_para]
        rescue_stranded_abstract_body(sections)
        assert sections[1].text.startswith("This paper proposes")
        assert sections[1].kind == SectionKind.PARAGRAPH

    def test_no_rescue_when_closer_to_next_heading(self):
        """Paragraph closer to the next heading stays in place."""
        abstract_hdr = self._make_sec_with_y(
            "Abstract", SectionKind.HEADING, 0, 100.0)
        next_hdr = self._make_sec_with_y(
            "Background", SectionKind.HEADING, 0, 200.0)
        para = self._make_sec_with_y(
            "Background content here.",
            SectionKind.PARAGRAPH, 0, 195.0)

        sections = [abstract_hdr, next_hdr, para]
        rescue_stranded_abstract_body(sections)
        assert sections[2].text.startswith("Background content")

    def test_no_rescue_when_body_already_present(self):
        """Abstract already has body text => nothing to rescue."""
        abstract_hdr = self._make_sec_with_y(
            "Abstract", SectionKind.HEADING, 0, 100.0)
        body = self._make_sec_with_y(
            "Existing abstract body.", SectionKind.PARAGRAPH, 0, 112.0)
        next_hdr = self._make_sec_with_y(
            "Introduction", SectionKind.HEADING, 0, 200.0)

        sections = [abstract_hdr, body, next_hdr]
        rescue_stranded_abstract_body(sections)
        assert sections[1].text == "Existing abstract body."
