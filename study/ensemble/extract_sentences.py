#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Extract sentence spans from a converted paper using the assay chunker.

Chunks the paper identically to assay Step 1, then segments each
chunk's text into sentences with pysbd. Produces a flat JSON list
with section metadata so downstream scripts can map sentences back
to assay chunks and report which chunks the classifier would skip.

Usage:
    python extract_sentences.py P2300R10
    python extract_sentences.py P4003R3
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pysbd

OUT_DIR = Path(__file__).parent / "data"

_SEGMENTER = pysbd.Segmenter(language="en", clean=False)


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: extract_sentences.py <paper_id>", file=sys.stderr)
        sys.exit(2)

    pid = sys.argv[1].strip().upper()

    from paperstore import SqliteBackend
    from assay.chunker import chunk_paper
    from pipeline import tokens_to_chars

    backend = SqliteBackend.from_env()
    paper_md = backend.get_paper_md(pid)
    lines = paper_md.splitlines()

    sections = chunk_paper(paper_md, max_chars=tokens_to_chars(2000))
    print(f"Paper: {pid}, {len(sections)} sections", file=sys.stderr)

    sid = 0
    rows: list[dict] = []
    for sec_idx, sec in enumerate(sections):
        start = sec.start_line - 1
        end = sec.end_line
        body = "\n".join(lines[start:end])
        sentences = _SEGMENTER.segment(body)

        for sent in sentences:
            text = sent.strip()
            if not text:
                continue
            line = _find_line(lines, text, start, end)
            rows.append({
                "sid": sid,
                "line": line,
                "text": text,
                "section_idx": sec_idx,
                "section_heading": sec.heading,
            })
            sid += 1

    print(f"Extracted {len(rows)} sentences from {len(sections)} sections",
          file=sys.stderr)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{pid.lower()}_sentences.json"
    out_path.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    print(f"Wrote {out_path}", file=sys.stderr)

    txt_path = OUT_DIR / f"{pid.lower()}_sentences.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(
                f'{r["sid"]:4d} S{r["section_idx"]:>2d} L{r["line"]:>4d} | '
                f'{r["text"]}\n'
            )
    print(f"Wrote {txt_path}", file=sys.stderr)


def _find_line(
    lines: list[str], text: str, start: int, end: int,
) -> int:
    """Find the line number where a sentence starts within a section."""
    needle = text[:60]
    for i in range(start, min(end, len(lines))):
        if needle in lines[i]:
            return i + 1
    return start + 1


if __name__ == "__main__":
    main()
