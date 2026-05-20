#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

from __future__ import annotations

from pipeline.markdown import extract_code_blocks, sections


def test_sections_basic():
    md = "# Title\n\n## A\n\nBody A\n\n## B\n\nBody B"
    result = sections(md)
    assert result["A"] == "Body A"
    assert result["B"] == "Body B"


def test_sections_fence_preserves_h2():
    md = (
        "## Step\n\n"
        "```jinja\n"
        "## Heading inside fence\n"
        "Content\n"
        "```\n"
    )
    result = sections(md)
    assert "Step" in result
    assert "## Heading inside fence" in result["Step"]
    assert "Heading inside fence" not in result


def test_sections_fence_preserves_hr():
    md = (
        "## Step\n\n"
        "```\n"
        "---\n"
        "still inside\n"
        "```\n"
    )
    result = sections(md)
    assert "Step" in result
    assert "---" in result["Step"]
    assert "still inside" in result["Step"]


def test_sections_fence_in_comment_zone():
    md = (
        "## Step\n\n"
        "Prompt text\n\n"
        "---\n\n"
        "```\n"
        "## Not a new section\n"
        "```\n"
        "\n## Real Next\n\nBody"
    )
    result = sections(md)
    assert "Step" in result
    assert result["Step"] == "Prompt text"
    assert "Not a new section" not in result
    assert "Real Next" in result


def test_sections_backward_compat_no_fences():
    md = (
        "## A\n\nText A\n\n---\n\nComment\n\n## B\n\nText B"
    )
    result = sections(md)
    assert result["A"] == "Text A"
    assert result["B"] == "Text B"


def test_extract_code_blocks_single():
    text = "some text\n```jinja\nHello {{ name }}\n```\nmore"
    blocks = extract_code_blocks(text)
    assert len(blocks) == 1
    assert blocks[0] == "Hello {{ name }}"


def test_extract_code_blocks_multiple():
    text = "```python\nprint(1)\n```\n\n```sql\nSELECT 1\n```"
    blocks = extract_code_blocks(text)
    assert len(blocks) == 2
    assert blocks[0] == "print(1)"
    assert blocks[1] == "SELECT 1"


def test_extract_code_blocks_none():
    text = "No code blocks here"
    blocks = extract_code_blocks(text)
    assert blocks == []


def test_extract_code_blocks_multiline():
    text = "```\nline 1\nline 2\nline 3\n```"
    blocks = extract_code_blocks(text)
    assert len(blocks) == 1
    assert blocks[0] == "line 1\nline 2\nline 3"
