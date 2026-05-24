#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Markdown utilities for pipeline prompt files and rendered output.

Three concerns:

- ``sections(source)`` splits a markdown document on H2 boundaries into
  a ``dict[str, str]``. Used to parse prompt files (``agora.md``,
  ``assay.md``) into step sections.
  Fenced code blocks are preserved intact (``## `` and ``---`` inside
  fences do not trigger splits).
- ``extract_code_blocks(text)`` pulls the raw content out of fenced
  code blocks within a section string.  Used to retrieve embedded
  templates (e.g. Jinja report templates) from section bodies.
- ``sanitize_md(text)`` escapes markdown-sensitive characters in prose
  while preserving inline code spans and fenced code blocks. Used by
  every render module to produce safe markdown output.
"""

from __future__ import annotations

import re
from pathlib import Path

_PREAMBLE_KEY = "_preamble"
_CODE_SPAN_RE = re.compile(r'``.+?``|`[^`]+`')


# -- Section splitter ---------------------------------------------------------


def sections(source: str | Path) -> dict[str, str]:
    """Split a markdown document into sections keyed by H2 header text.

    ``source`` is either a string of markdown content or a ``Path`` to a
    markdown file (read as UTF-8).

    Returns a dict where each key is the text of an ``## `` header
    (without the leading ``## ``). The value is the raw markdown body
    below that header, up to the next ``## `` or ``---`` horizontal
    rule, whichever comes first. Text between a ``---`` and the next
    ``## `` is discarded.

    Fenced code blocks (delimited by lines starting with ````` ``` `````)
    are preserved verbatim. Neither ``## `` nor ``---`` triggers a split
    while inside a fenced block - this applies in both the prompt zone
    (above ``---``) and the documentation zone (below ``---``).

    The special key ``"_preamble"`` holds any text before the first
    ``## `` (typically the H1 title, subtitle, mermaid diagram, etc.).
    """
    if isinstance(source, Path):
        text = source.read_text(encoding="utf-8")
    else:
        text = source

    result: dict[str, str] = {}
    current_key: str = _PREAMBLE_KEY
    lines: list[str] = []
    skipping = False
    in_fence = False

    for line in text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            lines.append(line)
            continue

        if in_fence:
            lines.append(line)
            continue

        if line.startswith("## "):
            if not skipping:
                _flush(result, current_key, lines)
            else:
                skipping = False
                lines = []
            current_key = line[3:].strip()
            continue

        if skipping:
            continue

        if line.strip() == "---":
            _flush(result, current_key, lines)
            skipping = True
            continue

        lines.append(line)

    if not skipping:
        _flush(result, current_key, lines)

    return result


def _flush(result: dict[str, str], key: str, lines: list[str]) -> None:
    body = "\n".join(lines).strip()
    if body or key != _PREAMBLE_KEY:
        result[key] = body
    lines.clear()


# -- Code block extraction ----------------------------------------------------


def extract_code_blocks(text: str) -> list[str]:
    """Extract the raw content of each fenced code block in *text*.

    Returns a list of strings, one per fenced block found.  The opening
    and closing fence lines (including any info string like ``jinja``)
    are stripped - only the interior lines are returned, joined with
    newlines.

    Useful for pulling embedded templates or structured data out of a
    section body that was parsed by :func:`sections`.
    """
    blocks: list[str] = []
    current: list[str] | None = None

    for line in text.splitlines():
        if line.startswith("```"):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current))
                current = None
        elif current is not None:
            current.append(line)

    return blocks


# -- Markdown sanitizer -------------------------------------------------------


def sanitize_md(text: str) -> str:
    """Sanitize text for safe markdown embedding.

    Splits on balanced inline code spans and triple-backtick fences.
    Code spans pass through unchanged.  Prose segments get ``<``, ``>``,
    ``|``, leading ``#``, and unbalanced emphasis markers escaped.
    """
    if '```' in text:
        parts = text.split('```')
        result = _sanitize_inline(parts[0].rstrip())
        for i in range(1, len(parts), 2):
            code = parts[i].strip()
            result += f'\n\n```\n{code}\n```'
            if i + 1 < len(parts) and parts[i + 1].strip():
                result += f'\n\n{_sanitize_inline(parts[i + 1].strip())}'
        return result
    return _sanitize_inline(text)


def _sanitize_inline(text: str) -> str:
    """Escape prose segments while preserving inline code spans."""
    segments: list[str] = []
    last = 0
    for m in _CODE_SPAN_RE.finditer(text):
        if m.start() > last:
            segments.append(_escape_md_chars(text[last:m.start()]))
        segments.append(m.group())
        last = m.end()
    if last < len(text):
        segments.append(_escape_md_chars(text[last:]))
    return ''.join(segments)


def _escape_md_chars(text: str) -> str:
    """Escape markdown-sensitive characters in prose text."""
    text = text.replace('<', r'\<').replace('>', r'\>')
    text = text.replace('|', r'\|')
    text = re.sub(r'^(\s*)(#)', r'\1\\\2', text, flags=re.MULTILINE)
    for double in ('**', '__'):
        if text.count(double) % 2 != 0:
            text = text.replace(double, '\\' + double)
    for single in ('*', '_'):
        double = single * 2
        esc_double = '\\' + double
        temp = text.replace(esc_double, '\x00\x00\x00')
        temp = temp.replace(double, '\x00\x00')
        temp = temp.replace('\\' + single, '\x00\x00')
        count = temp.count(single)
        if count % 2 != 0:
            parts: list[str] = []
            i = 0
            while i < len(text):
                if text[i:i + 3] == esc_double:
                    parts.append(text[i:i + 3])
                    i += 3
                elif text[i:i + 2] == double:
                    parts.append(text[i:i + 2])
                    i += 2
                elif text[i:i + 2] == '\\' + single:
                    parts.append(text[i:i + 2])
                    i += 2
                elif text[i] == single:
                    parts.append('\\' + single)
                    i += 1
                else:
                    parts.append(text[i])
                    i += 1
            text = ''.join(parts)
    return text
