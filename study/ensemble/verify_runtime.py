#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Programmatic check that the dissect runtime matches the ablation.

Runs every gold-labeled sentence through:
  1. the study's local ``is_structural_skip`` (from final_ablation.py)
  2. the runtime's ``_is_structural_skip`` (from dissect.harness)
and asserts agreement.

Also asserts the runtime constants (TARGET label, SKIP label,
default margins) equal the alt-hypothesis Tier 1 numbers reported
in ``results/final_findings.md``.

Exit code 0 if everything aligns, non-zero with a per-mismatch dump
otherwise.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"


def study_is_structural_skip(text: str) -> bool:
    """Verbatim copy of the predicate from final_ablation.py."""
    NUMBER_ONLY = re.compile(r"^\s*\d+\.\s*$")
    ELLIPSIS_PREFIX = re.compile(r"^\s*\.{2,}\s")
    PUNCT_ONLY = re.compile(r"^[\W\d]+$", re.UNICODE)
    EXAMPLE_BLOCK = re.compile(r"^\[\*Example\b.*\*end example\*\]", re.DOTALL)
    t = text.strip()
    if NUMBER_ONLY.match(t): return True
    if ELLIPSIS_PREFIX.match(t): return True
    if PUNCT_ONLY.match(t): return True
    if len(t.split()) < 3: return True
    if EXAMPLE_BLOCK.match(t): return True
    return False


def main() -> int:
    from dissect.harness import (
        _is_structural_skip,
        _TAG_TARGET_LABEL,
        _TAG_SKIP_LABEL,
        _DEFAULT_TARGET_MARGIN,
        _DEFAULT_SKIP_MARGIN,
    )

    errors: list[str] = []

    # --- constants ---------------------------------------------------------
    expected_target = "A statement describing what something does, is, or proposes."
    expected_skip = "A heading, list marker, or page metadata."
    if _TAG_TARGET_LABEL != expected_target:
        errors.append(
            f"TARGET label drift:\n  runtime: {_TAG_TARGET_LABEL!r}\n"
            f"  expected: {expected_target!r}"
        )
    if _TAG_SKIP_LABEL != expected_skip:
        errors.append(
            f"SKIP label drift:\n  runtime: {_TAG_SKIP_LABEL!r}\n"
            f"  expected: {expected_skip!r}"
        )
    if _DEFAULT_TARGET_MARGIN != 0.05:
        errors.append(f"target_margin drift: {_DEFAULT_TARGET_MARGIN} != 0.05")
    if _DEFAULT_SKIP_MARGIN != 0.40:
        errors.append(f"skip_margin drift: {_DEFAULT_SKIP_MARGIN} != 0.40")

    # --- prefilter equivalence on every gold-labeled sentence -------------
    paths = [
        DATA / "p2300r10_sentences.json",
    ]
    for p in paths:
        if not p.is_file():
            print(f"skip (not found): {p}", file=sys.stderr)
            continue
        rows = json.loads(p.read_text(encoding="utf-8"))
        mismatches = 0
        for r in rows:
            text = r["text"]
            study = study_is_structural_skip(text)
            runtime = _is_structural_skip(text)
            if study != runtime:
                mismatches += 1
                if mismatches <= 5:
                    errors.append(
                        f"prefilter mismatch [{p.name} sid={r.get('sid')}]: "
                        f"study={study} runtime={runtime} text={text!r}"
                    )
        print(f"{p.name}: {len(rows)} sentences, "
              f"{mismatches} prefilter mismatches", file=sys.stderr)

    # --- summary -----------------------------------------------------------
    print()
    print("=== runtime constants ===")
    print(f"  TARGET label:      {_TAG_TARGET_LABEL!r}")
    print(f"  SKIP label:        {_TAG_SKIP_LABEL!r}")
    print(f"  target_margin:     {_DEFAULT_TARGET_MARGIN}")
    print(f"  skip_margin:       {_DEFAULT_SKIP_MARGIN}")
    print()
    if errors:
        print("=== DRIFT DETECTED ===")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("=== runtime matches ablation Tier 1 exactly ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
