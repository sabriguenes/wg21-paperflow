#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Chunk a WG21 paper into semantic sections based on markdown headings.

Recursive descent: a heading "owns" lines until the next heading of the
same or higher level.  If a section exceeds *max_tokens*, it is split at
child headings.  If no children exist, it is split at paragraph
boundaries (blank lines).

Reads paper markdown from the paperstore directory.  Outputs a JSON list
of leaf sections to ``data/{pid}_sections.json`` and prints a structure
report to stdout.

Usage:
    python section_chunker.py P2300R10
    python section_chunker.py P4003R3 --max-tokens 1500
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

DATA_DIR = Path("c:/Users/Vinnie/wg21-data-dir/paperstore")
OUT_DIR = Path(__file__).parent / "data"

from pipeline.tokens import est_tokens

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")


@dataclass
class Section:
    heading: str
    level: int
    start_line: int
    end_line: int
    line_count: int
    token_est: int
    children: list[Section] = field(default_factory=list)


def _parse_headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """Return (line_index, level, title) for every markdown heading."""
    headings: list[tuple[int, int, str]] = []
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            headings.append((i, len(m.group(1)), m.group(2).strip()))
    return headings




def _build_tree(
    lines: list[str],
    headings: list[tuple[int, int, str]],
    start: int,
    end: int,
    parent_level: int,
) -> list[Section]:
    """Build a tree of sections from headings within [start, end)."""
    children_hdgs = [
        (ln, lv, t)
        for ln, lv, t in headings
        if start < ln < end and lv == parent_level + 1
    ]
    if not children_hdgs:
        return []

    sections: list[Section] = []
    for idx, (ln, lv, title) in enumerate(children_hdgs):
        child_end = children_hdgs[idx + 1][0] if idx + 1 < len(children_hdgs) else end
        sec_lines = lines[ln:child_end]
        sec = Section(
            heading=title,
            level=lv,
            start_line=ln + 1,
            end_line=child_end,
            line_count=child_end - ln,
            token_est=est_tokens("\n".join(sec_lines)),
            children=_build_tree(lines, headings, ln, child_end, lv),
        )
        sections.append(sec)
    return sections


def _flatten(sections: list[Section], max_tokens: int) -> list[Section]:
    """Flatten the tree into leaf sections, splitting large ones."""
    leaves: list[Section] = []
    for sec in sections:
        if sec.token_est <= max_tokens or not sec.children:
            leaves.append(Section(
                heading=sec.heading,
                level=sec.level,
                start_line=sec.start_line,
                end_line=sec.end_line,
                line_count=sec.line_count,
                token_est=sec.token_est,
                children=[],
            ))
        else:
            leaves.extend(_flatten(sec.children, max_tokens))
    return leaves


def _section_to_dict(sec: Section) -> dict:
    d = asdict(sec)
    del d["children"]
    return d


def _render_tree(sections: list[Section], indent: int = 0) -> str:
    """Render the section tree as indented text."""
    out: list[str] = []
    for sec in sections:
        prefix = "  " * indent
        size_tag = "LARGE" if sec.token_est > 1000 else ("MED" if sec.token_est > 500 else "small")
        hashes = "#" * sec.level
        out.append(
            f"{prefix}{hashes} {sec.heading} "
            f"({sec.start_line}-{sec.end_line}, ~{sec.token_est} tok, {size_tag})"
        )
        if sec.children:
            out.append(_render_tree(sec.children, indent + 1))
    return "\n".join(out)


def chunk_paper(pid: str, *, max_tokens: int = 2000) -> list[dict]:
    """Chunk a paper into semantic sections. Returns list of section dicts."""
    md_path = DATA_DIR / f"{pid.lower()}.md"
    if not md_path.is_file():
        raise FileNotFoundError(f"not found: {md_path}")
    source = md_path.read_text(encoding="utf-8")
    lines = source.splitlines()
    headings = _parse_headings(lines)
    top_level = min(lv for _, lv, _ in headings) if headings else 2
    tree = _build_tree(lines, headings, 0, len(lines), top_level - 1)
    leaves = _flatten(tree, max_tokens)
    return [_section_to_dict(s) for s in leaves]


def main() -> None:
    max_tokens = 2000

    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        print("usage: section_chunker.py <paper_id> [--max-tokens N]", file=sys.stderr)
        sys.exit(2)

    pid = args[0].upper()
    for i, a in enumerate(args):
        if a == "--max-tokens" and i + 1 < len(args):
            max_tokens = int(args[i + 1])

    md_path = DATA_DIR / f"{pid.lower()}.md"
    if not md_path.is_file():
        print(f"not found: {md_path}", file=sys.stderr)
        sys.exit(1)

    t0 = time.time()
    source = md_path.read_text(encoding="utf-8")
    lines = source.splitlines()
    headings = _parse_headings(lines)

    print(f"Paper: {pid} ({len(lines)} lines)", file=sys.stderr)
    print(f"Headings found: {len(headings)}", file=sys.stderr)
    print(f"Max tokens per leaf: {max_tokens}", file=sys.stderr)

    top_level = min(lv for _, lv, _ in headings) if headings else 2
    tree = _build_tree(lines, headings, 0, len(lines), top_level - 1)

    leaves = _flatten(tree, max_tokens)
    elapsed = time.time() - t0
    print(f"Leaf sections: {len(leaves)} ({elapsed:.2f}s)", file=sys.stderr)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{pid.lower()}_sections.json"
    out_data = [_section_to_dict(s) for s in leaves]
    out_path.write_text(json.dumps(out_data, indent=2), encoding="utf-8")
    print(f"Wrote: {out_path}", file=sys.stderr)

    total_tokens = sum(s.token_est for s in leaves)
    sizes = sorted([s.token_est for s in leaves])
    large = sum(1 for s in leaves if s.token_est > 1000)
    med = sum(1 for s in leaves if 500 < s.token_est <= 1000)
    small = sum(1 for s in leaves if s.token_est <= 500)

    print(f"\n# {pid} - Section Structure Report\n")
    print(f"- Lines: {len(lines)}")
    print(f"- Headings: {len(headings)}")
    print(f"- Leaf sections (max {max_tokens} tok): {len(leaves)}")
    print(f"- Total tokens: ~{total_tokens}")
    print(f"- Size distribution: {large} large (>1000), {med} medium (500-1000), {small} small (<500)")
    if sizes:
        print(f"- Median section: ~{sizes[len(sizes)//2]} tokens")
        print(f"- Largest section: ~{sizes[-1]} tokens")
        print(f"- Smallest section: ~{sizes[0]} tokens")
    print(f"\n## Section Tree\n")
    print(_render_tree(tree))


if __name__ == "__main__":
    main()
