#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""End-to-end equivalence check.

Replays the cached alt-hypothesis nli-small scores through the
RUNTIME code path (`dissect.harness._is_structural_skip` + the same
asymmetric-margin decision in `_tag_sentences`) on the 410 gold-
labeled P2300R10 sentences, and verifies we reproduce the
`final_ablation.md` "prefilter + small (alt)" numbers:

    0.676 acc, 96% TARGET recall, 92% SKIP recall, 1 T->S miss.

If this passes, dissect is provably following the ablation's Tier 1
recommendation on a real corpus.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"


def runtime_decide(target: float, skip: float,
                   target_margin: float, skip_margin: float) -> str:
    """Exact copy of the decision logic from `_tag_sentences`."""
    diff = target - skip
    if diff > target_margin:
        return "TARGET"
    if -diff > skip_margin:
        return "SKIP"
    return "CONTEXT"


def main() -> int:
    from dissect.harness import (
        _is_structural_skip,
        _DEFAULT_TARGET_MARGIN,
        _DEFAULT_SKIP_MARGIN,
    )

    alt_scores = json.loads(
        (DATA / "p2300r10_alt_scores.json").read_text(encoding="utf-8")
    )
    by_sid = {r["sid"]: r for r in alt_scores}

    gold_by_sid: dict[int, str] = {}
    for phase in (1, 2):
        gold = json.loads(
            (DATA / f"p2300r10_gold_phase{phase}.json").read_text(encoding="utf-8")
        )
        for r in gold["labels"]:
            gold_by_sid[r["sid"]] = r["gold"]

    confusion: dict[str, Counter] = {
        g: Counter() for g in ("TARGET", "CONTEXT", "SKIP")
    }
    prefilter_hits = 0
    for sid, gold in gold_by_sid.items():
        row = by_sid[sid]
        text = row["text"]
        if _is_structural_skip(text):
            pred = "SKIP"
            prefilter_hits += 1
        else:
            scores = row["nli-small"]["alt"]
            pred = runtime_decide(
                scores["target"], scores["skip"],
                _DEFAULT_TARGET_MARGIN, _DEFAULT_SKIP_MARGIN,
            )
        confusion[gold][pred] += 1

    total = sum(c for cnt in confusion.values() for c in cnt.values())
    correct = sum(confusion[g][g] for g in ("TARGET", "CONTEXT", "SKIP"))
    acc = correct / total
    t_total = sum(confusion["TARGET"].values())
    s_total = sum(confusion["SKIP"].values())
    target_recall = confusion["TARGET"]["TARGET"] / t_total
    skip_recall = confusion["SKIP"]["SKIP"] / s_total if s_total else 0
    t_to_s = confusion["TARGET"]["SKIP"]

    print(f"  prefilter caught: {prefilter_hits}/{total}")
    print(f"  acc:              {acc:.3f}  (correct {correct}/{total})")
    print(f"  TARGET recall:    {target_recall:.3f}  ({confusion['TARGET']['TARGET']}/{t_total})")
    print(f"  SKIP recall:      {skip_recall:.3f}  ({confusion['SKIP']['SKIP']}/{s_total})")
    print(f"  T->S misses:      {t_to_s}")
    print()

    expected = {
        "acc":          0.676,
        "target_recall": 0.96,
        "skip_recall":   0.92,
        "t_to_s":        1,
    }

    ok = True
    if abs(acc - expected["acc"]) > 0.001:
        print(f"DRIFT: acc {acc:.3f} != {expected['acc']}")
        ok = False
    if abs(target_recall - expected["target_recall"]) > 0.01:
        print(f"DRIFT: TARGET recall {target_recall:.3f} != {expected['target_recall']}")
        ok = False
    if abs(skip_recall - expected["skip_recall"]) > 0.01:
        print(f"DRIFT: SKIP recall {skip_recall:.3f} != {expected['skip_recall']}")
        ok = False
    if t_to_s != expected["t_to_s"]:
        print(f"DRIFT: T->S {t_to_s} != {expected['t_to_s']}")
        ok = False

    if ok:
        print("=== runtime end-to-end matches final_ablation 'prefilter + small (alt)' ===")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
