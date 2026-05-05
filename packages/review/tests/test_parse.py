#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for the general-purpose markdown section splitter.

All tests use synthetic markdown - not tied to review.md content.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from review.parse import sections


def test_basic_h2_split():
    md = "## Alpha\n\nBody of alpha.\n\n## Beta\n\nBody of beta."
    result = sections(md)
    assert result["Alpha"] == "Body of alpha."
    assert result["Beta"] == "Body of beta."


def test_preamble_before_first_h2():
    md = "# Title\n\nIntro text.\n\n## First\n\nBody."
    result = sections(md)
    assert result["_preamble"] == "# Title\n\nIntro text."
    assert result["First"] == "Body."


def test_no_preamble_when_starts_with_h2():
    md = "## Only\n\nContent."
    result = sections(md)
    assert "_preamble" not in result
    assert result["Only"] == "Content."


def test_hr_terminates_section():
    md = "## A\n\nBefore rule.\n\n---\n\nAfter rule.\n\n## B\n\nBody B."
    result = sections(md)
    assert result["A"] == "Before rule."
    assert result["B"] == "Body B."
    assert "After rule." not in result.get("A", "")


def test_hr_skips_until_next_h2():
    md = "## A\n\nBody A.\n\n---\n\nOrphaned text.\nMore orphaned.\n\n## B\n\nBody B."
    result = sections(md)
    assert result["A"] == "Body A."
    assert result["B"] == "Body B."
    assert "Orphaned" not in str(result)


def test_consecutive_hrs():
    md = "## A\n\nBody.\n\n---\n\n---\n\nStill skipped.\n\n## B\n\nOK."
    result = sections(md)
    assert result["A"] == "Body."
    assert result["B"] == "OK."


def test_empty_section_body():
    md = "## Empty\n\n## Notempty\n\nHas content."
    result = sections(md)
    assert result["Empty"] == ""
    assert result["Notempty"] == "Has content."


def test_h2_with_trailing_whitespace():
    md = "## Trimmed   \n\nBody."
    result = sections(md)
    assert "Trimmed" in result


def test_hr_with_surrounding_whitespace():
    md = "## A\n\nBody.\n\n  ---  \n\nSkipped.\n\n## B\n\nOK."
    result = sections(md)
    assert result["A"] == "Body."
    assert result["B"] == "OK."


def test_hr_at_end_of_document():
    md = "## A\n\nBody.\n\n---\n"
    result = sections(md)
    assert result["A"] == "Body."


def test_path_input(tmp_path: Path):
    md_file = tmp_path / "test.md"
    md_file.write_text("# Title\n\n## Sec\n\nContent.", encoding="utf-8")
    result = sections(md_file)
    assert result["_preamble"] == "# Title"
    assert result["Sec"] == "Content."


def test_preserves_internal_formatting():
    md = (
        "## Rich\n\n"
        "**Bold** and *italic*.\n\n"
        "- list item\n"
        "- another\n\n"
        "```python\ncode()\n```"
    )
    result = sections(md)
    assert "**Bold**" in result["Rich"]
    assert "```python" in result["Rich"]


def test_h3_within_section_preserved():
    md = "## Outer\n\n### Inner\n\nNested content.\n\n### Another\n\nMore."
    result = sections(md)
    assert "### Inner" in result["Outer"]
    assert "### Another" in result["Outer"]
    assert "Inner" not in result  # H3 is not a split boundary


def test_empty_input():
    result = sections("")
    assert result == {}


def test_only_preamble():
    md = "# Title\n\nJust a title and text."
    result = sections(md)
    assert result["_preamble"] == "# Title\n\nJust a title and text."
    assert len(result) == 1
