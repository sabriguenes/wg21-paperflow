#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Score every alt-hypothesis variant against gold using the same decision rule.

Reuses ``ablate.decide`` so we can sweep both axes -- hypothesis
phrasing AND margin -- and see which combo wins.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ablate import decide, TAGS, score_config  # type: ignore

ROOT = Path(__file__).parent
ALT_DIR = ROOT / "data" / "alt_hypothesis_scores"
GOLD_PATH = ROOT / "data" / "p4003r3_gold.json"


def main() -> None:
    gold_data = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    gold_by_sid = {r["sid"]: r["gold"] for r in gold_data["labels"]}

    margin_sets = [
        (0.05, 0.40),
        (0.05, 0.60),
        (0.00, 0.40),
    ]

    print("# Alt-hypothesis ablation\n")
    summary: list[tuple[float, int, float, float, str, str, tuple[float, float]]] = []

    variant_files = sorted(ALT_DIR.rglob("*.json"))
    for variant_file in variant_files:
        data = json.loads(variant_file.read_text(encoding="utf-8"))
        variant_id = data["variant"]
        clf = data.get("classifier", variant_file.parent.name)
        rows = [
            {
                "sid": r["sid"],
                "small": {"target": r["target"], "skip": r["skip"]},
                "large": {"target": r["target"], "skip": r["skip"]},
            }
            for r in data["rows"]
        ]

        print(f"## {clf} :: {variant_id}\n")
        print(f"- target hypotheses: {data['target_hypotheses']}")
        print(f"- skip hypotheses:   {data['skip_hypotheses']}\n")

        for tm, sm in margin_sets:
            sc = score_config(
                f"{clf}/{variant_id}", rows, gold_by_sid,
                lambda r: (r["small"]["target"], r["small"]["skip"]),
                tm, sm,
            )
            print(f"  margin ({tm}, {sm}): acc={sc.accuracy:.3f}  "
                  f"T->S miss={sc.crit_target_to_skip}  "
                  f"F1 T/C/S = "
                  f"{sc.per_class['TARGET']['F1']:.3f}/"
                  f"{sc.per_class['CONTEXT']['F1']:.3f}/"
                  f"{sc.per_class['SKIP']['F1']:.3f}")
            summary.append((
                sc.accuracy, sc.crit_target_to_skip,
                sc.per_class["TARGET"]["F1"],
                sc.per_class["SKIP"]["F1"],
                clf, variant_id, (tm, sm),
            ))
        print()

    print("\n## Sorted summary (by accuracy desc, T->S asc)\n")
    summary.sort(key=lambda r: (-r[0], r[1]))
    print(f"| acc | T->S miss | F1 T | F1 S | classifier | variant | margin |")
    print(f"| --- | --- | --- | --- | --- | --- | --- |")
    for acc, crit, ft, fs, clf, vid, (tm, sm) in summary:
        print(f"| {acc:.3f} | {crit} | {ft:.3f} | {fs:.3f} | "
              f"{clf} | {vid} | ({tm}, {sm}) |")


if __name__ == "__main__":
    main()
