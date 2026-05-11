"""General-purpose markdown section splitter.

Splits a markdown document on H2 (``## ``) boundaries and returns a
dict mapping each header's text to the raw body below it. Horizontal
rules (``---``) terminate the current section; text between a rule and
the next H2 is discarded.

This module is domain-free: it knows nothing about review pipelines,
LLM prompts, or WG21 papers. Keep it that way.
"""

from __future__ import annotations

from pathlib import Path

_PREAMBLE_KEY = "_preamble"


def sections(source: str | Path) -> dict[str, str]:
    """Split a markdown document into sections keyed by H2 header text.

    ``source`` is either a string of markdown content or a ``Path`` to a
    markdown file (read as UTF-8).

    Returns a dict where each key is the text of an ``## `` header
    (without the leading ``## ``). The value is the raw markdown body
    below that header, up to the next ``## `` or ``---`` horizontal
    rule, whichever comes first. Text between a ``---`` and the next
    ``## `` is discarded.

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

    for line in text.splitlines():
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
