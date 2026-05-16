#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Score every sentence in <paper>_sentences.json with one classifier.

Writes ``data/<paper>_<classifier>_scores.json`` with rows
``{sid, line, text, target, skip}``. Use the same hypothesis labels
the production tagger uses (baseline). No alt-hypothesis variants
here -- this is the per-paper "raw scores" data file the rest of the
study consumes.

Usage:
    python score_paper.py p2300r10 nli-small
    python score_paper.py p2300r10 zeroshot-large
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"

TARGET_HYPO = "A statement of fact or opinion."
SKIP_HYPO = "A heading, list marker, or page metadata."


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: score_paper.py <paper_id> <classifier_name>", file=sys.stderr)
        sys.exit(2)
    pid = sys.argv[1].lower()
    selector = sys.argv[2]

    sentences_path = DATA / f"{pid}_sentences.json"
    if not sentences_path.is_file():
        print(f"missing: {sentences_path} (run extract_sentences.py first)",
              file=sys.stderr)
        sys.exit(1)
    rows = json.loads(sentences_path.read_text(encoding="utf-8"))
    texts = [r["text"] for r in rows]

    from pipeline.services import load_classifiers, resolve_classifier_slots

    print(f"Loading classifier '{selector}'...", file=sys.stderr)
    clfs, defaults = load_classifiers()
    slots = resolve_classifier_slots(clfs, defaults, {"selector": selector})
    classifier = slots["selector"]

    print(f"Scoring {len(texts)} sentences (one batched call)...", file=sys.stderr)
    t0 = time.time()
    raw = classifier.classify(texts, [TARGET_HYPO, SKIP_HYPO], multi_label=True)
    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s ({elapsed/len(texts)*1000:.1f}ms/sentence)",
          file=sys.stderr)

    out_rows: list[dict] = []
    for r, scores in zip(rows, raw):
        out_rows.append({
            "sid": r["sid"], "line": r["line"], "text": r["text"],
            "target": float(scores[TARGET_HYPO]),
            "skip": float(scores[SKIP_HYPO]),
        })

    out_path = DATA / f"{pid}_{selector}_scores.json"
    out_path.write_text(json.dumps(out_rows, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    print(f"Wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
