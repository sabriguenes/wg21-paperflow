#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Validate the score matrix against hand-labeled ground truth.

Reads ``data/{pid}_score_matrix.json`` and ``data/{pid}_ground_truth.json``.
For each ground-truth finding, checks whether the classifier flagged the
correct source section(s) with the correct hypothesis(es).

Outputs a scorecard to ``results/{pid}_findings.md`` and prints to stdout.

Usage:
    python validate.py P2300R10
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

OUT_DIR = Path(__file__).parent
DATA_DIR = OUT_DIR / "data"
RESULTS_DIR = OUT_DIR / "results"


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: validate.py <paper_id>", file=sys.stderr)
        sys.exit(2)

    pid = sys.argv[1].upper()

    matrix_path = DATA_DIR / f"{pid.lower()}_score_matrix.json"
    gt_path = DATA_DIR / f"{pid.lower()}_ground_truth.json"

    if not matrix_path.is_file():
        print(f"missing: {matrix_path} (run section_classifier.py first)", file=sys.stderr)
        sys.exit(1)
    if not gt_path.is_file():
        print(f"missing: {gt_path} (create ground truth first)", file=sys.stderr)
        sys.exit(1)

    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    ground_truth = json.loads(gt_path.read_text(encoding="utf-8"))

    by_line: dict[int, dict] = {}
    for row in matrix:
        for line in range(row["start_line"], row["end_line"] + 1):
            by_line[line] = row

    threshold = 0.5
    hits = 0
    misses = 0
    partial = 0
    results: list[dict] = []

    for finding in ground_truth:
        fid = finding["id"]
        expected_hypos = finding.get("hypotheses", [])
        source_lines = finding.get("source_lines", [])
        finding_type = finding.get("type", "local")

        matched_sections: list[dict] = []
        for line in source_lines:
            sec = by_line.get(line)
            if sec and sec not in matched_sections:
                matched_sections.append(sec)

        hypo_hits: list[str] = []
        for sec in matched_sections:
            for hypo in expected_hypos:
                score = sec["scores"].get(hypo, 0)
                if score > threshold:
                    hypo_hits.append(f"{hypo}={score:.2f} in sec {sec['idx']}")

        if not matched_sections:
            status = "MISS-nosec"
            misses += 1
        elif not hypo_hits:
            status = "MISS-nohypo"
            misses += 1
        elif len(hypo_hits) < len(expected_hypos):
            status = "PARTIAL"
            partial += 1
        else:
            status = "HIT"
            hits += 1

        results.append({
            "id": fid,
            "status": status,
            "type": finding_type,
            "expected_hypos": expected_hypos,
            "matched": hypo_hits,
            "sections": [s["idx"] for s in matched_sections],
        })

    total = len(ground_truth)
    report_lines: list[str] = []
    report_lines.append(f"# {pid} - Validation Scorecard\n")
    report_lines.append(f"- Ground truth findings: {total}")
    report_lines.append(f"- HIT (all hypotheses fire): {hits} ({hits*100//total}%)")
    report_lines.append(f"- PARTIAL (some hypotheses fire): {partial} ({partial*100//total}%)")
    report_lines.append(f"- MISS: {misses} ({misses*100//total}%)")
    report_lines.append(f"- Threshold: {threshold}")
    report_lines.append("")

    by_type: dict[str, list] = {}
    for r in results:
        by_type.setdefault(r["type"], []).append(r)

    report_lines.append("## By Finding Type\n")
    for ftype, items in sorted(by_type.items()):
        type_hits = sum(1 for r in items if r["status"] == "HIT")
        type_partial = sum(1 for r in items if r["status"] == "PARTIAL")
        type_miss = sum(1 for r in items if r["status"].startswith("MISS"))
        report_lines.append(
            f"- **{ftype}**: {len(items)} findings - "
            f"{type_hits} hit, {type_partial} partial, {type_miss} miss"
        )
    report_lines.append("")

    report_lines.append("## Per-Finding Detail\n")
    for r in results:
        status_mark = {"HIT": "+", "PARTIAL": "~"}.get(r["status"], "-")
        matched_str = "; ".join(r["matched"][:3]) if r["matched"] else "none"
        report_lines.append(
            f"  [{status_mark}] {r['id']:<8s} ({r['type']:<12s}) "
            f"expected={r['expected_hypos']}  matched=[{matched_str}]"
        )

    report = "\n".join(report_lines)
    print(report)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{pid.lower()}_findings.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\nWrote: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
