#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

from __future__ import annotations

import pytest

from assay.chunker import Section, chunk_paper


SIMPLE_PAPER = """\
---
title: Test
---

## Section One

This is section one with some content that spans
multiple lines to give it enough characters.

## Section Two

Short.

## Section Three

This section has more text to work with so it will
be above the minimum character threshold we set. It
contains multiple sentences and paragraphs to make
it substantive enough for testing purposes.

More content here to pad it out a bit further for
the character count to be meaningful.
"""

BOLD_SUBSECTION_PAPER = """\
---
title: Test Bold Subsections
---

## Big Section

Some intro text.

**3.1** **First Subsection**

Content of first subsection that should be split out
into its own chunk when the parent is oversized.

**3.2** **Second Subsection**

Content of second subsection with enough text to be
meaningful and testable as an independent chunk.

**3.3** **Third Subsection**

Content of third subsection rounding out the test.
"""


class TestChunkPaperBasic:
    def test_returns_sections(self):
        result = chunk_paper(SIMPLE_PAPER)
        assert all(isinstance(s, Section) for s in result)

    def test_sections_have_char_count(self):
        result = chunk_paper(SIMPLE_PAPER)
        assert all(s.char_count > 0 for s in result)

    def test_sections_cover_full_paper(self):
        result = chunk_paper(SIMPLE_PAPER)
        assert result[0].start_line >= 1
        lines = SIMPLE_PAPER.splitlines()
        assert result[-1].end_line <= len(lines)

    def test_no_gaps_between_sections(self):
        result = chunk_paper(SIMPLE_PAPER)
        for i in range(len(result) - 1):
            assert result[i].end_line == result[i + 1].start_line - 1 or \
                   result[i].end_line >= result[i + 1].start_line - 1

    def test_empty_source(self):
        result = chunk_paper("")
        assert len(result) == 1
        assert result[0].heading == "(untitled)"

    def test_no_headings(self):
        result = chunk_paper("Just plain text\nwith no headings.\n")
        assert len(result) == 1
        assert result[0].heading == "(untitled)"


class TestMinChars:
    def test_merge_small_sections(self):
        all_sections = chunk_paper(SIMPLE_PAPER, min_chars=0)
        merged = chunk_paper(SIMPLE_PAPER, min_chars=200)
        assert len(merged) <= len(all_sections)

    def test_no_section_below_min(self):
        result = chunk_paper(SIMPLE_PAPER, min_chars=50)
        for s in result:
            assert s.char_count >= 50 or s == result[-1]

    def test_min_chars_zero_no_merge(self):
        result = chunk_paper(SIMPLE_PAPER, min_chars=0)
        headings = [s.heading for s in result]
        assert "Section Two" in headings


class TestMaxChars:
    def test_large_max_no_split(self):
        result = chunk_paper(SIMPLE_PAPER, max_chars=999999)
        all_under = all(s.char_count <= 999999 for s in result)
        assert all_under

    def test_small_max_more_chunks(self):
        big = chunk_paper(SIMPLE_PAPER, max_chars=999999)
        small = chunk_paper(SIMPLE_PAPER, max_chars=100)
        assert len(small) >= len(big)


class TestBoldSubsectionSplit:
    def test_splits_on_bold_pattern(self):
        result = chunk_paper(BOLD_SUBSECTION_PAPER, max_chars=50)
        headings = [s.heading for s in result]
        assert any("3.1" in h for h in headings)
        assert any("3.2" in h for h in headings)
        assert any("3.3" in h for h in headings)

    def test_no_split_when_under_max(self):
        result = chunk_paper(BOLD_SUBSECTION_PAPER, max_chars=999999)
        assert len(result) == 1

    def test_split_preserves_line_numbers(self):
        result = chunk_paper(BOLD_SUBSECTION_PAPER, max_chars=50)
        for s in result:
            assert s.start_line >= 1
            assert s.end_line >= s.start_line
