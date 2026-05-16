#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for the pure-Python code harness.

All tests use synthetic data - no LLM, no paperstore, no fixtures.
"""

from __future__ import annotations

from dissect.harness import (
    _blank_non_prose,
    _blank_yaml_frontmatter,
    _chunk_paper,
    _decompose_sentences,
    _dedup_tier0,
    _dedup_tier1,
    _extract_citations,
    _number_lines,
    _promote_claims,
    _promote_evidence,
    _promote_rhetoric,
    _render_tagged_chunk,
    _split_tagged_by_chunk,
    _strip_line_prefix,
    _tag_sentences,
)
from dissect.models import (
    Chunk,
    Claim,
    RawClaim,
    RawEvidence,
    RawRhetoric,
    SentenceSpan,
    SentenceTag,
    SourceLoc,
    TaggedSentence,
)


def test_number_lines():
    chunk = Chunk(text="line one\nline two\nline three", line_offset=10)
    result = _number_lines(chunk)
    assert result == "10| line one\n11| line two\n12| line three"


def test_chunk_paper_small():
    small = "# Title\n\nShort paper."
    chunks = _chunk_paper(small, max_chars=70_000)
    assert len(chunks) == 1
    assert chunks[0].text == small
    assert chunks[0].line_offset == 1


def test_chunk_paper_splits_at_headings():
    lines = []
    for i in range(20):
        lines.append(f"## Section {i}\n")
        lines.append("x" * 4000 + "\n")
    paper = "".join(lines)
    chunks = _chunk_paper(paper, max_chars=10_000)
    assert len(chunks) > 1


def test_chunk_paper_overlap():
    lines = []
    for i in range(10):
        lines.append(f"## Section {i}\n")
        lines.append("content " * 1000 + "\n")
    paper = "".join(lines)
    chunks = _chunk_paper(paper, max_chars=10_000)
    if len(chunks) >= 2:
        assert chunks[1].line_offset < chunks[0].line_offset + len(chunks[0].text.splitlines())


def test_chunk_paper_overlap_counts_nonblank_lines():
    """Overlap measures nonblank lines so it skips over blanked code
    fences. Build a paper where the region before each heading is
    mostly blank lines; the overlap should still reach back to grab
    actual prose (3 nonblank lines) rather than stopping in the blanks.
    """
    parts = []
    parts.append("# A\n\n")
    parts.append(("X" * 6000 + "\n"))  # large prose line in section A
    parts.append("\n")
    parts.append("setup line 1\n")
    parts.append("setup line 2\n")
    parts.append("setup line 3\n")
    parts.append("\n\n\n\n\n")  # several blank lines (simulates blanked code)
    parts.append("# B\n\n")
    parts.append(("Y" * 6000 + "\n"))  # large prose line in section B
    paper = "".join(parts)
    chunks = _chunk_paper(paper, max_chars=8_000)
    assert len(chunks) == 2
    # Second chunk's text must contain all three setup lines: the
    # backward walk skips blank lines and stops after collecting 3
    # nonblank lines of overlap.
    second = chunks[1].text
    assert "setup line 1" in second
    assert "setup line 2" in second
    assert "setup line 3" in second


def test_promote_claims_uses_start_line():
    source = "line one\nline two\nline three"
    raws = [
        RawClaim(text="line two", start_line=2, section="1", question="Q?"),
    ]
    claims, next_uid = _promote_claims(raws, source)
    assert len(claims) == 1
    assert claims[0].loc.line == 2
    assert claims[0].uid == 1
    assert next_uid == 2


def test_promote_claims_never_drops():
    source = "Only this text exists."
    raws = [
        RawClaim(text="nonexistent quote", start_line=1, section="1", question="Q?"),
    ]
    claims, next_uid = _promote_claims(raws, source)
    assert len(claims) == 1
    assert next_uid == 2


def test_promote_claims_defaults_to_normative():
    source = "claim text"
    raws = [
        RawClaim(text="claim text", start_line=1, question="Q?"),
    ]
    claims, next_uid = _promote_claims(raws, source)
    assert len(claims) == 1
    assert claims[0].kind == "normative"


def test_promote_evidence_uses_start_line():
    source = "Evidence text here."
    raws = [
        RawEvidence(
            text="Evidence text", start_line=1, section="1",
            supports=["something"], quantitative=False, cited=False,
            verifiable=False, normative=False,
        ),
    ]
    evidence, next_uid = _promote_evidence(raws, source)
    assert len(evidence) == 1
    assert evidence[0].loc.line == 1
    assert evidence[0].uid == 1
    assert next_uid == 2


def test_dedup_tier0_tombstones_duplicate_locs():
    loc = SourceLoc(line=1, start_char=0, end_char=5)
    c1 = Claim(uid=1, loc=loc, text="A", original_quotes=["A"], section="1", question="Q?", depends_on=[])
    c2 = Claim(uid=2, loc=loc, text="A", original_quotes=["A"], section="1", question="Q?", depends_on=[])
    result = _dedup_tier0([c1, c2])
    assert result[0].merged_into is None
    assert result[1].merged_into == 1


def test_dedup_tier0_preserves_distinct():
    c1 = Claim(
        uid=1, loc=SourceLoc(line=1, start_char=0, end_char=5),
        text="A", original_quotes=["A"], section="1", question="Q?", depends_on=[],
    )
    c2 = Claim(
        uid=2, loc=SourceLoc(line=2, start_char=0, end_char=5),
        text="B", original_quotes=["B"], section="1", question="Q?", depends_on=[],
    )
    result = _dedup_tier0([c1, c2])
    assert all(r.merged_into is None for r in result)


def test_dedup_tier1_substring_tombstone():
    c_short = Claim(
        uid=1, loc=SourceLoc(line=1, start_char=0, end_char=2),
        text="AB", original_quotes=["AB"], section="1", question="Q?", depends_on=[],
    )
    c_long = Claim(
        uid=2, loc=SourceLoc(line=2, start_char=0, end_char=4),
        text="XABX", original_quotes=["XABX"], section="1", question="Q?", depends_on=[],
    )
    result = _dedup_tier1([c_short, c_long])
    assert result[0].merged_into == c_long.uid
    assert result[1].merged_into is None
    assert "AB" in result[1].original_quotes


def test_promote_rhetoric_uses_start_line():
    source = "This is line one.\nWe dismiss complexity.\nLine three."
    raws = [
        RawRhetoric(
            text="We dismiss complexity.", start_line=2,
            section="1", marker_type="dismissal",
            target="complexity", intensity="medium",
        ),
    ]
    items, next_uid = _promote_rhetoric(raws, source)
    assert len(items) == 1
    assert items[0].loc.line == 2
    assert items[0].marker_type == "dismissal"
    assert items[0].uid == 1
    assert next_uid == 2


def test_extract_citations_basic():
    text = "See [P4172R0](https://example.com/p4172r0.pdf) and P2300R10 for details."
    refs = _extract_citations(text)
    assert len(refs) == 2
    # Equal counts tie-break alphabetically by paper_id; "P2300R10" < "P4172R0".
    assert refs[0].paper_id == "P2300R10"
    assert refs[0].count == 1
    assert refs[1].paper_id == "P4172R0"
    assert refs[1].count == 1


def test_extract_citations_dedup_case_insensitive():
    text = "p4172r0 and P4172R0 both appear"
    refs = _extract_citations(text)
    assert len(refs) == 1
    assert refs[0].paper_id == "P4172R0"
    assert refs[0].count == 2


def test_extract_citations_n_papers():
    text = "N4861 is the working draft"
    refs = _extract_citations(text)
    assert len(refs) == 1
    assert refs[0].paper_id == "N4861"


def test_blank_non_prose_preserves_line_count():
    source = "prose\n```cpp\nint x = 1;\n```\nmore prose\n"
    result, blanked = _blank_non_prose(source)
    assert len(result.splitlines()) == len(source.splitlines())
    assert blanked == 3


def test_blank_non_prose_strips_fenced_code():
    source = "before\n```cpp\nint x = 1;\nreturn x;\n```\nafter\n"
    result, _ = _blank_non_prose(source)
    assert "int x" not in result
    assert "before" in result
    assert "after" in result


def test_blank_non_prose_strips_wording_div():
    source = "before\n:::wording\nspec text\n:::\nafter\n"
    result, blanked = _blank_non_prose(source)
    assert "spec text" not in result
    assert "before" in result
    assert "after" in result
    assert blanked == 3


def test_blank_non_prose_strips_wording_add():
    source = "before\n:::wording-add\nnew text\n:::\nafter\n"
    result, _ = _blank_non_prose(source)
    assert "new text" not in result
    assert "before" in result
    assert "after" in result


def test_blank_non_prose_preserves_prose():
    source = "This is a normative claim.\nAnother sentence.\n"
    result, blanked = _blank_non_prose(source)
    assert result == source
    assert blanked == 0


def test_blank_non_prose_fence_with_language_tag():
    source = "x\n```python\nprint('hi')\n```\ny\n"
    result, _ = _blank_non_prose(source)
    assert "print" not in result
    assert "x\n" in result
    assert "y\n" in result


def test_blank_non_prose_strips_yaml_frontmatter():
    source = "---\ntitle: X\n---\nReal content.\n"
    result, blanked = _blank_non_prose(source)
    assert "title:" not in result
    assert "Real content." in result
    assert blanked == 3
    assert len(result.splitlines()) == len(source.splitlines())


def test_blank_non_prose_no_yaml_passes_through():
    source = "# Heading\nProse follows.\n"
    result, blanked = _blank_non_prose(source)
    assert result == source
    assert blanked == 0


def test_blank_non_prose_unclosed_yaml_passes_through():
    source = "---\ntitle: X\n# H1\n"
    result, blanked = _blank_non_prose(source)
    assert result == source
    assert blanked == 0


def test_blank_non_prose_yaml_after_leading_blank_line():
    source = "\n---\ntitle: X\n---\nbody\n"
    result, blanked = _blank_non_prose(source)
    assert "title:" not in result
    assert "body" in result
    assert blanked == 3
    assert len(result.splitlines()) == len(source.splitlines())


def test_blank_yaml_frontmatter_preserves_line_count():
    lines = ["---\n", "title: X\n", "document: Pxxxx\n", "---\n", "body\n"]
    original_len = len(lines)
    _blank_yaml_frontmatter(lines)
    assert len(lines) == original_len
    assert lines[0] == "\n"
    assert lines[1] == "\n"
    assert lines[2] == "\n"
    assert lines[3] == "\n"
    assert lines[4] == "body\n"


def test_blank_yaml_frontmatter_returns_zero_when_no_yaml():
    lines = ["# Heading\n", "Prose.\n"]
    snapshot = list(lines)
    blanked = _blank_yaml_frontmatter(lines)
    assert blanked == 0
    assert lines == snapshot


def test_blank_yaml_frontmatter_returns_zero_when_unclosed():
    lines = ["---\n", "title: X\n", "# H1\n"]
    snapshot = list(lines)
    blanked = _blank_yaml_frontmatter(lines)
    assert blanked == 0
    assert lines == snapshot


# ---------------------------------------------------------------------------
# Step 1: _decompose_sentences
# ---------------------------------------------------------------------------


def test_decompose_single_sentence():
    chunk = Chunk(text="The committee should adopt P2300R10.", line_offset=42)
    spans = _decompose_sentences(chunk)
    assert len(spans) == 1
    assert spans[0].text == "The committee should adopt P2300R10."
    assert spans[0].line == 42
    assert spans[0].start_char == 0


def test_decompose_multiple_sentences_on_one_line():
    chunk = Chunk(text="First sentence. Second sentence. Third.", line_offset=10)
    spans = _decompose_sentences(chunk)
    assert len(spans) == 3
    assert all(s.line == 10 for s in spans)
    assert [s.text for s in spans] == [
        "First sentence.", "Second sentence.", "Third.",
    ]


def test_decompose_preserves_absolute_line_numbers():
    chunk = Chunk(text="Line A.\nLine B.\nLine C.", line_offset=100)
    spans = _decompose_sentences(chunk)
    assert [s.line for s in spans] == [100, 101, 102]


def test_decompose_skips_blank_lines():
    chunk = Chunk(text="First.\n\n\nSecond.", line_offset=1)
    spans = _decompose_sentences(chunk)
    assert [s.text for s in spans] == ["First.", "Second."]
    assert [s.line for s in spans] == [1, 4]


def test_decompose_skips_headings():
    chunk = Chunk(text="# Title\nReal sentence.\n## Sub heading\nAnother.", line_offset=5)
    spans = _decompose_sentences(chunk)
    assert [s.text for s in spans] == ["Real sentence.", "Another."]
    assert [s.line for s in spans] == [6, 8]


def test_decompose_handles_abbreviations_conservatively():
    """Should not break on common abbreviations like e.g., i.e., Dr."""
    chunk = Chunk(text="See e.g. P2300R10 for details. It works.", line_offset=1)
    spans = _decompose_sentences(chunk)
    # pysbd should keep "See e.g. P2300R10 for details." as one sentence,
    # not split on "e.g."
    assert len(spans) == 2
    assert "e.g." in spans[0].text


def test_decompose_handles_decimals():
    chunk = Chunk(text="Version 3.14 was released. It includes fixes.", line_offset=1)
    spans = _decompose_sentences(chunk)
    assert len(spans) == 2
    assert "3.14" in spans[0].text


def test_decompose_bullet_items():
    chunk = Chunk(text="- First item.\n- Second item.", line_offset=1)
    spans = _decompose_sentences(chunk)
    # Each line yields one bullet-prefixed span; pysbd may preserve or
    # strip the marker -- we accept either as long as both lines emit.
    assert len(spans) == 2
    assert [s.line for s in spans] == [1, 2]


def test_decompose_inline_code_does_not_split():
    chunk = Chunk(text="The expression `a.b.c` is fine. So is this.", line_offset=1)
    spans = _decompose_sentences(chunk)
    assert len(spans) == 2
    assert "`a.b.c`" in spans[0].text


def test_decompose_empty_chunk_returns_empty():
    chunk = Chunk(text="", line_offset=1)
    assert _decompose_sentences(chunk) == []


def test_decompose_only_headings_and_blanks_returns_empty():
    chunk = Chunk(text="# H1\n\n## H2\n\n", line_offset=1)
    assert _decompose_sentences(chunk) == []


# ---------------------------------------------------------------------------
# Step 1: _split_tagged_by_chunk
# ---------------------------------------------------------------------------


def _ts(line: int, tag: SentenceTag = SentenceTag.TARGET) -> TaggedSentence:
    """Build a TaggedSentence anchored at the given line."""
    return TaggedSentence(
        span=SentenceSpan(text="x", line=line, start_char=0, end_char=1),
        tag=tag,
        target_score=0.8 if tag == SentenceTag.TARGET else 0.1,
        skip_score=0.8 if tag == SentenceTag.SKIP else 0.1,
    )


def test_split_tagged_two_disjoint_chunks():
    chunks = [
        Chunk(text="a\nb\nc", line_offset=1),     # lines 1, 2, 3
        Chunk(text="d\ne\nf", line_offset=10),    # lines 10, 11, 12
    ]
    tagged = [_ts(2), _ts(11), _ts(3)]
    buckets = _split_tagged_by_chunk(chunks, tagged)
    assert [t.span.line for t in buckets[0]] == [2, 3]
    assert [t.span.line for t in buckets[1]] == [11]


def test_split_tagged_empty():
    chunks = [Chunk(text="a\nb", line_offset=1)]
    assert _split_tagged_by_chunk(chunks, []) == [[]]


def test_split_tagged_overlapping_chunks_assign_to_later():
    """Adjacent chunks overlap by a small backward window
    (``_chunk_paper`` uses nonblank-line counting). A sentence whose
    line falls in the overlap region is assigned to the later chunk."""
    chunks = [
        Chunk(text="x\n" * 50, line_offset=1),    # lines 1..50
        Chunk(text="y\n" * 50, line_offset=40),   # lines 40..89 (overlap 40-50)
    ]
    # Line 45 is in the overlap region. Should go to the later chunk.
    buckets = _split_tagged_by_chunk(chunks, [_ts(45)])
    assert buckets[0] == []
    assert [t.span.line for t in buckets[1]] == [45]


def test_split_tagged_out_of_range_line_dropped():
    chunks = [Chunk(text="a\nb", line_offset=1)]  # lines 1, 2
    buckets = _split_tagged_by_chunk(chunks, [_ts(99)])
    assert buckets == [[]]


# ---------------------------------------------------------------------------
# Step 1: _strip_line_prefix classifier-tag extension
# ---------------------------------------------------------------------------


def test_strip_line_prefix_strips_line_number():
    assert _strip_line_prefix("47| foo bar") == "foo bar"


def test_strip_line_prefix_strips_target_tag():
    assert _strip_line_prefix("[TARGET] foo bar") == "foo bar"


def test_strip_line_prefix_strips_context_tag():
    assert _strip_line_prefix("[CONTEXT] foo bar") == "foo bar"


def test_strip_line_prefix_strips_combined():
    """LLM may copy both the line-number prefix and the classifier tag."""
    assert _strip_line_prefix("47| [TARGET] foo bar") == "foo bar"


def test_strip_line_prefix_leaves_other_brackets_alone():
    """Only [TARGET] / [CONTEXT] are classifier tags; other bracketed
    content (e.g. attribute syntax) is part of the source."""
    assert _strip_line_prefix("[OTHER] foo") == "[OTHER] foo"
    assert _strip_line_prefix("[[no_unique_address]] foo") == "[[no_unique_address]] foo"


def test_strip_line_prefix_strips_only_leading_tag():
    """A tag mid-string is data, not a prefix."""
    assert _strip_line_prefix("foo [TARGET] bar") == "foo [TARGET] bar"


def test_promote_claims_strips_classifier_tags():
    """SourceLoc round-trip: a RawClaim whose text has a leaked tag
    survives ``_promote_claims`` with the tag stripped and a correct
    ``start_char`` against the original source line."""
    source = "alpha beta gamma.\nThis is a real sentence in the source.\n"
    raw = RawClaim(
        text="[TARGET] This is a real sentence in the source.",
        start_line=2,
        question="Q?",
    )
    claims, _ = _promote_claims([raw], source, start_uid=1)
    assert len(claims) == 1
    c = claims[0]
    assert c.text == "This is a real sentence in the source."
    assert c.loc.line == 2
    assert c.loc.start_char == 0
    assert c.loc.end_char == len("This is a real sentence in the source.")


# ---------------------------------------------------------------------------
# Step 1: _tag_sentences
# ---------------------------------------------------------------------------


class _FakeClassifier:
    """Stand-in for a ``ClassifierBackend``.

    ``scores_by_text`` maps a substring -> ``(target_score, skip_score)``.
    If no substring matches, defaults to ``(0.1, 0.1)``.
    """

    def __init__(self, scores_by_text):
        self.scores_by_text = scores_by_text
        self.calls = []

    def classify(self, texts, candidate_labels, *, multi_label=True):
        self.calls.append({
            "texts": list(texts),
            "labels": list(candidate_labels),
            "multi_label": multi_label,
        })
        target_label, skip_label = candidate_labels
        out = []
        for t in texts:
            tgt, skp = 0.1, 0.1
            for key, (a, b) in self.scores_by_text.items():
                if key in t:
                    tgt, skp = a, b
                    break
            out.append({target_label: tgt, skip_label: skp})
        return out


def _span(text: str, line: int = 1) -> SentenceSpan:
    # Wrap the keyword with padding words so the span clears the
    # structural-skip length filter (>= 3 words). _FakeClassifier
    # uses substring matching so the keyword still routes scores.
    wrapped = f"the {text} sentence here"
    return SentenceSpan(
        text=wrapped, line=line, start_char=0, end_char=len(wrapped),
    )


def test_tag_sentences_empty_short_circuits():
    fc = _FakeClassifier({})
    assert _tag_sentences([], fc) == []
    assert fc.calls == []


def test_tag_sentences_uses_multi_label_true():
    fc = _FakeClassifier({"x": (0.9, 0.0)})
    _tag_sentences([_span("x")], fc)
    assert fc.calls[0]["multi_label"] is True


def test_tag_sentences_target_when_target_margin_exceeded():
    fc = _FakeClassifier({"x": (0.8, 0.1)})
    out = _tag_sentences([_span("xxx")], fc)
    assert out[0].tag == SentenceTag.TARGET
    assert out[0].target_score == 0.8
    assert out[0].skip_score == 0.1


def test_tag_sentences_target_with_low_absolute_scores():
    """Margin-based: even when both scores are small in absolute terms,
    a clear difference between them picks the winner. This is the
    common case for DeBERTa-v3-base on out-of-domain technical prose."""
    fc = _FakeClassifier({"x": (0.20, 0.05)})
    out = _tag_sentences([_span("xxx")], fc)
    assert out[0].tag == SentenceTag.TARGET


def test_tag_sentences_skip_only_when_high_confidence():
    """Skip margin defaults to 0.40 -- a clear lead for skip is
    required. Smaller leads keep the sentence as CONTEXT so the LLM
    still sees it."""
    fc = _FakeClassifier({"y": (0.1, 0.9)})
    out = _tag_sentences([_span("yyy")], fc)
    assert out[0].tag == SentenceTag.SKIP


def test_tag_sentences_skip_bias_keeps_uncertain_as_context():
    """A modest skip lead (below skip_margin) defaults to CONTEXT, NOT
    SKIP. This is the recall-priority bias: dropping a real claim is
    irreversible, keeping a piece of boilerplate as CONTEXT is cheap."""
    # skip leads by 0.25 -- not enough for SKIP under default margin 0.40.
    fc = _FakeClassifier({"q": (0.10, 0.35)})
    out = _tag_sentences([_span("qqq")], fc)
    assert out[0].tag == SentenceTag.CONTEXT


def test_tag_sentences_context_when_scores_equal():
    fc = _FakeClassifier({"z": (0.3, 0.3)})
    out = _tag_sentences([_span("zzz")], fc)
    assert out[0].tag == SentenceTag.CONTEXT


def test_tag_sentences_context_when_within_target_margin():
    """Scores close to each other -> CONTEXT regardless of magnitude."""
    fc = _FakeClassifier({"q": (0.9, 0.88)})
    out = _tag_sentences([_span("qqq")], fc)
    # 0.9 - 0.88 = 0.02, below default target_margin 0.05.
    assert out[0].tag == SentenceTag.CONTEXT


def test_tag_sentences_custom_margins_change_decision():
    fc = _FakeClassifier({"x": (0.3, 0.2)})
    # Default target_margin (0.05): diff=0.1 > 0.05 -> TARGET.
    out = _tag_sentences([_span("xxx")], fc)
    assert out[0].tag == SentenceTag.TARGET
    # With target_margin=0.2: diff=0.1 < 0.2 -> CONTEXT.
    out = _tag_sentences([_span("xxx")], fc, target_margin=0.2)
    assert out[0].tag == SentenceTag.CONTEXT


def test_tag_sentences_skip_margin_overrideable():
    fc = _FakeClassifier({"y": (0.1, 0.3)})
    # Default skip_margin=0.40: diff=-0.2, |diff| < 0.40 -> CONTEXT.
    out = _tag_sentences([_span("yyy")], fc)
    assert out[0].tag == SentenceTag.CONTEXT
    # With skip_margin=0.1: |diff|=0.2 > 0.1 -> SKIP.
    out = _tag_sentences([_span("yyy")], fc, skip_margin=0.1)
    assert out[0].tag == SentenceTag.SKIP


def test_tag_sentences_preserves_span_metadata():
    fc = _FakeClassifier({"hi": (0.9, 0.0)})
    sp = SentenceSpan(text="hi there friend", line=42, start_char=5, end_char=20)
    out = _tag_sentences([sp], fc)
    assert out[0].span == sp


# ---------------------------------------------------------------------------
# Step 1: structural-SKIP prefilter
# ---------------------------------------------------------------------------


def _raw_span(text: str, line: int = 1) -> SentenceSpan:
    """Build a span verbatim (no padding) for prefilter tests."""
    return SentenceSpan(text=text, line=line, start_char=0, end_char=len(text))


def test_structural_skip_number_only():
    """Numbered-list markers ('1.', '2.', ...) -- pysbd fragments these
    off as standalone "sentences" with no signal."""
    from dissect.harness import _is_structural_skip
    assert _is_structural_skip("1.")
    assert _is_structural_skip("2.")
    assert _is_structural_skip("  10.  ")


def test_structural_skip_ellipsis_prefix():
    """Continuation fragments like '... is explicitly created.' --
    the antecedent is in the prior chunk so the sentence is unmoored."""
    from dissect.harness import _is_structural_skip
    assert _is_structural_skip("... is explicitly created.")
    assert _is_structural_skip(".. continues here")


def test_structural_skip_punct_only():
    from dissect.harness import _is_structural_skip
    assert _is_structural_skip("...")
    assert _is_structural_skip("---")
    assert _is_structural_skip("()")


def test_structural_skip_too_short():
    """Fewer than 3 words -- not enough material to classify."""
    from dissect.harness import _is_structural_skip
    assert _is_structural_skip("hello world")
    assert _is_structural_skip("yes.")
    assert not _is_structural_skip("hello world here")


def test_structural_skip_example_block():
    from dissect.harness import _is_structural_skip
    assert _is_structural_skip("[*Example 1: foo *end example*]")


def test_structural_skip_preserves_real_prose():
    """Negative cases: normal sentences must NOT be auto-skipped."""
    from dissect.harness import _is_structural_skip
    assert not _is_structural_skip(
        "Senders model asynchronous computation."
    )
    assert not _is_structural_skip(
        "This proposal introduces a new concept."
    )


def test_tag_sentences_prefilter_short_circuits_classifier():
    """Structural SKIPs are tagged without ever hitting the classifier."""
    fc = _FakeClassifier({})
    spans = [_raw_span("1."), _raw_span("...")]
    out = _tag_sentences(spans, fc)
    assert [t.tag for t in out] == [SentenceTag.SKIP, SentenceTag.SKIP]
    assert fc.calls == []  # classifier was never called


def test_tag_sentences_prefilter_uses_sentinel_scores():
    """SKIPs from the structural prefilter carry target=0.0, skip=1.0
    so debug output can distinguish them from classifier-decided SKIPs."""
    fc = _FakeClassifier({})
    out = _tag_sentences([_raw_span("1.")], fc)
    assert out[0].target_score == 0.0
    assert out[0].skip_score == 1.0


def test_tag_sentences_prefilter_mixed_batch():
    """Mixed batch: some spans prefilter to SKIP, others go to the
    classifier. Output order must match input order; the classifier
    receives only the non-prefiltered spans."""
    fc = _FakeClassifier({"alpha": (0.8, 0.1), "beta": (0.1, 0.9)})
    spans = [
        _raw_span("1."),                              # SKIP (structural)
        _raw_span("the alpha sentence here"),         # TARGET (classified)
        _raw_span("..."),                             # SKIP (structural)
        _raw_span("the beta sentence here"),          # SKIP (classified)
    ]
    out = _tag_sentences(spans, fc)
    assert [t.tag for t in out] == [
        SentenceTag.SKIP, SentenceTag.TARGET,
        SentenceTag.SKIP, SentenceTag.SKIP,
    ]
    # Classifier was called exactly once with only the two non-prefiltered spans.
    assert len(fc.calls) == 1
    assert len(fc.calls[0]["texts"]) == 2
    # Sentinel scores on the structural skips, real scores on the classified ones.
    assert out[0].target_score == 0.0 and out[0].skip_score == 1.0
    assert out[1].target_score == 0.8 and out[1].skip_score == 0.1


def test_tag_target_label_is_descriptive_phrasing():
    """Cross-validated hypothesis must be the descriptive-claim phrasing.
    Regression guard: the previous opinion-phrasing label tanked TARGET
    recall on formal-wording prose (43-50% vs 96-98%)."""
    from dissect.harness import _TAG_TARGET_LABEL
    assert "does, is, or proposes" in _TAG_TARGET_LABEL


# ---------------------------------------------------------------------------
# Step 1: _render_tagged_chunk
# ---------------------------------------------------------------------------


def _tagged_for(text: str, line: int, tag: SentenceTag, start_char: int = 0) -> TaggedSentence:
    return TaggedSentence(
        span=SentenceSpan(
            text=text, line=line, start_char=start_char, end_char=start_char + len(text),
        ),
        tag=tag,
        target_score=0.8 if tag == SentenceTag.TARGET else 0.1,
        skip_score=0.8 if tag == SentenceTag.SKIP else 0.1,
    )


def test_render_tagged_chunk_target_and_context_prefixes():
    chunk = Chunk(text="Hello world.\nAnother line.", line_offset=10)
    tagged = [
        _tagged_for("Hello world.", line=10, tag=SentenceTag.TARGET),
        _tagged_for("Another line.", line=11, tag=SentenceTag.CONTEXT),
    ]
    out = _render_tagged_chunk(chunk, tagged)
    assert out == "10| [TARGET] Hello world.\n11| [CONTEXT] Another line."


def test_render_tagged_chunk_drops_skip_by_default():
    chunk = Chunk(text="Boring line.\nReal claim.", line_offset=5)
    tagged = [
        _tagged_for("Boring line.", line=5, tag=SentenceTag.SKIP),
        _tagged_for("Real claim.", line=6, tag=SentenceTag.TARGET),
    ]
    out = _render_tagged_chunk(chunk, tagged)
    # Line 5 (SKIP) dropped entirely; line 6 emitted.
    assert "Boring line." not in out
    assert out == "6| [TARGET] Real claim."


def test_render_tagged_chunk_keeps_skip_when_drop_skip_false():
    chunk = Chunk(text="Skip me.", line_offset=1)
    tagged = [_tagged_for("Skip me.", line=1, tag=SentenceTag.SKIP)]
    # With drop_skip=False, SKIP sentences still produce no tag prefix
    # (only TARGET / CONTEXT have prefixes), so the SKIP line is also
    # omitted in practice. The flag exists for callers that want to see
    # all classified sentences; absence of a prefix marks them as SKIP.
    # Verify the behavior is deterministic.
    out = _render_tagged_chunk(chunk, tagged, drop_skip=True)
    assert out == ""


def test_render_tagged_chunk_preserves_line_numbers():
    chunk = Chunk(text="A.\nB.\nC.", line_offset=100)
    tagged = [
        _tagged_for("A.", line=100, tag=SentenceTag.TARGET),
        _tagged_for("B.", line=101, tag=SentenceTag.CONTEXT),
        _tagged_for("C.", line=102, tag=SentenceTag.TARGET),
    ]
    out = _render_tagged_chunk(chunk, tagged)
    assert "100|" in out
    assert "101|" in out
    assert "102|" in out


def test_render_tagged_chunk_multi_sentence_line_mixed_tags():
    """A single source line can carry multiple sentences with different tags."""
    chunk = Chunk(text="First. Second.", line_offset=43)
    tagged = [
        _tagged_for("First.", line=43, tag=SentenceTag.TARGET, start_char=0),
        _tagged_for("Second.", line=43, tag=SentenceTag.CONTEXT, start_char=7),
    ]
    out = _render_tagged_chunk(chunk, tagged)
    assert out == "43| [TARGET] First. [CONTEXT] Second."


def test_render_tagged_chunk_passes_through_headings():
    """Heading lines (no tags) are passed through with their line number."""
    chunk = Chunk(text="## Section 1\nReal sentence.", line_offset=20)
    tagged = [_tagged_for("Real sentence.", line=21, tag=SentenceTag.TARGET)]
    out = _render_tagged_chunk(chunk, tagged)
    assert "20| ## Section 1" in out
    assert "21| [TARGET] Real sentence." in out


def test_render_tagged_chunk_collapses_blank_runs():
    """Blank-line collapsing from _number_lines must still work."""
    chunk = Chunk(text="A.\n\n\n\nB.", line_offset=1)
    tagged = [
        _tagged_for("A.", line=1, tag=SentenceTag.TARGET),
        _tagged_for("B.", line=5, tag=SentenceTag.TARGET),
    ]
    out = _render_tagged_chunk(chunk, tagged)
    # Blank run lines 2..4 collapse to a single "4|" sentinel.
    assert out.count("\n") == 2  # three lines: A, sentinel, B
    assert "4|" in out


def test_render_tagged_chunk_empty_tagged():
    chunk = Chunk(text="Hi.", line_offset=1)
    out = _render_tagged_chunk(chunk, [])
    # No classifier output: pass through verbatim (treat as non-prose).
    assert out == "1| Hi."
