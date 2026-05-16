#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Score gold labels against per-classifier scores for a paper / phase.

Mirrors ablate.py but consumes per-classifier scores produced by
score_paper.py (one classifier per file) rather than the dual-classifier
format of p4003r3_scores.json. Restricts to the sids the gold file
covers (supports Phase 1 / 2 / ... partial labeling).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ablate import decide, TAGS, score_config  # type: ignore

ROOT = Path(__file__).parent
DATA = ROOT / "data"


def load_classifier(pid: str, name: str, sids: set[int]) -> dict[int, dict[str, float]]:
    path = DATA / f"{pid}_{name}_scores.json"
    if not path.is_file():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {r["sid"]: {"target": r["target"], "skip": r["skip"]}
            for r in rows if r["sid"] in sids}


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: score_phase.py <gold_json>", file=sys.stderr)
        sys.exit(2)
    gold_path = Path(sys.argv[1])
    gold_data = json.loads(gold_path.read_text(encoding="utf-8"))
    pid = gold_data["paper_id"].lower()
    gold_by_sid = {r["sid"]: r["gold"] for r in gold_data["labels"]}
    sids = set(gold_by_sid)

    small = load_classifier(pid, "nli-small", sids)
    large = load_classifier(pid, "zeroshot-large", sids)
    have_small = len(small) == len(sids)
    have_large = len(large) == len(sids)
    print(f"# {pid} phase ablation  ({len(sids)} sentences)")
    print(f"\nGold: " + ", ".join(
        f"{tag}={sum(1 for v in gold_by_sid.values() if v == tag)}"
        for tag in TAGS
    ))
    print(f"\nClassifier coverage:  nli-small={have_small} ({len(small)}/{len(sids)})"
          f"  zeroshot-large={have_large} ({len(large)}/{len(sids)})\n")

    if not have_small:
        print("Need nli-small scores; run score_paper.py first.", file=sys.stderr)
        sys.exit(1)

    margin_sets = [(0.05, 0.40), (0.05, 0.60), (0.00, 0.40)]

    rows = []
    for sid in sorted(sids):
        r = {"sid": sid}
        if sid in small:
            r["small"] = small[sid]
        if sid in large:
            r["large"] = large[sid]
        rows.append(r)

    summary = []

    def run(name, combine, rows_in):
        for tm, sm in margin_sets:
            sc = score_config(name, rows_in, gold_by_sid, combine, tm, sm)
            summary.append((name, tm, sm, sc))

    run("nli-small alone",
        lambda r: (r["small"]["target"], r["small"]["skip"]), rows)

    if have_large:
        run("zeroshot-large alone",
            lambda r: (r["large"]["target"], r["large"]["skip"]), rows)
        run("ensemble arithmetic mean",
            lambda r: ((r["small"]["target"] + r["large"]["target"]) / 2,
                       (r["small"]["skip"] + r["large"]["skip"]) / 2), rows)

        SMALL_T_THRESH = 0.50
        SMALL_S_THRESH = 0.70
        SMALL_MARGIN = 0.30

        def cascade(r):
            t_s = r["small"]["target"]
            s_s = r["small"]["skip"]
            if t_s > SMALL_T_THRESH and t_s - s_s > SMALL_MARGIN:
                return t_s, s_s
            if s_s > SMALL_S_THRESH and s_s - t_s > SMALL_MARGIN:
                return t_s, s_s
            return (
                (t_s + r["large"]["target"]) / 2,
                (s_s + r["large"]["skip"]) / 2,
            )

        run("cascade (small fast-path, mean on ambiguous)", cascade, rows)

    print("| classifier / ensemble | margin (T, S) | acc | T->S | T->C | S->T | F1_T | F1_C | F1_S |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for name, tm, sm, sc in summary:
        ttc = sc.confusion["TARGET"]["CONTEXT"]
        stt = sc.confusion["SKIP"]["TARGET"]
        print(f"| {name} | ({tm}, {sm}) | "
              f"{sc.accuracy:.3f} | {sc.crit_target_to_skip} | "
              f"{ttc} | {stt} | "
              f"{sc.per_class['TARGET']['F1']:.3f} | "
              f"{sc.per_class['CONTEXT']['F1']:.3f} | "
              f"{sc.per_class['SKIP']['F1']:.3f} |")


if __name__ == "__main__":
    main()
