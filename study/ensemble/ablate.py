#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Score configurations of the Step 1 sentence tagger against gold.

Reads ``data/p4003r3_scores.json`` (per-sentence target/skip scores from
the two existing classifier runs) and ``data/p4003r3_gold.json`` (Claude
Opus 4.7 labels). Applies each decision-rule configuration to compute a
predicted tag per sentence, then scores predictions against gold.

A "config" here is everything except the per-sentence raw scores: which
ensemble shape (small alone / large alone / average / max-OR / asymmetric
weighted), and what ``target_margin`` / ``skip_margin`` to apply. To
ablate hypothesis labels we re-run the classifier (see
``run_alt_hypotheses.py``) producing a new scores file; this script then
treats it as another configuration.

Run with no args; prints a markdown table per config. Pipe to a file in
``results/`` if you want to keep the output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SCORES_PATH = Path(__file__).parent / "data" / "p4003r3_scores.json"
GOLD_PATH = Path(__file__).parent / "data" / "p4003r3_gold.json"

TAGS = ("TARGET", "CONTEXT", "SKIP")


# ---------------------------------------------------------------------------
# Ensemble combinators
# ---------------------------------------------------------------------------


def combine_small_only(row: dict) -> tuple[float, float]:
    return row["small"]["target"], row["small"]["skip"]


def combine_large_only(row: dict) -> tuple[float, float]:
    return row["large"]["target"], row["large"]["skip"]


def combine_average(row: dict) -> tuple[float, float]:
    """Per-label arithmetic mean across the two classifiers."""
    t = (row["small"]["target"] + row["large"]["target"]) / 2
    s = (row["small"]["skip"] + row["large"]["skip"]) / 2
    return t, s


def combine_max_target_min_skip(row: dict) -> tuple[float, float]:
    """OR-style recall combinator: TARGET takes max, SKIP takes min.

    A sentence is TARGET-likely if EITHER model says so; the SKIP
    score uses the more cautious (lower) of the two so a single model
    saying 'don't skip' wins. Maximally recall-biased.
    """
    t = max(row["small"]["target"], row["large"]["target"])
    s = min(row["small"]["skip"], row["large"]["skip"])
    return t, s


def combine_max_both(row: dict) -> tuple[float, float]:
    """Both labels take max. Symmetric OR across models."""
    t = max(row["small"]["target"], row["large"]["target"])
    s = max(row["small"]["skip"], row["large"]["skip"])
    return t, s


def combine_weighted(row: dict, w_small: float, w_large: float) -> tuple[float, float]:
    s_w = w_small / (w_small + w_large)
    l_w = w_large / (w_small + w_large)
    t = s_w * row["small"]["target"] + l_w * row["large"]["target"]
    s = s_w * row["small"]["skip"] + l_w * row["large"]["skip"]
    return t, s


# ---------------------------------------------------------------------------
# Decision rule
# ---------------------------------------------------------------------------


def decide(target: float, skip: float, target_margin: float, skip_margin: float) -> str:
    """Apply the asymmetric-margin rule used by `_tag_sentences`."""
    diff = target - skip
    if diff > target_margin:
        return "TARGET"
    if -diff > skip_margin:
        return "SKIP"
    return "CONTEXT"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass
class Score:
    name: str
    target_margin: float
    skip_margin: float
    correct: int
    total: int
    confusion: dict
    per_class: dict
    crit_target_to_skip: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


def score_config(
    name: str, rows: list[dict], gold_by_sid: dict,
    combine, target_margin: float, skip_margin: float,
) -> Score:
    confusion = {g: {p: 0 for p in TAGS} for g in TAGS}
    correct = 0
    crit = 0
    for row in rows:
        gold = gold_by_sid[row["sid"]]
        t, s = combine(row)
        pred = decide(t, s, target_margin, skip_margin)
        confusion[gold][pred] += 1
        if pred == gold:
            correct += 1
        if gold == "TARGET" and pred == "SKIP":
            crit += 1
    per_class = {}
    for tag in TAGS:
        tp = confusion[tag][tag]
        fn = sum(confusion[tag][p] for p in TAGS if p != tag)
        fp = sum(confusion[g][tag] for g in TAGS if g != tag)
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_class[tag] = {"P": precision, "R": recall, "F1": f1}
    return Score(
        name=name, target_margin=target_margin, skip_margin=skip_margin,
        correct=correct, total=len(rows), confusion=confusion,
        per_class=per_class, crit_target_to_skip=crit,
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def print_score(sc: Score) -> None:
    print(f"### {sc.name}  (target_margin={sc.target_margin}, skip_margin={sc.skip_margin})")
    print()
    print(f"- **accuracy**: {sc.accuracy:.3f} ({sc.correct}/{sc.total})")
    print(f"- **target -> skip miss-fires**: {sc.crit_target_to_skip} (HIGH-STAKES; irrecoverable)")
    print()
    print("| metric | TARGET | CONTEXT | SKIP |")
    print("| --- | --- | --- | --- |")
    for m in ("P", "R", "F1"):
        cells = " | ".join(f"{sc.per_class[t][m]:.3f}" for t in TAGS)
        print(f"| {m} | {cells} |")
    print()
    print("**Confusion** (rows=gold, cols=pred):")
    print()
    print("| gold \\ pred | TARGET | CONTEXT | SKIP |")
    print("| --- | --- | --- | --- |")
    for g in TAGS:
        cells = " | ".join(str(sc.confusion[g][p]) for p in TAGS)
        print(f"| **{g}** | {cells} |")
    print()


def main() -> None:
    rows = json.loads(SCORES_PATH.read_text(encoding="utf-8"))
    gold_data = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    gold_by_sid = {r["sid"]: r["gold"] for r in gold_data["labels"]}

    configs: list[tuple[str, callable]] = [
        ("nli-small alone", combine_small_only),
        ("zeroshot-large alone", combine_large_only),
        ("ensemble: arithmetic mean", combine_average),
        ("ensemble: max-TARGET min-SKIP (recall-biased)", combine_max_target_min_skip),
        ("ensemble: max both (symmetric OR)", combine_max_both),
        ("ensemble: weighted 0.7 small + 0.3 large",
         lambda r: combine_weighted(r, 0.7, 0.3)),
    ]

    margin_sets = [
        # Current production defaults.
        (0.05, 0.40),
        # Lower target_margin: more TARGET-biased.
        (0.00, 0.40),
        # Stricter skip: harder to SKIP.
        (0.05, 0.60),
        # Looser skip: easier to SKIP (closer to symmetric).
        (0.05, 0.20),
    ]

    print(f"# Ablation on P4003R3 ({len(rows)} sentences)")
    print()
    print(f"Gold distribution: TARGET={sum(1 for r in gold_data['labels'] if r['gold']=='TARGET')} "
          f"CONTEXT={sum(1 for r in gold_data['labels'] if r['gold']=='CONTEXT')} "
          f"SKIP={sum(1 for r in gold_data['labels'] if r['gold']=='SKIP')}")
    print()
    for cfg_name, combine in configs:
        for tm, sm in margin_sets:
            score = score_config(cfg_name, rows, gold_by_sid, combine, tm, sm)
            print_score(score)


if __name__ == "__main__":
    main()
