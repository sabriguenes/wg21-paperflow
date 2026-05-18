# Copyright 2026 The wg21-paperflow authors. All rights reserved.
# Use of this software is governed by the BSL-1.0 license found in LICENSE.
"""
Audit all paperstore *.md files for Abstract section signals (tomd output shape).

Scans the full document body (after YAML front matter) with line-anchored
patterns so headings still match when a long TOC precedes the Abstract.

Usage:

  uv run --directory packages/tomd python scripts/markdown_abstract_audit.py \\
      --paperstore "C:\\path\\to\\data\\paperstore" \\
      -o markdown_abstract_audit.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

WORD_ABSTRACT = re.compile(r"\babstract\b", re.I)


def split_front_matter(raw: str) -> tuple[str, str]:
    if not raw.startswith("---"):
        return "", raw
    sep = raw.find("\n---", 3)
    if sep == -1:
        return "", raw
    fm_end = sep + 1 + 3
    return raw[:fm_end], raw[fm_end:]


def detect_patterns(fm: str, body: str) -> tuple[list[str], bool]:
    """Return (pattern_tags, has_structured_abstract_section)."""
    tags: list[str] = []
    b = body
    if re.search(r"(?mi)^#\s+abstract\b", b):
        tags.append("h1_abstract")
    if re.search(r"(?mi)^##\s+abstract\b", b):
        tags.append("h2_abstract")
    if re.search(r"(?mi)^###\s+abstract\b", b):
        tags.append("h3_abstract")
    if re.search(r"(?mi)^####\s+abstract\b", b):
        tags.append("h4_abstract")
    if re.search(r"(?mi)^abstract\s*:?\s*$", b, re.MULTILINE):
        tags.append("standalone_line_abstract")
    if re.search(r"(?mi)^\d+\s*[\.)]\s*abstract\b", b, re.MULTILINE):
        tags.append("numbered_dot_abstract")
    if re.search(r"(?mi)^\d+\s+abstract\b", b, re.MULTILINE):
        tags.append("numbered_space_abstract")
    # "1. Abstract" nested under markdown headings (`### 1. Abstract`, common in WG21 MD)
    if re.search(r"(?mi)^#{1,6}\s+\d+\s*[\.)]\s*abstract\b", b):
        tags.append("atx_heading_numbered_dot_abstract")
    if re.search(r"(?mi)^#{1,6}\s+\d+\s+abstract\b", b):
        tags.append("atx_heading_numbered_space_abstract")
    if re.search(r"(?mi)^\*\*abstract\*\*\s*$", b, re.MULTILINE):
        tags.append("bold_standalone_abstract")
    if re.search(r"(?mi)^##\s+\*\*abstract\*\*\s*$", b, re.MULTILINE):
        tags.append("h2_bold_abstract")
    if re.search(r"(?mi)^\*\*abstract\*\*[:\.]?\s+\S", b, re.MULTILINE):
        tags.append("bold_inline_abstract_opener")
    if re.search(r"(?mi)^>\s*abstract\s*:?\s*$", b, re.MULTILINE):
        tags.append("blockquote_abstract")
    if re.search(r"(?mi)^abstract\s*:\s*\S", b, re.MULTILINE):
        tags.append("inline_abstract_colon_paragraph")
    if "abstract:" in fm.lower():
        tags.append("front_matter_abstract_key")

    structured = bool(tags)
    return sorted(set(tags)), structured


def opening_word_hit(body: str, n_lines: int = 80) -> tuple[bool, str]:
    head = "\n".join(body.splitlines()[:n_lines])
    m = WORD_ABSTRACT.search(head)
    if not m:
        return False, ""
    s = max(0, m.start() - 50)
    e = min(len(head), m.end() + 80)
    return True, head[s:e].replace("\n", " ").strip()


def full_file_word_count(body: str) -> int:
    return len(WORD_ABSTRACT.findall(body))


def audit_file(path: Path) -> dict[str, object]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    fm, body = split_front_matter(raw)
    tags, structured = detect_patterns(fm, body)
    open_hit, open_snip = opening_word_hit(body, 80)
    n_words = full_file_word_count(body)
    pid = path.stem.lower()
    return {
        "pid": pid,
        "md_path": str(path.name),
        "tags": ";".join(tags),
        "has_structured_abstract_section": structured,
        "opening_has_abstract_word": open_hit,
        "opening_snippet": open_snip[:220],
        "abstract_word_count_body": n_words,
        "body_chars": len(body),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paperstore", type=Path, required=True)
    ap.add_argument("-o", "--output", type=Path, default=None)
    args = ap.parse_args(argv)

    store = args.paperstore.resolve()
    if not store.is_dir():
        print(f"Not a directory: {store}", file=sys.stderr)
        return 2

    mds = sorted(store.glob("*.md"))
    if len(mds) != 270:
        print(
            f"WARNING: expected 270 .md files, found {len(mds)}",
            file=sys.stderr,
        )

    out = args.output or (store.parent / "markdown_abstract_audit.csv")
    rows = [audit_file(p) for p in mds]

    fieldnames = [
        "pid",
        "md_path",
        "tags",
        "has_structured_abstract_section",
        "opening_has_abstract_word",
        "opening_snippet",
        "abstract_word_count_body",
        "body_chars",
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    n_struct = sum(1 for r in rows if r["has_structured_abstract_section"])
    n_open = sum(1 for r in rows if r["opening_has_abstract_word"])
    n_neither = sum(
        1
        for r in rows
        if not r["has_structured_abstract_section"] and not r["opening_has_abstract_word"]
    )
    summary = {
        "md_files_scanned": len(rows),
        "has_structured_abstract_section": n_struct,
        "opening_only_word_no_structured": sum(
            1
            for r in rows
            if r["opening_has_abstract_word"] and not r["has_structured_abstract_section"]
        ),
        "no_structured_and_no_opening_word": n_neither,
        "csv": str(out.resolve()),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
