#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Single-call advocatus synthesis from red-team findings.

Reads red-team findings, cross-reference candidates, and paper intro.
Produces an advocatus report (seal, objections, probationes, notae
minores) in one LLM call via AgentBackend.

Usage:
    python study/advocatus/synthesize.py P2300R10
    python study/advocatus/synthesize.py P2300R10 --slot b200-r1
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

DATA_DIR = Path("c:/Users/Vinnie/wg21-data-dir/paperstore")
RED_TEAM_DATA = Path(__file__).parent.parent / "red-team" / "data"
CHUNKS_DATA = Path(__file__).parent.parent / "section-chunks" / "data"
OUT_DIR = Path(__file__).parent / "data"

SealKind = Literal["nihil_obstat", "cum_objectionibus", "sine_causa"]
ChallengeVerdict = Literal["killed", "relegated", "survived"]


class ChargeOutcome(BaseModel, frozen=True):
    finding_id: str = Field(description="ID from the red-team finding.")
    verdict: ChallengeVerdict = Field(
        description="killed = charge dismissed, relegated = sent to notae minores, survived = becomes objection."
    )
    challenge: str = Field(
        description="Which of the 6 challenges prevailed (Confessio/Articulus/Testimonium/Humanitas/Prudentia/Dignitas). Empty if survived."
    )
    reasoning: str = Field(description="One sentence explaining the verdict.")


class Objection(BaseModel, frozen=True):
    finding_id: str = Field(description="ID of the surviving charge.")
    severity: Literal["high", "medium", "low"] = Field(
        description="high = paper-killing or NB-level, medium = section-weakening, low = capital-cost only."
    )
    gravamen: str = Field(description="The essential complaint in one sentence.")
    quoted_text: str = Field(description="Exact quote from the paper.")
    source_line: int = Field(description="Line number of the quoted text.")
    adversary: str = Field(description="Who would actually raise this objection.")
    forum: str = Field(description="Where the attack would land: lewg / sg1 / reflector / nb_comment / hallway.")
    damage: str = Field(description="What happens if the attack lands: paper_killing / section_weakening / revision_forcing / capital_cost.")


class Probatio(BaseModel, frozen=True):
    section: str = Field(description="Section heading certified strong.")
    challenge: str = Field(description="Which Defensor challenge certified it.")
    reasoning: str = Field(description="One sentence explaining why it is strong.")


class AdvocatusOutput(BaseModel, frozen=True):
    seal: SealKind = Field(
        description="nihil_obstat if thesis survives and no objections touch it. cum_objectionibus if objections exist. sine_causa if paper has no claims."
    )
    one_sentence_assessment: str = Field(description="The single sentence that closes the Relatio.")
    confidence: float = Field(ge=0.0, le=1.0, description="Overall confidence in the verdict.")
    charge_outcomes: list[ChargeOutcome] = Field(
        default=[], description="Verdict for each red-team finding after applying the 6 Defensor challenges."
    )
    objections: list[Objection] = Field(default=[], description="Surviving charges with motivatio.")
    probationes: list[Probatio] = Field(default=[], description="Sections certified strong.")
    notae_minores: list[str] = Field(default=[], description="Relegated items (typos, formatting, housekeeping).")


SYSTEM_PROMPT = """\
You serve the tribunal of the Advocatus Diaboli examining a WG21 paper. \
Your office exists to test, not to convict. Nihil obstat is the highest \
outcome the office can deliver. Every finding is filed reluctantly. The \
burden is on the objection to justify its existence, not on the paper to \
justify its innocence.

You receive a set of candidate charges (red-team findings). For each charge, \
apply the six Defensor challenges in order. Stop at the first killed or \
relegated verdict; otherwise emit survived.

THE SIX CHALLENGES:

1. Confessio - Does the paper already concede the specific gravamen? The \
concession must directly address the gravamen, not merely touch the same \
topic. If conceded, killed.

2. Articulus - Does the paper actually claim what this charge attacks? If \
the charge attacks an inference the red team drew rather than a claim the \
paper stated, killed. The boundaries are the law of this tribunal.

3. Testimonium - Could this charge be dissolved by a single factual check? \
If a ten-second verification would collapse it, killed.

4. Humanitas - Would a real human committee member raise this argument? If \
the objection exists only because exhaustive analysis surfaced it and no \
committee member would replicate the work, killed.

5. Prudentia - Would pressing this argument be self-defeating for the \
actual opponent? If raising it requires an adversary to contradict their \
own position, killed.

6. Dignitas - Is this charge beneath the dignity of the office? Typos, \
formatting, word-choice, citation-style are housekeeping, not charges. \
Verdict: relegated (sent to notae minores).

For surviving charges, attach a motivatio: name the adversary, the forum, \
and the damage.

For sections where no charge survived, certify them as probationes (strong).

Then weigh the cause as a whole and issue the seal.\
"""


def _load_paper_intro(pid: str) -> str:
    md_path = DATA_DIR / f"{pid.lower()}.md"
    lines = md_path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[:100])


def _build_user_message(pid: str, findings: list, candidates: dict, intro: str) -> str:
    parts: list[str] = []

    parts.append(f"# Paper: {pid}\n")
    parts.append("## Paper Introduction and Priorities\n")
    parts.append(intro)
    parts.append("")

    if candidates.get("candidates"):
        parts.append("## Cross-Reference Observations\n")
        for c in candidates["candidates"]:
            parts.append(f"- [{c['rule']}] {c['label']}: {c.get('detail', c.get('count', ''))}")
        parts.append("")

    parts.append("## Red-Team Findings (Candidate Charges)\n")
    parts.append("Apply the six Defensor challenges to each finding.\n")
    for f in findings:
        parts.append(f"### {f['id']}: {f['title']}")
        parts.append(f"- Severity: {f['severity']}")
        parts.append(f"- Lens: {f['lens']}")
        parts.append(f"- Line: {f['source_line']}")
        parts.append(f"- Quote: {f['quoted_text']}")
        parts.append(f"- Explanation: {f['explanation']}")
        parts.append("")

    return "\n".join(parts)


async def main() -> None:
    slot_name = "default"

    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        print("usage: synthesize.py <paper_id> [--slot NAME]", file=sys.stderr)
        sys.exit(2)

    pid = args[0].upper()
    for i, a in enumerate(args):
        if a == "--slot" and i + 1 < len(args):
            slot_name = args[i + 1]

    findings_path = RED_TEAM_DATA / f"{pid.lower()}_findings.json"
    if not findings_path.is_file():
        print(f"missing: {findings_path} (run red-team/analyze.py first)", file=sys.stderr)
        sys.exit(1)

    candidates_path = RED_TEAM_DATA / f"{pid.lower()}_candidates.json"
    if not candidates_path.is_file():
        print(f"missing: {candidates_path} (run red-team/cross_reference.py first)", file=sys.stderr)
        sys.exit(1)

    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    intro = _load_paper_intro(pid)

    print(f"Paper: {pid}", file=sys.stderr)
    print(f"Findings: {len(findings)}", file=sys.stderr)
    print(f"Slot: {slot_name}", file=sys.stderr)

    from pipeline.services import load_services, resolve_slots
    from pipeline.agents import AgentBackend

    registry = load_services()
    slots = resolve_slots(registry)

    if slot_name in registry.services:
        backend = registry.services[slot_name]
    else:
        svc_name, backend = slots[slot_name]
    agent = AgentBackend(backend, max_tokens=16384, thinking_budget=4096)

    user_msg = _build_user_message(pid, findings, candidates, intro)
    token_est = len(user_msg.split())
    print(f"User message: ~{token_est} words", file=sys.stderr)

    print("Running advocatus synthesis...", file=sys.stderr)
    t0 = time.time()
    result = await agent.run(
        system_prompt=SYSTEM_PROMPT,
        user_message=user_msg,
        output_type=AdvocatusOutput,
        label="advocatus-synthesis",
    )
    elapsed = time.time() - t0

    survived = sum(1 for c in result.charge_outcomes if c.verdict == "survived")
    killed = sum(1 for c in result.charge_outcomes if c.verdict == "killed")
    relegated = sum(1 for c in result.charge_outcomes if c.verdict == "relegated")

    print(f"Done in {elapsed:.1f}s", file=sys.stderr)
    print(f"Seal: {result.seal}", file=sys.stderr)
    print(f"Charges: {survived} survived, {killed} killed, {relegated} relegated", file=sys.stderr)
    print(f"Objections: {len(result.objections)}", file=sys.stderr)
    print(f"Probationes: {len(result.probationes)}", file=sys.stderr)
    print(f"Notae minores: {len(result.notae_minores)}", file=sys.stderr)
    print(f"Confidence: {result.confidence}", file=sys.stderr)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{pid.lower()}_advocatus.json"
    out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(f"Wrote: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
