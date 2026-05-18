#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Test broader TARGET hypothesis labels on P2300R10 Phase 1.

The Phase 1 evidence shows nli-small misses ~50 TARGETs as CONTEXT
on P2300R10. The misses are dominated by implementation-description
sentences ("Defines a sender that...", "Customizes X to do Y"). The
baseline hypothesis "A statement of fact or opinion." does not entail
those well.

We try alternative TARGET hypotheses that more inclusively cover
specification/implementation claims, then re-score the whole Phase 1
set and report accuracy.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ablate import decide  # type: ignore

CANDIDATES = [
    "A statement of fact or opinion.",
    "A claim, proposal, or assertion.",
    "A statement about software behavior, design, or specification.",
    "A statement describing what something does, is, or proposes.",
    "An assertion of fact, design choice, or behavior.",
]

SKIP_HYPO = "A heading, list marker, or page metadata."


def main() -> None:
    pid = "p2300r10"
    sentences = json.loads(
        Path(f"study/ensemble/data/{pid}_sentences.json")
        .read_text(encoding="utf-8")
    )
    gold = {
        r["sid"]: r["gold"]
        for r in json.loads(
            Path(f"study/ensemble/data/{pid}_gold_phase1.json")
            .read_text(encoding="utf-8")
        )["labels"]
    }
    texts = [s["text"] for s in sentences if s["sid"] in gold]
    sid_order = [s["sid"] for s in sentences if s["sid"] in gold]

    from pipeline.services import load_classifiers, resolve_classifier_slots
    clfs, defaults = load_classifiers()
    slots = resolve_classifier_slots(clfs, defaults, {"selector": "nli-small"})
    classifier = slots["selector"]

    print("# Alternative TARGET hypothesis test (nli-small, P2300R10 Phase 1)\n")
    print(f"Phase 1: {len(texts)} sentences, "
          f"gold T/C/S = "
          f"{sum(1 for v in gold.values() if v == 'TARGET')}/"
          f"{sum(1 for v in gold.values() if v == 'CONTEXT')}/"
          f"{sum(1 for v in gold.values() if v == 'SKIP')}")
    print(f"SKIP hypothesis (fixed): {SKIP_HYPO!r}\n")

    print("| TARGET hypothesis | acc (0.05/0.6) | T->S | T->C | TARGET recall |")
    print("| --- | --- | --- | --- | --- |")

    for hypo in CANDIDATES:
        raw = classifier.classify(texts, [hypo, SKIP_HYPO], multi_label=True)
        correct = 0
        t2s = 0
        t2c = 0
        target_hit = 0
        target_total = sum(1 for v in gold.values() if v == "TARGET")
        for sid, scores in zip(sid_order, raw):
            t = scores[hypo]
            s = scores[SKIP_HYPO]
            pred = decide(t, s, 0.05, 0.6)
            g = gold[sid]
            if pred == g:
                correct += 1
            if g == "TARGET":
                if pred == "TARGET":
                    target_hit += 1
                elif pred == "CONTEXT":
                    t2c += 1
                elif pred == "SKIP":
                    t2s += 1
        recall = target_hit / target_total
        acc = correct / len(texts)
        print(f"| {hypo} | {acc:.3f} | {t2s} | {t2c} | "
              f"{target_hit}/{target_total} ({100*recall:.0f}%) |")


if __name__ == "__main__":
    main()
