"""Standalone runner that prints a full analysis of the synthetic paper.

Not a pytest module (leading underscore keeps it out of collection).
Run with::

    uv run python packages/dissect/tests/_inspect_synthetic.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import anyio

# Make the sibling test module importable without packaging the tests dir.
sys.path.insert(0, str(Path(__file__).parent))

from test_synthetic_pipeline import (  # noqa: E402
    _factual_alive,
    _normative_alive,
    _run_full_pipeline,
    _seed_backend,
)


def _label(s: str, w: int = 78) -> str:
    return f"\n{'=' * w}\n{s}\n{'=' * w}"


async def _main() -> None:
    # Use ignore_cleanup_errors so a held DB handle on Windows doesn't
    # crash before we get to print the results.
    with tempfile.TemporaryDirectory(
        prefix="dissect-inspect-", ignore_cleanup_errors=True,
    ) as tmp:
        backend = _seed_backend(Path(tmp))
        try:
            state = await _run_full_pipeline(backend)
        finally:
            backend.close()

    print(_label("CHUNKS"))
    assert state.chunks is not None
    print(f"chunks = {len(state.chunks)}")
    for i, c in enumerate(state.chunks):
        print(f"  chunk {i}: {len(c.text)} chars, starts at line {c.line_offset}")

    print(_label("NORMATIVE CLAIMS (alive after dedup)"))
    for c in _normative_alive(state):
        print(f"  [{c.uid}] L{c.loc.line}  ({c.section})")
        print(f"      text: {c.text}")
        if c.depends_on:
            print(f"      depends_on: {c.depends_on}")

    print(_label("FACTUAL CLAIMS (alive after dedup)"))
    for c in _factual_alive(state):
        print(f"  [{c.uid}] L{c.loc.line}  ({c.section})")
        print(f"      text: {c.text}")

    print(_label("EVIDENCE (alive after dedup)"))
    if state.deduped_evidence:
        for e in state.deduped_evidence:
            if e.merged_into is not None:
                continue
            flags = []
            if e.quantitative:
                flags.append("Q")
            if e.cited:
                flags.append("C")
            if e.verifiable:
                flags.append("V")
            if e.normative:
                flags.append("N")
            print(f"  [{e.uid}] L{e.loc.line} [{','.join(flags) or '-'}]  ({e.section})")
            print(f"      text: {e.text}")
            for s in e.supports:
                print(f"      supports: {s}")

    print(_label("RHETORIC"))
    if state.rhetoric:
        for m in state.rhetoric:
            print(f"  [{m.uid}] {m.marker_type}/{m.intensity} L{m.loc.line}")
            print(f"      text:   {m.text}")
            print(f"      target: {m.target}")

    print(_label("STEP 8 TRIAGE STATE"))
    print(f"  verify_batch_count = {state.verify_batch_count}")
    if state.centrality_scores:
        ranked = sorted(
            state.centrality_scores.items(), key=lambda kv: (-kv[1], kv[0]),
        )
        for uid, score in ranked:
            text = next(
                (c.text for c in (state.normative_claims or []) if c.uid == uid),
                "?",
            )
            print(f"  centrality uid={uid:>3}  score={score:.1f}  text={text!r}")
    if state.triaged_evidence:
        evid_by_uid = {e.uid: e.text for e in (state.deduped_evidence or [])}
        for cuid in sorted(state.triaged_evidence.keys()):
            ev_uids = state.triaged_evidence[cuid]
            ctext = next(
                (c.text for c in (state.normative_claims or []) if c.uid == cuid),
                "?",
            )
            print(f"  triage uid={cuid:>3}  text={ctext!r}")
            for euid in ev_uids:
                etext = evid_by_uid.get(euid, "?")
                print(f"    + evidence uid={euid:>3}  text={etext!r}")
    if state.disclaim_candidates:
        for a, b in state.disclaim_candidates:
            atext = next((c.text for c in (state.normative_claims or []) if c.uid == a), "?")
            btext = next((c.text for c in (state.normative_claims or []) if c.uid == b), "?")
            print(f"  disclaim pair ({a}, {b})")
            print(f"    A: {atext!r}")
            print(f"    B: {btext!r}")

    print(_label("VERDICTS (Step 8 Verify)"))
    if state.verdicts:
        for v in state.verdicts:
            related = "" if v.related_uid < 0 else f" (related uid={v.related_uid})"
            print(f"  claim {v.claim_uid:>3}  ->  {v.status}{related}")

    print(_label("LOAD-BEARING (Step 9, possibly upgraded by Step 12)"))
    if state.load_bearing_claims:
        for lb in state.load_bearing_claims:
            deps = f" deps={lb.dependents}" if lb.dependents else ""
            print(f"  claim {lb.claim_uid:>3}  =>  {lb.classification}{deps}")

    print(_label("CITATION AUDIT (Step 10)"))
    if state.citation_audit:
        for row in state.citation_audit:
            r = "OK" if row.resolved else "MISS"
            print(f"  {row.paper_id}  [{r}]  method={row.resolution_method}  "
                  f"quote={row.quote_match}")
            if row.discrepancy:
                print(f"      discrepancy: {row.discrepancy}")

    print(_label("WEB EVIDENCE FOUND (Step 11)"))
    if state.external_evidence:
        for ex in state.external_evidence:
            print(f"  claim {ex.claim_uid:>3} <- {ex.stance}  {ex.source_title}")
            print(f"      url:     {ex.source_url}")
            print(f"      finding: {ex.finding}")
    else:
        print("  (none)")

    print(_label("CAPUT CAUSAE (Step 13)"))
    if state.caput_causae:
        print(f"  thesis: {state.caput_causae.thesis}")
        print(f"  anchored uids: {state.caput_causae.anchored_claim_uids}")
        print(f"  evidence root uids: {state.caput_causae.evidence_root_uids}")
    else:
        print("  (none)")

    print(_label("PATTERNS (Step 14)"))
    if state.marker_patterns:
        for a in state.marker_patterns.asymmetries:
            print(f"  asymmetry: {a}")
        for c in state.marker_patterns.concession_clusters:
            print(f"  concession_cluster: {c}")
        for s in state.marker_patterns.scope_chains:
            print(f"  scope_chain: {s}")

    print(_label("FINAL REPORT (Step 15)"))
    print(state.report or "(empty)")


if __name__ == "__main__":
    anyio.run(_main)
