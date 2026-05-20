#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Render advocatus JSON to markdown report.

Reads ``data/{pid}_advocatus.json`` (from synthesize.py) and renders
the standard advocatus report format to ``results/{pid}_advocatus.md``.

Usage:
    python study/advocatus/report.py P2300R10
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

OUT_DIR = Path(__file__).parent
DATA_DIR = OUT_DIR / "data"
RESULTS_DIR = OUT_DIR / "results"

SEAL_DISPLAY = {
    "nihil_obstat": "***Nihil obstat*** - The cause proceeds without objection.",
    "cum_objectionibus": "***Cum obiectionibus*** - The cause proceeds with objections.",
    "sine_causa": "***Sine causa*** - No cause to examine.",
}


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: report.py <paper_id>", file=sys.stderr)
        sys.exit(2)

    pid = sys.argv[1].upper()
    adv_path = DATA_DIR / f"{pid.lower()}_advocatus.json"
    if not adv_path.is_file():
        print(f"missing: {adv_path} (run synthesize.py first)", file=sys.stderr)
        sys.exit(1)

    t0 = time.time()
    data = json.loads(adv_path.read_text(encoding="utf-8"))

    lines: list[str] = []
    lines.append(f"# Animadversiones - {pid}\n")
    lines.append("---\n")

    lines.append("## Seal\n")
    lines.append(SEAL_DISPLAY.get(data["seal"], data["seal"]))
    lines.append("")
    lines.append(data["one_sentence_assessment"])
    lines.append(f"\nConfidence: {data['confidence']}\n")
    lines.append("---\n")

    # Probationes
    if data.get("probationes"):
        lines.append("## Approbationes\n")
        for i, p in enumerate(data["probationes"], 1):
            lines.append(f"### {i}. {p['section']}\n")
            lines.append(f"*Approbatio.* {p['reasoning']} The Defensor prevailed on {p['challenge']}.\n")
        lines.append("---\n")

    # Objections
    if data.get("objections"):
        lines.append("## Objections\n")
        for i, obj in enumerate(data["objections"], 1):
            lines.append(f"### Objection {i}: {obj['gravamen']}\n")
            lines.append(f"**Severity:** {obj['severity'].title()}\n")
            lines.append(f"**Quoted text:**")
            lines.append(f"> {obj['quoted_text']}\n")
            if obj.get("source_line"):
                lines.append(f"(Line {obj['source_line']})\n")
            lines.append(f"**Motivatio:**\n")
            lines.append(f"- **Adversary:** {obj['adversary']}")
            lines.append(f"- **Forum:** {obj['forum']}")
            lines.append(f"- **Damage:** {obj['damage']}")
            lines.append("")
        lines.append("---\n")

    # Notae Minores
    if data.get("notae_minores"):
        lines.append("## Notae Minores\n")
        for note in data["notae_minores"]:
            lines.append(f"- {note}")
        lines.append("\n---\n")

    # Acta - charge outcomes
    if data.get("charge_outcomes"):
        lines.append("## Acta\n")
        lines.append("### Candidate charges and outcomes\n")
        survived = [c for c in data["charge_outcomes"] if c["verdict"] == "survived"]
        killed = [c for c in data["charge_outcomes"] if c["verdict"] == "killed"]
        relegated = [c for c in data["charge_outcomes"] if c["verdict"] == "relegated"]

        if survived:
            lines.append(f"**Survived ({len(survived)}):**")
            for c in survived:
                lines.append(f"- {c['finding_id']}: {c['reasoning']}")
            lines.append("")
        if killed:
            lines.append(f"**Killed ({len(killed)}):**")
            for c in killed:
                lines.append(f"- {c['finding_id']}: [{c['challenge']}] {c['reasoning']}")
            lines.append("")
        if relegated:
            lines.append(f"**Relegated ({len(relegated)}):**")
            for c in relegated:
                lines.append(f"- {c['finding_id']}: [{c['challenge']}] {c['reasoning']}")
            lines.append("")

    elapsed = time.time() - t0
    report = "\n".join(lines)
    sys.stdout.buffer.write(report.encode("utf-8"))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{pid.lower()}_advocatus.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\nWrote: {out_path} ({elapsed:.1f}s)", file=sys.stderr)


if __name__ == "__main__":
    main()
