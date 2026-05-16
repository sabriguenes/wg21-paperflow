#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Definitive cascade-vs-single ablation, both hypotheses, both phases.

Reads ``data/p2300r10_alt_scores.json`` (produced by
score_labeled_subset.py) and the Phase 1 + Phase 2 gold files. Reports
each (corpus, classifier-shape, hypothesis, margin) combination so we
can answer the closing-the-loop question: does the cascade still pay
its compute cost once the new hypothesis is in?
"""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))
from ablate import decide  # type: ignore

ROOT = Path(__file__).parent
DATA = ROOT / "data"

# Same prefilter as proposed_design.md.
NUMBER_ONLY = re.compile(r"^\s*\d+\.\s*$")
ELLIPSIS_PREFIX = re.compile(r"^\s*\.{2,}\s")
PUNCT_ONLY = re.compile(r"^[\W\d]+$", re.UNICODE)
EXAMPLE_BLOCK = re.compile(r"^\[\*Example\b.*\*end example\*\]", re.DOTALL)


def is_structural_skip(text: str) -> bool:
    t = text.strip()
    if NUMBER_ONLY.match(t): return True
    if ELLIPSIS_PREFIX.match(t): return True
    if PUNCT_ONLY.match(t): return True
    if len(t.split()) < 3: return True
    if EXAMPLE_BLOCK.match(t): return True
    return False


def cascade_pred(small_t, small_s, large_t, large_s,
                 tm=0.05, sm=0.40,
                 small_t_thresh=0.50, small_s_thresh=0.70, small_margin=0.30):
    """Cascade decision: small fast-path, mean on ambiguous."""
    if small_t > small_t_thresh and small_t - small_s > small_margin:
        return decide(small_t, small_s, tm, sm), "small"
    if small_s > small_s_thresh and small_s - small_t > small_margin:
        return decide(small_t, small_s, tm, sm), "small"
    return (decide((small_t + large_t) / 2, (small_s + large_s) / 2, tm, sm),
            "both")


def main() -> None:
    scores = json.loads((DATA / "p2300r10_alt_scores.json").read_text(encoding="utf-8"))
    by_sid = {r["sid"]: r for r in scores}

    gold_all: dict[int, str] = {}
    phase_map: dict[int, int] = {}
    for phase in (1, 2):
        gold = json.loads(
            (DATA / f"p2300r10_gold_phase{phase}.json").read_text(encoding="utf-8")
        )
        for r in gold["labels"]:
            gold_all[r["sid"]] = r["gold"]
            phase_map[r["sid"]] = phase

    phase_sids = {1: [s for s, p in phase_map.items() if p == 1],
                  2: [s for s, p in phase_map.items() if p == 2],
                  "all": list(gold_all.keys())}

    def score_one(name, sids, get_pred):
        from collections import Counter
        confusion = {g: Counter() for g in ("TARGET", "CONTEXT", "SKIP")}
        oracle_calls = 0
        total = len(sids)
        for sid in sids:
            row = by_sid[sid]
            g = gold_all[sid]
            pred, mode = get_pred(row)
            confusion[g][pred] += 1
            if mode == "both" or mode == "large":
                oracle_calls += 1
        correct = sum(confusion[g][g] for g in ("TARGET", "CONTEXT", "SKIP"))
        t2s = confusion["TARGET"]["SKIP"]
        target_total = sum(confusion["TARGET"].values())
        target_hit = confusion["TARGET"]["TARGET"]
        skip_total = sum(confusion["SKIP"].values())
        skip_hit = confusion["SKIP"]["SKIP"]
        return {
            "acc": correct / total,
            "t2s": t2s,
            "target_recall": target_hit / target_total if target_total else 0,
            "skip_recall": skip_hit / skip_total if skip_total else 0,
            "oracle_calls": oracle_calls,
            "total": total,
        }

    # Configurations to test.
    def small_only(which, row):
        scores_ = row["nli-small"][which]
        return decide(scores_["target"], scores_["skip"], 0.05, 0.40), "small"

    def large_only(which, row):
        scores_ = row["zeroshot-large"][which]
        return decide(scores_["target"], scores_["skip"], 0.05, 0.40), "large"

    def cascade(which, row):
        s = row["nli-small"][which]
        l = row["zeroshot-large"][which]
        return cascade_pred(s["target"], s["skip"], l["target"], l["skip"])

    def filter_then_small(which, row):
        if is_structural_skip(row["text"]):
            return "SKIP", "filter"
        return small_only(which, row)

    def filter_then_large(which, row):
        if is_structural_skip(row["text"]):
            return "SKIP", "filter"
        return large_only(which, row)

    def filter_then_cascade(which, row):
        if is_structural_skip(row["text"]):
            return "SKIP", "filter"
        return cascade(which, row)

    print("# Final cascade-vs-single ablation\n")
    print(f"Corpus: P2300R10 Phase 1 + Phase 2  (n=410)\n")

    for which in ("baseline", "alt"):
        hypo_label = (
            'baseline ("A statement of fact or opinion.")'
            if which == "baseline" else
            'alt ("A statement describing what something does, is, or proposes.")'
        )
        print(f"## TARGET hypothesis: {hypo_label}\n")
        print("| config | corpus | acc | T→S | TARGET rec | SKIP rec | oracle calls |")
        print("| --- | --- | --: | --: | --: | --: | --: |")
        for corpus_label, sids in (
            ("Phase 1 (prose)", phase_sids[1]),
            ("Phase 2 (wording)", phase_sids[2]),
            ("ALL", phase_sids["all"]),
        ):
            for cfg_name, fn in (
                ("nli-small alone",       lambda r, w=which: small_only(w, r)),
                ("zeroshot-large alone",  lambda r, w=which: large_only(w, r)),
                ("cascade",               lambda r, w=which: cascade(w, r)),
                ("prefilter + small",     lambda r, w=which: filter_then_small(w, r)),
                ("prefilter + large",     lambda r, w=which: filter_then_large(w, r)),
                ("prefilter + cascade",   lambda r, w=which: filter_then_cascade(w, r)),
            ):
                r = score_one(cfg_name, sids, fn)
                print(f"| {cfg_name} | {corpus_label} | {r['acc']:.3f} | "
                      f"{r['t2s']} | {100*r['target_recall']:.0f}% | "
                      f"{100*r['skip_recall']:.0f}% | "
                      f"{r['oracle_calls']}/{r['total']} |")
        print()


if __name__ == "__main__":
    main()
