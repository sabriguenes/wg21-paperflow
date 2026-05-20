#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Cross-reference classifier score matrix into finding candidates.

Reads the score matrix from section-chunks and the paper markdown from
paperstore.  Applies deterministic rules to produce finding candidates
and per-lens section groups for downstream LLM analysis.

Usage:
    python study/red-team/cross_reference.py P2300R10
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

DATA_DIR = Path("c:/Users/Vinnie/wg21-data-dir/paperstore")
CHUNKS_DATA = Path(__file__).parent.parent / "section-chunks" / "data"
OUT_DIR = Path(__file__).parent / "data"

THRESHOLD = 0.5

LENS_HYPOTHESES = {
    "performance": ["perf-claim", "measurement"],
    "design": ["design-goal", "design-rationale", "limitation"],
    "specification": ["wording"],
    "usability": ["example", "design-rationale"],
    "ecosystem": ["deferral", "limitation", "comparison"],
}


def _load_section_text(pid: str, sec: dict) -> str:
    md_path = DATA_DIR / f"{pid.lower()}.md"
    lines = md_path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[sec["start_line"] - 1 : sec["end_line"]])


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: cross_reference.py <paper_id>", file=sys.stderr)
        sys.exit(2)

    pid = sys.argv[1].upper()
    matrix_path = CHUNKS_DATA / f"{pid.lower()}_score_matrix.json"
    if not matrix_path.is_file():
        print(f"missing: {matrix_path}", file=sys.stderr)
        print("Run section_chunker.py and section_classifier.py first.", file=sys.stderr)
        sys.exit(1)

    t0 = time.time()
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    print(f"Paper: {pid}, {len(matrix)} sections", file=sys.stderr)

    candidates: list[dict] = []

    # --- Rule 1: Absence detection ---
    perf_sections = [s for s in matrix if s["scores"].get("perf-claim", 0) > THRESHOLD]
    meas_sections = [s for s in matrix if s["scores"].get("measurement", 0) > THRESHOLD]
    if len(perf_sections) >= 2 and len(meas_sections) < 2:
        candidates.append({
            "rule": "absence",
            "label": "performance-without-benchmarks",
            "detail": f"{len(perf_sections)} sections make performance claims, {len(meas_sections)} contain benchmark data",
            "perf_sections": [s["idx"] for s in perf_sections],
            "meas_sections": [s["idx"] for s in meas_sections],
        })

    # --- Rule 2: Promise extraction ---
    goal_sections = [s for s in matrix if s["scores"].get("design-goal", 0) > THRESHOLD]
    promises: list[dict] = []
    for s in goal_sections:
        text = _load_section_text(pid, s)
        promises.append({
            "section_idx": s["idx"],
            "heading": s["heading"],
            "start_line": s["start_line"],
            "text_preview": text[:500],
        })
    if promises:
        candidates.append({
            "rule": "promises",
            "label": "design-goals-stated",
            "count": len(promises),
            "promises": promises,
        })

    # --- Rule 3: Deferral count ---
    defer_sections = [s for s in matrix if s["scores"].get("deferral", 0) > THRESHOLD]
    deferrals: list[dict] = []
    for s in defer_sections:
        text = _load_section_text(pid, s)
        deferrals.append({
            "section_idx": s["idx"],
            "heading": s["heading"],
            "start_line": s["start_line"],
            "text_preview": text[:500],
        })
    if deferrals:
        candidates.append({
            "rule": "deferrals",
            "label": "deferred-items",
            "count": len(deferrals),
            "deferrals": deferrals,
        })

    # --- Rule 4: Limitation harvest ---
    lim_sections = [s for s in matrix if s["scores"].get("limitation", 0) > THRESHOLD]
    limitations: list[dict] = []
    for s in lim_sections:
        text = _load_section_text(pid, s)
        limitations.append({
            "section_idx": s["idx"],
            "heading": s["heading"],
            "start_line": s["start_line"],
            "text_preview": text[:500],
        })
    if limitations:
        candidates.append({
            "rule": "limitations",
            "label": "stated-limitations",
            "count": len(limitations),
        })

    # --- Rule 5: Lens routing ---
    lens_groups: dict[str, list[dict]] = {}
    for lens_name, hypos in LENS_HYPOTHESES.items():
        relevant = []
        for s in matrix:
            if any(s["scores"].get(h, 0) > THRESHOLD for h in hypos):
                text = _load_section_text(pid, s)
                relevant.append({
                    "section_idx": s["idx"],
                    "heading": s["heading"],
                    "start_line": s["start_line"],
                    "end_line": s["end_line"],
                    "token_est": s["token_est"],
                    "text": text,
                    "trigger_scores": {
                        h: s["scores"].get(h, 0)
                        for h in hypos
                        if s["scores"].get(h, 0) > THRESHOLD
                    },
                })
        lens_groups[lens_name] = relevant

    output = {
        "pid": pid,
        "section_count": len(matrix),
        "candidates": candidates,
        "lens_groups": {
            name: {
                "section_count": len(secs),
                "total_tokens": sum(s["token_est"] for s in secs),
                "sections": secs,
            }
            for name, secs in lens_groups.items()
        },
    }

    elapsed = time.time() - t0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{pid.lower()}_candidates.json"
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Wrote: {out_path}", file=sys.stderr)

    print(f"\n# {pid} - Cross-Reference Report\n")
    print(f"- Time: {elapsed:.1f}s")
    print(f"## Candidates: {len(candidates)}\n")
    for c in candidates:
        print(f"  - [{c['rule']}] {c['label']}: {c.get('detail', c.get('count', ''))}")

    print(f"\n## Lens Groups\n")
    for name, group in output["lens_groups"].items():
        print(f"  {name:<20s}: {group['section_count']:3d} sections, ~{group['total_tokens']} tokens")


if __name__ == "__main__":
    main()
