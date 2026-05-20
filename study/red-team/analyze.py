#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Map-reduce red-team analysis of classifier-routed sections.

Two-phase pipeline:

1. **Map** (lens-agnostic): batch sections by context budget, extract
   structured signals from each batch via one LLM call. Signals carry
   short quotes and observations but make no severity judgments.

2. **Reduce** (per-lens): filter signals by type, feed to a
   lens-specific prompt, produce findings. Hard-fail if signals
   overflow the context window.

Reads ``score_matrix.json`` from section-chunks (section boundaries
and token estimates) and paper markdown from paperstore. Writes
``data/{pid}_signals.json`` (intermediate) and
``data/{pid}_findings.json`` (final, same schema as before).

Usage:
    python study/red-team/analyze.py P2300R10
    python study/red-team/analyze.py P2300R10 --slot fast
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from pipeline.tokens import est_tokens

DATA_DIR = Path("c:/Users/Vinnie/wg21-data-dir/paperstore")
CHUNKS_DATA = Path(__file__).parent.parent / "section-chunks" / "data"
OUT_DIR = Path(__file__).parent / "data"

MAX_OUTPUT_TOKENS = 16384

SeverityKind = Literal["critical", "significant", "minor"]

SignalType = Literal[
    "claim",
    "evidence",
    "promise",
    "limitation",
    "deferral",
    "example",
    "wording-issue",
    "definition",
    "comparison",
    "design-rationale",
]

LENS_SIGNAL_TYPES: dict[str, list[str]] = {
    "performance": ["claim", "evidence"],
    "design": ["promise", "limitation", "design-rationale"],
    "specification": ["wording-issue", "definition"],
    "usability": ["example", "design-rationale"],
    "ecosystem": ["deferral", "limitation", "comparison"],
}

LENS_ID_PREFIX = {
    "performance": "P",
    "design": "D",
    "specification": "S",
    "usability": "U",
    "ecosystem": "E",
}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Signal(BaseModel, frozen=True):
    section_idx: int = Field(description="Index of the source section.")
    heading: str = Field(description="Section heading.")
    start_line: int = Field(description="Line number where the section starts.")
    signal_type: SignalType = Field(description="Type of observation.")
    quote: str = Field(description="Exact quote from the paper, 1-2 sentences.")
    observation: str = Field(description="What was noticed, 1 sentence.")


class MapOutput(BaseModel, frozen=True):
    signals: list[Signal] = Field(
        default=[],
        description="Structured observations extracted from the sections.",
    )


class Finding(BaseModel, frozen=True):
    id: str = Field(description="Short identifier like D-C1 or S-S2.")
    severity: SeverityKind = Field(description="critical, significant, or minor.")
    title: str = Field(description="One-line finding title.")
    quoted_text: str = Field(description="Exact quote from the paper that the finding is based on.")
    source_line: int = Field(description="Line number where the quoted text appears.")
    explanation: str = Field(description="Why this is a problem. 2-4 sentences.")


class LensOutput(BaseModel, frozen=True):
    findings: list[Finding] = Field(
        default=[],
        description="Findings for this analytical lens. Empty if no problems found.",
    )


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

MAP_SYSTEM_PROMPT = """\
You are reading sections of a C++ standards proposal. Extract structured \
observations from the text. Do not judge severity or importance. Do not \
assign findings. Your job is to notice and record.

For each observation, classify it into exactly one signal type:
- claim: a performance, efficiency, or correctness claim
- evidence: benchmark data, measurements, profiling results
- promise: a stated design goal or guarantee
- limitation: an acknowledged weakness or restriction
- deferral: something explicitly left for future work
- example: a code example demonstrating usage
- wording-issue: a potential bug in formal specification text
- definition: a formal definition of a term or concept
- comparison: a comparison with another language, library, or proposal
- design-rationale: an explanation of why a design choice was made

Quote exactly from the paper (1-2 sentences). State what you noticed \
in one sentence. If a section has nothing noteworthy, skip it.\
"""

LENS_PROMPTS = {
    "performance": (
        "You are analyzing a C++ standards proposal for performance substantiation.\n\n"
        "Below are observations extracted from the paper: claims about performance "
        "or efficiency, and evidence (benchmarks, measurements) if any.\n\n"
        "For each claim that lacks supporting evidence, report a finding. Also report "
        "if broad performance claims appear with zero empirical data.\n\n"
        "Use the quoted text from the observations. Only report findings you are "
        "confident about."
    ),
    "design": (
        "You are analyzing a C++ standards proposal for design coherence.\n\n"
        "Below are observations extracted from the paper: stated promises (design "
        "goals), acknowledged limitations, and design rationale.\n\n"
        "For each promise, check whether the design delivers it. Report findings "
        "where a goal is contradicted, undermined, or only partially met. Flag "
        "internal contradictions.\n\n"
        "Use the quoted text from the observations. Only report findings you are "
        "confident about."
    ),
    "specification": (
        "You are analyzing the formal specification of a C++ standards proposal.\n\n"
        "Below are observations about wording issues and formal definitions.\n\n"
        "Check for: wrong names, undeclared identifiers, missing template arguments, "
        "inconsistent naming, duplicate paragraphs, missing normative requirements, "
        "and unspecified behavior.\n\n"
        "Use the quoted text from the observations. Only report definite bugs, "
        "not stylistic preferences."
    ),
    "usability": (
        "You are analyzing a C++ standards proposal for usability and learnability.\n\n"
        "Below are observations about code examples and design rationale.\n\n"
        "Check for: excessive concept count in simple examples, confusing naming, "
        "missing convenience APIs, pit-of-despair patterns, and prohibitive "
        "complexity for common tasks.\n\n"
        "Use the quoted text from the observations. Only report findings you are "
        "confident about."
    ),
    "ecosystem": (
        "You are analyzing a C++ standards proposal for ecosystem completeness.\n\n"
        "Below are observations about deferrals, limitations, and comparisons.\n\n"
        "Report findings for: essential facilities explicitly omitted, missing "
        "migration paths, incomplete interop with other C++ features, and "
        "dependencies on unfinished companion proposals.\n\n"
        "Use the quoted text from the observations. Only report findings you are "
        "confident about."
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------




def _load_section_text(pid: str, sec: dict) -> str:
    md_path = DATA_DIR / f"{pid.lower()}.md"
    lines = md_path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[sec["start_line"] - 1 : sec["end_line"]])


CHUNKER_MULTIPLIER = 1.3

def _batch_sections(
    sections: list[dict], available_tokens: int,
    token_multiplier: float = 1.3,
) -> list[list[dict]]:
    """Pack sections into batches that fit within the token budget.

    ``token_est`` in the score matrix was computed with a 1.3x multiplier.
    Scale each section's estimate to the target model's multiplier.
    """
    scale = token_multiplier / CHUNKER_MULTIPLIER
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_tokens = 0

    for sec in sections:
        sec_tokens = int(sec["token_est"] * scale)
        if current_tokens + sec_tokens > available_tokens and current:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(sec)
        current_tokens += sec_tokens

    if current:
        batches.append(current)

    return batches


def _build_map_user_message(pid: str, batch: list[dict]) -> str:
    parts: list[str] = [f"# Paper: {pid}\n"]
    for sec in batch:
        text = _load_section_text(pid, sec)
        parts.append(
            f"## {sec['heading']} (section {sec['idx']}, line {sec['start_line']})\n"
            f"{text}\n"
        )
    return "\n".join(parts)


def _build_reduce_user_message(
    lens: str, signals: list[dict], candidates: list[dict],
) -> str:
    parts: list[str] = []

    parts.append(f"## Signals for {lens} analysis\n")
    for s in signals:
        parts.append(
            f"- [{s['signal_type']}] {s['heading']} (line {s['start_line']})\n"
            f"  Quote: \"{s['quote']}\"\n"
            f"  Observation: {s['observation']}\n"
        )

    if candidates:
        parts.append("\n## Cross-reference observations\n")
        for c in candidates:
            parts.append(
                f"- [{c['rule']}] {c['label']}: "
                f"{c.get('detail', c.get('count', ''))}\n"
            )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def main() -> None:
    slot_name = "default"

    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        print(
            "usage: analyze.py <paper_id> [--slot NAME]",
            file=sys.stderr,
        )
        sys.exit(2)

    pid = args[0].upper()
    for i, a in enumerate(args):
        if a == "--slot" and i + 1 < len(args):
            slot_name = args[i + 1]

    # Load score matrix (section boundaries + token estimates)
    matrix_path = CHUNKS_DATA / f"{pid.lower()}_score_matrix.json"
    if not matrix_path.is_file():
        print(f"missing: {matrix_path}", file=sys.stderr)
        print("Run section_chunker.py and section_classifier.py first.", file=sys.stderr)
        sys.exit(1)

    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    print(f"Paper: {pid}, {len(matrix)} sections", file=sys.stderr)

    # Load candidates (deterministic rules from cross_reference.py)
    cand_path = OUT_DIR / f"{pid.lower()}_candidates.json"
    candidates: list[dict] = []
    if cand_path.is_file():
        cand_data = json.loads(cand_path.read_text(encoding="utf-8"))
        candidates = cand_data.get("candidates", [])

    # Resolve service
    from pipeline.services import load_services, resolve_slots
    from pipeline.agents import AgentBackend

    registry = load_services()
    slots = resolve_slots(registry)

    if slot_name in registry.services:
        backend = registry.services[slot_name]
    else:
        svc_name, backend = slots[slot_name]

    agent = AgentBackend(backend, max_tokens=MAX_OUTPUT_TOKENS, thinking_budget=4096)

    multiplier = agent.token_multiplier
    context_window = agent.max_context_window
    system_tokens = est_tokens(MAP_SYSTEM_PROMPT, agent=agent)
    available = context_window - system_tokens - MAX_OUTPUT_TOKENS

    print(f"Context window: {context_window}", file=sys.stderr)
    print(f"Available for sections: {available} tokens", file=sys.stderr)
    print(f"Slot: {slot_name}", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Phase 1: Map (lens-agnostic signal extraction)
    # -----------------------------------------------------------------------

    batches = _batch_sections(matrix, available, multiplier)
    print(f"\nMap phase: {len(batches)} batch(es)", file=sys.stderr)

    all_signals: list[dict] = []
    total_t0 = time.time()

    for batch_idx, batch in enumerate(batches):
        batch_tokens = sum(s["token_est"] for s in batch)
        print(
            f"  batch {batch_idx}: {len(batch)} sections, ~{batch_tokens} tokens...",
            file=sys.stderr, end="", flush=True,
        )

        user_msg = _build_map_user_message(pid, batch)

        t0 = time.time()
        result = await agent.run(
            system_prompt=MAP_SYSTEM_PROMPT,
            user_message=user_msg,
            output_type=MapOutput,
            label=f"map-batch-{batch_idx}",
        )
        elapsed = time.time() - t0

        batch_signals = [s.model_dump() for s in result.signals]
        all_signals.extend(batch_signals)
        print(f" {len(batch_signals)} signals ({elapsed:.1f}s)", file=sys.stderr)

    map_elapsed = time.time() - total_t0
    print(
        f"Map total: {len(all_signals)} signals in {map_elapsed:.1f}s",
        file=sys.stderr,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    signals_path = OUT_DIR / f"{pid.lower()}_signals.json"
    signals_path.write_text(json.dumps(all_signals, indent=2), encoding="utf-8")
    print(f"Wrote: {signals_path}", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Phase 2: Reduce (per-lens finding synthesis)
    # -----------------------------------------------------------------------

    reduce_available = context_window - MAX_OUTPUT_TOKENS

    all_findings: list[dict] = []
    reduce_t0 = time.time()

    print(f"\nReduce phase: {len(LENS_PROMPTS)} lenses", file=sys.stderr)

    for lens_name, system_prompt in LENS_PROMPTS.items():
        relevant_types = set(LENS_SIGNAL_TYPES[lens_name])
        filtered = [s for s in all_signals if s["signal_type"] in relevant_types]

        if not filtered:
            print(f"  {lens_name}: no signals, skipping", file=sys.stderr)
            continue

        user_msg = _build_reduce_user_message(lens_name, filtered, candidates)
        msg_tokens = est_tokens(system_prompt, agent=agent) + est_tokens(user_msg, agent=agent)

        if msg_tokens > reduce_available:
            raise OverflowError(
                f"{lens_name} lens: {msg_tokens} tokens exceeds "
                f"context budget {reduce_available}. "
                f"Paper has too many signals for a single reduce call."
            )

        print(
            f"  {lens_name}: {len(filtered)} signals, ~{msg_tokens} tokens...",
            file=sys.stderr, end="", flush=True,
        )

        t0 = time.time()
        result = await agent.run(
            system_prompt=system_prompt,
            user_message=user_msg,
            output_type=LensOutput,
            label=f"reduce-{lens_name}",
        )
        elapsed = time.time() - t0
        print(f" {len(result.findings)} findings ({elapsed:.1f}s)", file=sys.stderr)

        prefix = LENS_ID_PREFIX[lens_name]
        for i, f in enumerate(result.findings):
            finding_dict = f.model_dump()
            finding_dict["lens"] = lens_name
            if not finding_dict["id"].startswith(prefix):
                sev_code = {"critical": "C", "significant": "S", "minor": "M"}[
                    f.severity
                ]
                finding_dict["id"] = f"{prefix}-{sev_code}{i + 1}"
            all_findings.append(finding_dict)

    reduce_elapsed = time.time() - reduce_t0
    total_elapsed = time.time() - total_t0

    print(
        f"\nReduce total: {len(all_findings)} findings in {reduce_elapsed:.1f}s",
        file=sys.stderr,
    )
    print(f"Pipeline total: {total_elapsed:.1f}s", file=sys.stderr)

    findings_path = OUT_DIR / f"{pid.lower()}_findings.json"
    findings_path.write_text(json.dumps(all_findings, indent=2), encoding="utf-8")
    print(f"Wrote: {findings_path}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
