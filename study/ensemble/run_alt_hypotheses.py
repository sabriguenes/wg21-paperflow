#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Re-score sentences against alternative hypothesis labels.

Zero-shot classifiers are extremely sensitive to hypothesis phrasing.
This script A/B tests TARGET and SKIP hypothesis variants: loads the
nli-small backend, scores all sentences once per candidate, and writes
per-label scores into ``data/alt_hypothesis_scores/<selector>/<variant>.json``.

Usage:
    python run_alt_hypotheses.py [selector_name [variant_id ...]]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
SCORES_OUT = DATA / "alt_hypothesis_scores"
SCORES_OUT.mkdir(exist_ok=True)


# Hypothesis variants to test. Each entry is (variant_id, target_list,
# skip_list). When a list has >1 entry, we score each one and take the
# max per side (multi-hypothesis fusion).
VARIANTS: list[tuple[str, list[str], list[str]]] = [
    (
        "baseline",
        ["A statement of fact or opinion."],
        ["A heading, list marker, or page metadata."],
    ),
    (
        "v1_claim_or_proposal",
        ["A claim, proposal, or assertion."],
        ["A heading, list marker, or page metadata."],
    ),
    (
        "v2_request_too",
        ["A claim, proposal, request, or assertion."],
        ["A heading, list marker, or page metadata."],
    ),
    (
        "v3_skip_fragment",
        ["A statement of fact or opinion."],
        ["A heading, list marker, page metadata, or sentence fragment."],
    ),
    (
        "v4_combo",
        ["A claim, proposal, request, or assertion."],
        ["A heading, list marker, page metadata, or sentence fragment."],
    ),
    (
        "v5_multi_target",
        [
            "A claim, proposal, or assertion.",
            "A request or recommendation.",
            "A definition or specification.",
        ],
        ["A heading, list marker, page metadata, or sentence fragment."],
    ),
    (
        "v6_multi_both",
        [
            "A claim, proposal, or assertion.",
            "A request or recommendation.",
            "A factual or empirical statement.",
        ],
        [
            "A heading or list marker.",
            "Page metadata or a section header.",
            "A sentence fragment or formatting artifact.",
        ],
    ),
]


def load_sentences(pid: str = "p4003r3") -> list[dict]:
    path = DATA / f"{pid}_sentences.json"
    if not path.is_file():
        path = DATA / f"{pid}_scores.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [{"sid": r["sid"], "line": r["line"], "text": r["text"]} for r in rows]


def score_variant(
    classifier, sentences: list[dict],
    target_hypos: list[str], skip_hypos: list[str],
) -> list[dict]:
    """Score every sentence against every hypothesis, then aggregate per side.

    We hand the classifier all hypotheses as candidate labels in one call,
    then post-process to compute per-side aggregate scores.
    """
    texts = [s["text"] for s in sentences]
    all_labels = target_hypos + skip_hypos
    raw = classifier.classify(texts, all_labels, multi_label=True)

    out: list[dict] = []
    for s, scores in zip(sentences, raw):
        t_scores = [scores[h] for h in target_hypos]
        s_scores = [scores[h] for h in skip_hypos]
        # max aggregation: any hypothesis that fires raises the side
        t = max(t_scores)
        sk = max(s_scores)
        out.append({
            "sid": s["sid"], "line": s["line"], "text": s["text"],
            "target": float(t), "skip": float(sk),
        })
    return out


def main() -> None:
    # Allow choosing the classifier slot and filtering variants from CLI.
    # Usage:
    #   python run_alt_hypotheses.py [selector_name [variant_id ...]]
    selector = sys.argv[1] if len(sys.argv) > 1 else "nli-small"
    filter_ids = set(sys.argv[2:]) if len(sys.argv) > 2 else None

    from pipeline.services import load_classifiers, resolve_classifier_slots

    print(f"Loading classifier '{selector}'...", file=sys.stderr)
    clfs, defaults = load_classifiers()
    slots = resolve_classifier_slots(clfs, defaults, {"selector": selector})
    classifier = slots["selector"]

    sentences = load_sentences()
    out_subdir = SCORES_OUT / selector
    out_subdir.mkdir(exist_ok=True)
    variants = [v for v in VARIANTS if filter_ids is None or v[0] in filter_ids]
    print(f"{len(sentences)} sentences, {len(variants)} variants -> {out_subdir}",
          file=sys.stderr)

    for variant_id, t_hypos, s_hypos in variants:
        print(f"  scoring {variant_id} ({len(t_hypos)} target hypos, "
              f"{len(s_hypos)} skip hypos)...", file=sys.stderr)
        scores = score_variant(classifier, sentences, t_hypos, s_hypos)
        out_path = out_subdir / f"{variant_id}.json"
        out_path.write_text(json.dumps({
            "variant": variant_id,
            "classifier": selector,
            "target_hypotheses": t_hypos,
            "skip_hypotheses": s_hypos,
            "rows": scores,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"    wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
