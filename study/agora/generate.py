#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Generate an Agora thread from red-team findings.

Reads red-team findings and cross-reference candidates, loads the paper
from paperstore for context, then makes two LLM calls: a smell test
(structured output) and thread generation (markdown output).

Uses the prompt-file pattern: all LLM-facing instructions live in
``agora-study.md``, parsed by ``pipeline.markdown.sections()``.

Usage:
    python study/agora/generate.py P2300R10
    python study/agora/generate.py P4003R3 --slot b200-r1
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

STUDY_DIR = Path(__file__).parent
RED_TEAM_DATA = STUDY_DIR.parent / "red-team" / "data"
DATA_DIR = Path("c:/Users/Vinnie/wg21-data-dir/paperstore")
OUT_DIR = STUDY_DIR / "data"
RESULTS_DIR = STUDY_DIR / "results"

HeatTier = Literal["cold", "warm", "hot", "thermonuclear"]
InterestTier = Literal["niche", "relevant", "magnetic", "gravitational"]
PaperType = Literal["wording", "proposal", "directional"]


class TechnicalAnchor(BaseModel, frozen=True):
    finding_id: str = Field(description="ID of the red-team finding this anchor is based on.")
    reddit_angle: str = Field(description="One sentence: how a commenter would phrase this.")
    visibility: Literal["high", "medium", "subtle"] = Field(
        description="high=obvious on first read, medium=requires reading the section, subtle=requires cross-referencing."
    )


class SmellTestOutput(BaseModel, frozen=True):
    heat: HeatTier = Field(description="Thread temperature tier.")
    interest: InterestTier = Field(description="Community engagement tier.")
    paper_type: PaperType = Field(description="Wording, proposal, or directional.")
    hot_takes: list[str] = Field(description="3-5 surface reactions the paper will trigger (noise fuel).")
    tangent_magnets: list[str] = Field(description="2-3 adjacent topics the thread will veer toward.")
    technical_anchors: list[TechnicalAnchor] = Field(description="5-10 findings that will drive discussion.")
    one_sentence_summary: str = Field(description="How the submission poster would describe the paper.")


class ThreadOutput(BaseModel, frozen=True):
    thread_markdown: str = Field(description="The full Reddit thread as markdown text.")


def _load_prompt_sections() -> dict[str, str]:
    from pipeline.markdown import sections
    prompt_path = STUDY_DIR / "agora-study.md"
    return sections(prompt_path.read_text(encoding="utf-8"))


def _build_smell_test_msg(pid: str, findings: list, candidates: dict, paper_intro: str) -> str:
    parts: list[str] = []

    parts.append("## Paper\n")
    md_path = DATA_DIR / f"{pid.lower()}.md"
    if md_path.is_file():
        lines = md_path.read_text(encoding="utf-8").splitlines()
        parts.append("\n".join(lines[:20]))
        parts.append("\n\n[...paper text follows...]\n")
        words = " ".join(lines).split()
        parts.append(" ".join(words[:6000]))
    parts.append("\n\n")

    parts.append("## Red-Team Findings\n\n")
    for f in findings:
        parts.append(
            f"- **{f['id']}** [{f['severity']}] {f['title']}\n"
            f"  Line {f.get('source_line', '?')}: \"{f.get('quoted_text', '')[:200]}\"\n"
            f"  {f['explanation'][:200]}\n\n"
        )

    parts.append("## Cross-Reference Candidates\n\n")
    for c in candidates.get("candidates", []):
        parts.append(f"- [{c['rule']}] {c['label']}: {c.get('detail', c.get('count', ''))}\n")

    return "\n".join(parts)


def _build_thread_gen_msg(pid: str, smell: SmellTestOutput) -> str:
    md_path = DATA_DIR / f"{pid.lower()}.md"
    meta_lines = []
    if md_path.is_file():
        for line in md_path.read_text(encoding="utf-8").splitlines()[:17]:
            meta_lines.append(line)

    parts: list[str] = []
    parts.append("## Paper Metadata\n")
    parts.append("\n".join(meta_lines))
    parts.append("\n\n## Smell Test Results\n\n")
    parts.append(f"- Heat: {smell.heat}\n")
    parts.append(f"- Interest: {smell.interest}\n")
    parts.append(f"- Paper type: {smell.paper_type}\n")
    parts.append(f"- Summary: {smell.one_sentence_summary}\n\n")

    parts.append("### Hot Takes\n")
    for ht in smell.hot_takes:
        parts.append(f"- {ht}\n")

    parts.append("\n### Tangent Magnets\n")
    for tm in smell.tangent_magnets:
        parts.append(f"- {tm}\n")

    parts.append("\n### Technical Anchors\n")
    for a in smell.technical_anchors:
        parts.append(f"- [{a.finding_id}] ({a.visibility}) {a.reddit_angle}\n")

    return "\n".join(parts)


async def main() -> None:
    smell_slot = "default"
    thread_slot = "default"

    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        print("usage: generate.py <paper_id> [--slot NAME] [--smell-slot NAME] [--thread-slot NAME]", file=sys.stderr)
        sys.exit(2)

    pid = args[0].upper()
    for i, a in enumerate(args):
        if a == "--slot" and i + 1 < len(args):
            smell_slot = thread_slot = args[i + 1]
        elif a == "--smell-slot" and i + 1 < len(args):
            smell_slot = args[i + 1]
        elif a == "--thread-slot" and i + 1 < len(args):
            thread_slot = args[i + 1]

    findings_path = RED_TEAM_DATA / f"{pid.lower()}_findings.json"
    candidates_path = RED_TEAM_DATA / f"{pid.lower()}_candidates.json"

    if not findings_path.is_file():
        print(f"missing: {findings_path} (run red-team pipeline first)", file=sys.stderr)
        sys.exit(1)
    if not candidates_path.is_file():
        print(f"missing: {candidates_path}", file=sys.stderr)
        sys.exit(1)

    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))

    print(f"Paper: {pid}", file=sys.stderr)
    print(f"Findings: {len(findings)}", file=sys.stderr)
    print(f"Smell slot: {smell_slot}, Thread slot: {thread_slot}", file=sys.stderr)

    prompt_sections = _load_prompt_sections()
    system_prompt = prompt_sections.get("System Prompt", "")
    smell_instructions = prompt_sections.get("1. Smell Test", "")
    thread_instructions = prompt_sections.get("2. Generate Thread", "")

    from pipeline.services import load_services, resolve_slots
    from pipeline.agents import AgentBackend

    registry = load_services()
    slots = resolve_slots(registry)

    def _resolve(name: str):
        if name in registry.services:
            return registry.services[name]
        _svc, backend = slots[name]
        return backend

    smell_agent = AgentBackend(_resolve(smell_slot), max_tokens=16384, thinking_budget=4096)
    thread_agent = AgentBackend(_resolve(thread_slot), max_tokens=16384, thinking_budget=4096)

    # Step 1: Smell test
    smell_msg = _build_smell_test_msg(pid, findings, candidates, "")
    print(f"Step 1 (smell test): ~{len(smell_msg.split())} words...", file=sys.stderr, end="", flush=True)

    t0 = time.time()
    smell_result = await smell_agent.run(
        system_prompt=system_prompt + "\n\n" + smell_instructions,
        user_message=smell_msg,
        output_type=SmellTestOutput,
        label="agora-smell-test",
    )
    elapsed = time.time() - t0
    print(f" {smell_result.heat}/{smell_result.interest}, {len(smell_result.technical_anchors)} anchors ({elapsed:.1f}s)", file=sys.stderr)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    smell_path = OUT_DIR / f"{pid.lower()}_smell_test.json"
    smell_path.write_text(smell_result.model_dump_json(indent=2), encoding="utf-8")
    print(f"Wrote: {smell_path}", file=sys.stderr)

    # Step 2: Generate thread
    thread_msg = _build_thread_gen_msg(pid, smell_result)
    print(f"Step 2 (thread gen): ~{len(thread_msg.split())} words...", file=sys.stderr, end="", flush=True)

    t0 = time.time()
    thread_result = await thread_agent.run(
        system_prompt=system_prompt + "\n\n" + thread_instructions,
        user_message=thread_msg,
        output_type=ThreadOutput,
        label="agora-thread-gen",
    )
    elapsed = time.time() - t0
    print(f" done ({elapsed:.1f}s)", file=sys.stderr)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{pid.lower()}_thread.md"
    out_path.write_text(thread_result.thread_markdown, encoding="utf-8")
    print(f"Wrote: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
