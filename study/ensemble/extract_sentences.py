#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Extract sentence spans from a converted paper, mirroring dissect Step 0+1.

Runs the same blanking and pysbd decomposition that production dissect
uses, but produces a flat (sid, line, text) list we can label and
re-score. Avoids running the full dissect pipeline (which costs LLM
calls); we only need the sentence boundaries.

Usage:
    python extract_sentences.py p2300r10
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DATA_DIR = Path("c:/Users/Vinnie/wg21-data-dir/paperstore")
OUT_DIR = Path(__file__).parent / "data"


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: extract_sentences.py <paper_id>", file=sys.stderr)
        sys.exit(2)
    pid = sys.argv[1].lower()
    md_path = DATA_DIR / f"{pid}.md"
    if not md_path.is_file():
        print(f"not found: {md_path}", file=sys.stderr)
        sys.exit(1)

    from dissect.harness import (
        _blank_non_prose, _chunk_paper, _decompose_sentences,
    )

    source = md_path.read_text(encoding="utf-8")
    blanked, n_blanked = _blank_non_prose(source)
    print(f"Blanked {n_blanked} non-prose lines (of {len(source.splitlines())})",
          file=sys.stderr)

    chunks = _chunk_paper(blanked)
    print(f"Chunked into {len(chunks)} chunks", file=sys.stderr)

    sid = 0
    rows: list[dict] = []
    for chunk in chunks:
        spans = _decompose_sentences(chunk)
        for span in spans:
            rows.append({
                "sid": sid,
                "line": span.line,
                "text": span.text,
            })
            sid += 1
    print(f"Decomposed into {len(rows)} sentences", file=sys.stderr)

    out_path = OUT_DIR / f"{pid}_sentences.json"
    out_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    print(f"Wrote {out_path}", file=sys.stderr)

    txt_path = OUT_DIR / f"{pid}_sentences.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(f'{r["sid"]:4d} L{r["line"]:>4d} | {r["text"]}\n')
    print(f"Wrote {txt_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
