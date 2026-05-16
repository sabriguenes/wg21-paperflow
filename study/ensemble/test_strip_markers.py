#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Test whether stripping leading list markers fixes T->C misses.

The Phase 1 P2300R10 analysis showed that ~40% of TARGET sentences
the classifier demotes to CONTEXT carry a leading numeric or
bulleted list marker ("1. ", "- ", "* "). The classifier reads
those as a list marker and discounts the target probability.

This script re-scores those specific sentences with the marker
stripped, to see whether the marker alone was the cause.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ablate import decide  # type: ignore

LIST_MARKER = re.compile(r"^\s*(?:\d+\.|[-*])\s+")
TARGET_HYPO = "A statement of fact or opinion."
SKIP_HYPO = "A heading, list marker, or page metadata."


def main() -> None:
    pid = "p2300r10"
    small = json.loads(
        Path(f"study/ensemble/data/{pid}_nli-small_scores.json")
        .read_text(encoding="utf-8")
    )
    gold = {
        r["sid"]: r["gold"]
        for r in json.loads(
            Path(f"study/ensemble/data/{pid}_gold_phase1.json")
            .read_text(encoding="utf-8")
        )["labels"]
    }
    by_sid = {r["sid"]: r for r in small if r["sid"] in gold}

    candidates: list[dict] = []
    for sid, g in gold.items():
        if g != "TARGET":
            continue
        r = by_sid[sid]
        pred = decide(r["target"], r["skip"], 0.05, 0.6)
        if pred == "TARGET":
            continue
        if LIST_MARKER.match(r["text"]):
            candidates.append(r)

    print(f"Found {len(candidates)} list-prefixed TARGET misses to re-score",
          file=sys.stderr)

    from pipeline.services import load_classifiers, resolve_classifier_slots
    clfs, defaults = load_classifiers()
    slots = resolve_classifier_slots(clfs, defaults, {"selector": "nli-small"})
    classifier = slots["selector"]

    stripped = [LIST_MARKER.sub("", r["text"], count=1) for r in candidates]
    raw = classifier.classify(stripped, [TARGET_HYPO, SKIP_HYPO],
                              multi_label=True)

    fixed = 0
    print(f"\n{'sid':>4} {'orig_t':>7} {'orig_s':>7} -> {'new_t':>7} {'new_s':>7}  "
          f"{'orig_pred':>10} -> {'new_pred':>10}  text")
    for r, new_scores, stripped_text in zip(candidates, raw, stripped):
        new_t = new_scores[TARGET_HYPO]
        new_s = new_scores[SKIP_HYPO]
        new_pred = decide(new_t, new_s, 0.05, 0.6)
        old_pred = decide(r["target"], r["skip"], 0.05, 0.6)
        if new_pred == "TARGET":
            fixed += 1
        print(f"{r['sid']:>4} {r['target']:>7.3f} {r['skip']:>7.3f} -> "
              f"{new_t:>7.3f} {new_s:>7.3f}  "
              f"{old_pred:>10} -> {new_pred:>10}  {stripped_text[:70]}")
    print(f"\n{fixed}/{len(candidates)} now correctly tagged TARGET after "
          f"stripping list marker.")


if __name__ == "__main__":
    main()
