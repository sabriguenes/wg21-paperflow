#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Score only the gold-labeled sentences with both classifiers + new hypothesis.

This is the closing-the-loop step: we already have baseline-hypothesis
scores for the full P2300R10 corpus. Now we want
``(classifier, hypothesis) -> (target, skip)`` for every gold-labeled
sentence so the cascade ablation can compare apples to apples.

Writes ``data/p2300r10_alt_scores.json`` with shape::

    [{"sid": 0, "line": 16, "text": "...",
       "nli-small":      {"baseline": (t, s), "alt": (t, s)},
       "zeroshot-large": {"baseline": (t, s), "alt": (t, s)}}, ...]

We only score the 410 sentences across Phase 1 (sids 0..209) and
Phase 2 (sids 1500..1699). zeroshot-large costs ~1.8s/sentence, so
the full pass is ~12 minutes wall.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"

BASELINE_TARGET = "A statement of fact or opinion."
ALT_TARGET = "A statement describing what something does, is, or proposes."
SKIP_HYPO = "A heading, list marker, or page metadata."


def main() -> None:
    sentences_path = DATA / "p2300r10_sentences.json"
    all_sentences = json.loads(sentences_path.read_text(encoding="utf-8"))

    gold_sids: set[int] = set()
    for phase in (1, 2):
        gold = json.loads(
            (DATA / f"p2300r10_gold_phase{phase}.json")
            .read_text(encoding="utf-8")
        )
        gold_sids.update(r["sid"] for r in gold["labels"])

    labeled = [s for s in all_sentences if s["sid"] in gold_sids]
    print(f"Labeled subset: {len(labeled)} of {len(all_sentences)}",
          file=sys.stderr)

    from pipeline.services import load_classifiers, resolve_classifier_slots

    clfs, defaults = load_classifiers()

    # Score every (classifier, hypothesis) pair on the same texts.
    texts = [s["text"] for s in labeled]
    out_by_sid: dict[int, dict] = {
        s["sid"]: {"sid": s["sid"], "line": s["line"], "text": s["text"]}
        for s in labeled
    }

    pairs: list[tuple[str, str]] = [
        ("nli-small",      BASELINE_TARGET),
        ("nli-small",      ALT_TARGET),
        ("zeroshot-large", BASELINE_TARGET),
        ("zeroshot-large", ALT_TARGET),
    ]
    for clf_name, target_hypo in pairs:
        slots = resolve_classifier_slots(clfs, defaults, {"selector": clf_name})
        clf = slots["selector"]

        which = "baseline" if target_hypo == BASELINE_TARGET else "alt"
        print(f"Scoring {clf_name} / {which} ({len(texts)} sentences)...",
              file=sys.stderr)
        t0 = time.time()
        raw = clf.classify(texts, [target_hypo, SKIP_HYPO], multi_label=True)
        dt = time.time() - t0
        print(f"  done in {dt:.1f}s ({dt/len(texts)*1000:.1f}ms/sentence)",
              file=sys.stderr)

        for s, scores in zip(labeled, raw):
            row = out_by_sid[s["sid"]].setdefault(clf_name, {})
            row[which] = {
                "target": float(scores[target_hypo]),
                "skip": float(scores[SKIP_HYPO]),
            }

    out_path = DATA / "p2300r10_alt_scores.json"
    out_path.write_text(
        json.dumps(list(out_by_sid.values()), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
