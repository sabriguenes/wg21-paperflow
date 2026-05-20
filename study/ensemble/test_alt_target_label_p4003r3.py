#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Validate the winning P2300R10 hypothesis doesn't regress P4003R3.

If a hypothesis tuned on one paper degrades on another, the design
doesn't transfer and we cannot ship the change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ablate import decide  # type: ignore

CANDIDATES = [
    "A statement of fact or opinion.",  # baseline
    "A statement about software behavior, design, or specification.",  # P2300 winner on accuracy
    "A statement describing what something does, is, or proposes.",  # highest recall on P2300
    "An assertion of fact, design choice, or behavior.",
]

SKIP_HYPO = "A heading, list marker, or page metadata."


def main() -> None:
    pid = "p4003r3"
    data_dir = Path(__file__).parent / "data"
    sentences_path = data_dir / f"{pid}_sentences.json"
    if not sentences_path.is_file():
        sentences_path = data_dir / f"{pid}_scores.json"
    sentences = json.loads(sentences_path.read_text(encoding="utf-8"))
    gold = {
        r["sid"]: r["gold"]
        for r in json.loads(
            (data_dir / f"{pid}_gold.json").read_text(encoding="utf-8")
        )["labels"]
    }
    texts = [s["text"] for s in sentences]
    sid_order = [s["sid"] for s in sentences]

    from pipeline.services import load_classifiers, resolve_classifier_slots
    clfs, defaults = load_classifiers()
    slots = resolve_classifier_slots(clfs, defaults, {"selector": "nli-small"})
    classifier = slots["selector"]

    print("# Cross-paper validation: alt target labels on P4003R3\n")
    g_t = sum(1 for v in gold.values() if v == "TARGET")
    g_c = sum(1 for v in gold.values() if v == "CONTEXT")
    g_s = sum(1 for v in gold.values() if v == "SKIP")
    print(f"P4003R3: {len(texts)} sentences, gold T/C/S = {g_t}/{g_c}/{g_s}")
    print(f"SKIP hypothesis (fixed): {SKIP_HYPO!r}\n")

    print("| TARGET hypothesis | acc (0.05/0.6) | T->S | T->C | TARGET recall |")
    print("| --- | --- | --- | --- | --- |")

    for hypo in CANDIDATES:
        raw = classifier.classify(texts, [hypo, SKIP_HYPO], multi_label=True)
        correct = 0
        t2s = 0
        t2c = 0
        target_hit = 0
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
        acc = correct / len(texts)
        print(f"| {hypo} | {acc:.3f} | {t2s} | {t2c} | "
              f"{target_hit}/{g_t} ({100*target_hit/g_t:.0f}%) |")


if __name__ == "__main__":
    main()
