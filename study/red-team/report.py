#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Assemble findings into a red team markdown report.

Reads ``data/{pid}_findings.json`` (from analyze.py) and renders
a structured markdown report to ``results/{pid}_red_team.md``.

Usage:
    python study/red-team/report.py P2300R10
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

OUT_DIR = Path(__file__).parent
DATA_DIR = OUT_DIR / "data"
RESULTS_DIR = OUT_DIR / "results"

LENS_TITLES = {
    "design": "I. Design Philosophy and Conceptual Model",
    "specification": "II. Formal Specification",
    "usability": "III. Usability and Learnability",
    "performance": "IV. Performance and Scalability",
    "ecosystem": "V. Ecosystem Interoperability and Missing Facilities",
}

SEVERITY_ORDER = {"critical": 0, "significant": 1, "minor": 2}


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: report.py <paper_id>", file=sys.stderr)
        sys.exit(2)

    pid = sys.argv[1].upper()
    findings_path = DATA_DIR / f"{pid.lower()}_findings.json"
    if not findings_path.is_file():
        print(f"missing: {findings_path} (run analyze.py first)", file=sys.stderr)
        sys.exit(1)

    t0 = time.time()
    findings = json.loads(findings_path.read_text(encoding="utf-8"))

    by_lens: dict[str, list[dict]] = {}
    for f in findings:
        by_lens.setdefault(f["lens"], []).append(f)

    for lens_findings in by_lens.values():
        lens_findings.sort(key=lambda f: SEVERITY_ORDER.get(f["severity"], 9))

    severity_counts = {"critical": 0, "significant": 0, "minor": 0}
    for f in findings:
        severity_counts[f["severity"]] = severity_counts.get(f["severity"], 0) + 1

    lines: list[str] = []
    lines.append(f"# Red Team - {pid}\n")
    lines.append("---\n")
    lines.append("## Executive Summary\n")
    lines.append(
        f"**Finding counts: {severity_counts['critical']} Critical, "
        f"{severity_counts['significant']} Significant, "
        f"{severity_counts['minor']} Minor.**\n"
    )
    lines.append("---\n")

    for lens_key in ["design", "specification", "usability", "performance", "ecosystem"]:
        title = LENS_TITLES[lens_key]
        lens_findings = by_lens.get(lens_key, [])
        lines.append(f"## {title}\n")

        if not lens_findings:
            lines.append("No findings.\n")
            continue

        current_severity = None
        for f in lens_findings:
            if f["severity"] != current_severity:
                current_severity = f["severity"]
                lines.append(f"### {current_severity.title()}\n")

            lines.append(f"**{f['id']}. {f['title']}**")
            if f.get("source_line"):
                lines.append(f"Line {f['source_line']}:")
            lines.append(f"> {f['quoted_text']}\n")
            lines.append(f"{f['explanation']}\n")

    lines.append("---\n")

    elapsed = time.time() - t0
    report = "\n".join(lines)
    sys.stdout.buffer.write(report.encode("utf-8"))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{pid.lower()}_red_team.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\nWrote: {out_path} ({elapsed:.1f}s)", file=sys.stderr)


if __name__ == "__main__":
    main()
