# Copyright (c) 2026 C++ Alliance, Inc. (https://cppalliance.org)
# Distributed under the Boost Software License, Version 1.0.
# https://www.boost.org/LICENSE_1_0.txt

"""Unit tests for metadata_yaml strip and format modules."""

from conftest import make_section
from tomd.lib.pdf.types import SectionKind
from tomd.lib.metadata_yaml.strip import (
    _matches_author_name,
    _is_content_heading,
    strip_metadata_headings,
    strip_pre_heading_fragments,
    strip_pre_content_paragraphs,
)
from tomd.lib.metadata_yaml.format import (
    format_front_matter,
    sanitize_metadata,
)


class TestMatchesAuthorName:

    def test_single_matching_name(self):
        assert _matches_author_name("John Smith", {"john", "smith"})

    def test_no_match(self):
        assert not _matches_author_name("Jane Doe", {"john", "smith"})

    def test_comma_rejects(self):
        assert not _matches_author_name("Smith, John", {"john", "smith"})

    def test_email_in_text_stripped(self):
        assert _matches_author_name(
            "John Smith john@example.com", {"john", "smith"})

    def test_short_tokens_ignored(self):
        assert not _matches_author_name("Jo", {"jo"})

    def test_partial_match_sufficient(self):
        assert _matches_author_name("John W Smith", {"john", "smith"})


class TestIsContentHeading:

    def test_numbered_heading(self):
        sec = make_section("1. Introduction", kind=SectionKind.HEADING)
        assert _is_content_heading(sec)

    def test_known_section_name(self):
        sec = make_section("Abstract", kind=SectionKind.HEADING)
        assert _is_content_heading(sec)

    def test_unknown_heading(self):
        sec = make_section("Random Title", kind=SectionKind.HEADING)
        assert not _is_content_heading(sec)

    def test_paragraph_rejected(self):
        sec = make_section("1. Introduction", kind=SectionKind.PARAGRAPH)
        assert not _is_content_heading(sec)

    def test_empty_text(self):
        sec = make_section("", kind=SectionKind.HEADING)
        assert not _is_content_heading(sec)

    def test_deep_numbered_heading(self):
        sec = make_section("1.2.3 Details", kind=SectionKind.HEADING)
        assert _is_content_heading(sec)


class TestStripMetadataHeadings:

    def test_removes_doc_number_heading(self):
        metadata = {"document": "P1234R0", "title": "Test Paper"}
        sections = [
            make_section("Doc. No.: P1234R0", kind=SectionKind.HEADING,
                         page_num=0),
            make_section("1. Introduction", kind=SectionKind.HEADING,
                         page_num=0),
            make_section("Body text."),
        ]
        n = strip_metadata_headings(sections, metadata)
        assert n >= 1
        assert not any("Doc. No." in s.text for s in sections)

    def test_preserves_content_headings(self):
        metadata = {"document": "P1234R0", "title": "Test"}
        sections = [
            make_section("1. Introduction", kind=SectionKind.HEADING,
                         page_num=0),
            make_section("Body text."),
        ]
        strip_metadata_headings(sections, metadata)
        assert any("Introduction" in s.text for s in sections)

    def test_empty_metadata(self):
        sections = [
            make_section("Something", kind=SectionKind.HEADING, page_num=0),
        ]
        n = strip_metadata_headings(sections, {})
        assert n == 0


class TestStripPreHeadingFragments:

    def test_strips_pre_heading_paragraphs(self):
        sections = [
            make_section("P1234R0", page_num=0),
            make_section("Author Name", page_num=0),
            make_section("1. Introduction", kind=SectionKind.HEADING,
                         page_num=0),
            make_section("Body text."),
        ]
        n = strip_pre_heading_fragments(sections)
        assert n == 2
        assert sections[0].kind == SectionKind.HEADING

    def test_no_heading_no_strip(self):
        sections = [
            make_section("Just text.", page_num=0),
        ]
        n = strip_pre_heading_fragments(sections)
        assert n == 0

    def test_heading_first_no_strip(self):
        sections = [
            make_section("Title", kind=SectionKind.HEADING, page_num=0),
            make_section("Body."),
        ]
        n = strip_pre_heading_fragments(sections)
        assert n == 0


class TestStripPreContentParagraphs:

    def test_strips_metadata_paragraphs_before_content(self):
        sections = [
            make_section("P1234R0", page_num=0),
            make_section("2026-01-15", page_num=0),
            make_section("1. Introduction", kind=SectionKind.HEADING,
                         page_num=0),
            make_section("Body text."),
        ]
        n = strip_pre_content_paragraphs(sections)
        assert n == 2

    def test_no_content_heading_no_strip(self):
        sections = [
            make_section("Random text", page_num=0),
            make_section("More text", page_num=0),
        ]
        n = strip_pre_content_paragraphs(sections)
        assert n == 0


class TestFormatFrontMatter:

    def test_basic_format(self):
        metadata = {
            "title": "Test Paper",
            "document": "P1234R0",
            "date": "2026-01-15",
        }
        result = format_front_matter(metadata)
        assert result.startswith("---\n")
        assert "---" in result
        assert "title:" in result
        assert "P1234R0" in result

    def test_empty_metadata_returns_empty(self):
        result = format_front_matter({})
        assert result == ""

    def test_field_order_matches_front_matter_order(self):
        metadata = {
            "date": "2026-01-15",
            "title": "Test Paper",
            "document": "P1234R0",
        }
        result = format_front_matter(metadata)
        title_pos = result.find("title:")
        doc_pos = result.find("document:")
        date_pos = result.find("date:")
        assert title_pos < doc_pos < date_pos


class TestSanitizeMetadata:

    def test_cleans_title_whitespace(self):
        metadata = {"title": "Test  Paper\n  Title"}
        result = sanitize_metadata(metadata)
        assert "\n" not in result["title"]
        assert "  " not in result["title"]

    def test_preserves_valid_fields(self):
        metadata = {"title": "Test Paper", "document": "P1234R0"}
        result = sanitize_metadata(metadata)
        assert result["title"] == "Test Paper"
        assert result["document"] == "P1234R0"
