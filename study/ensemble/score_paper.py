#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Score sentences and report per-chunk KEEP/SKIP decisions.

Reads ``data/<paper>_sentences.json`` (from extract_sentences.py),
scores every sentence with the classifier, then groups by section
and reports which assay chunks the classifier would skip.

Usage:
    python score_paper.py P2300R10 nli-small
    python score_paper.py P4003R3 nli-small
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

DATA = Path(__file__).parent / "data"

TARGET_HYPO = "A statement describing what something does, is, or proposes."
SKIP_HYPO = "A heading, list marker, or page metadata."
SKIP_THRESHOLD = 0.40


def main() -> None:
    if len(sys.argv) < 2:
        print(
            "usage: score_paper.py <paper_id> [classifier_name]",
            file=sys.stderr,
        )
        sys.exit(2)
    pid = sys.argv[1].strip().upper()
    selector = sys.argv[2] if len(sys.argv) > 2 else "nli-small"

    sentences_path = DATA / f"{pid.lower()}_sentences.json"
    if not sentences_path.is_file():
        print(
            f"missing: {sentences_path} (run extract_sentences.py first)",
            file=sys.stderr,
        )
        sys.exit(1)
    rows = json.loads(sentences_path.read_text(encoding="utf-8"))
    texts = [r["text"] for r in rows]

    from pipeline.services import load_classifiers, resolve_classifier_slots

    print(f"Loading classifier '{selector}'...", file=sys.stderr)
    clfs, defaults = load_classifiers()
    slots = resolve_classifier_slots(clfs, defaults, {"selector": selector})
    classifier = slots["selector"]

    print(f"Scoring {len(texts)} sentences...", file=sys.stderr)
    t0 = time.time()
    raw = classifier.classify(texts, [TARGET_HYPO, SKIP_HYPO], multi_label=True)
    elapsed = time.time() - t0
    print(
        f"Done in {elapsed:.1f}s ({elapsed / len(texts) * 1000:.1f}ms/sentence)",
        file=sys.stderr,
    )

    out_rows: list[dict] = []
    for r, scores in zip(rows, raw):
        out_rows.append({
            "sid": r["sid"],
            "line": r["line"],
            "text": r["text"],
            "section_idx": r.get("section_idx", -1),
            "section_heading": r.get("section_heading", ""),
            "target": round(float(scores[TARGET_HYPO]), 4),
            "skip": round(float(scores[SKIP_HYPO]), 4),
        })

    # Per-sentence scores JSON
    out_path = DATA / f"{pid.lower()}_{selector}_scores.json"
    out_path.write_text(
        json.dumps(out_rows, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    print(f"Wrote {out_path}", file=sys.stderr)

    # Chunk skip report
    sections: dict[int, dict] = {}
    for r in out_rows:
        si = r["section_idx"]
        if si < 0:
            continue
        if si not in sections:
            sections[si] = {
                "heading": r["section_heading"],
                "max_target": 0.0,
                "sentences": 0,
                "target_hits": 0,
            }
        sec = sections[si]
        sec["sentences"] += 1
        if r["target"] > sec["max_target"]:
            sec["max_target"] = r["target"]
        if r["target"] > SKIP_THRESHOLD:
            sec["target_hits"] += 1

    print(f"\n## Chunk Skip Report ({pid}, {selector})\n", file=sys.stderr)
    print(
        f"{'Idx':>3s}  {'Decision':8s}  {'Max':>5s}  {'Hits':>4s}  "
        f"{'Sent':>4s}  Heading",
        file=sys.stderr,
    )
    print("-" * 72, file=sys.stderr)

    keep_count = 0
    skip_count = 0
    for si in sorted(sections.keys()):
        sec = sections[si]
        decision = "KEEP" if sec["max_target"] > SKIP_THRESHOLD else "SKIP"
        if decision == "KEEP":
            keep_count += 1
        else:
            skip_count += 1
        heading = sec["heading"][:45]
        print(
            f"{si:3d}  {decision:8s}  {sec['max_target']:5.2f}  "
            f"{sec['target_hits']:4d}  {sec['sentences']:4d}  {heading}",
            file=sys.stderr,
        )

    total = keep_count + skip_count
    print(
        f"\nKEEP: {keep_count}/{total}  SKIP: {skip_count}/{total}  "
        f"({skip_count / total * 100:.0f}% reduction)" if total else "",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
