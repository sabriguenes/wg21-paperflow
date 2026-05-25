#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for tomd.lib.check_content (content-coverage check)."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paperstore import SqliteBackend

from tomd.errors import CheckContentArgError
from tomd.lib.check_content import (
    MisalignedRegion,
    _dedup_regions,
    _dehyphenate,
    _extract_markdown_stream,
    _local_coverage_windows,
    _multiset_coverage,
    _normalize,
    _shingle_hashes,
    _strip_repeating_lines,
    _tokenize,
    check_paper_content,
)


class TestNormalize:
    def test_smart_quotes_folded(self):
        # Verify smart quotes match their ASCII forms after normalization.
        a = _normalize("It’s “quoted”")
        b = _normalize("It's \"quoted\"")
        assert a == b

    def test_em_dash_folded(self):
        assert _normalize("foo—bar") == _normalize("foo-bar")

    def test_nbsp_collapsed(self):
        assert _normalize("foo bar") == "foo bar"

    def test_lowercase(self):
        assert _normalize("Hello World") == "hello world"

    def test_punct_stripped(self):
        assert _normalize("hello, world.") == "hello world"


class TestDehyphenate:
    def test_split_word_joined(self):
        assert _dehyphenate("imple-\nmentation") == "implementation"

    def test_compound_prefix_preserved(self):
        assert _dehyphenate("self-\naware") == "self-aware"
        assert _dehyphenate("non-\nzero") == "non-zero"

    def test_no_hyphen_break_left_alone(self):
        assert _dehyphenate("plain text") == "plain text"


class TestTokenize:
    def test_simple(self):
        assert _tokenize("the quick brown fox") == [
            "the", "quick", "brown", "fox",
        ]

    def test_empty(self):
        assert _tokenize("") == []

    def test_multiple_spaces(self):
        assert _tokenize("a   b") == ["a", "b"]


class TestShingles:
    def test_width_5_produces_n_minus_4_shingles(self):
        toks = ["a", "b", "c", "d", "e", "f"]
        hashes = _shingle_hashes(toks, width=5)
        assert len(hashes) == 2

    def test_short_stream_yields_none(self):
        assert _shingle_hashes(["a", "b"], width=5) == []

    def test_identical_streams_identical_hashes(self):
        a = _shingle_hashes(["one", "two", "three", "four", "five"])
        b = _shingle_hashes(["one", "two", "three", "four", "five"])
        assert a == b

    def test_one_change_disrupts_local_shingles(self):
        a = _shingle_hashes(["one", "two", "three", "four", "five"])
        b = _shingle_hashes(["one", "two", "REPLACED", "four", "five"])
        assert a != b


class TestMultisetCoverage:
    def test_full_overlap(self):
        s = [1, 2, 3]
        assert _multiset_coverage(s, s) == 1.0

    def test_partial(self):
        # 2 of 4 source shingles are present in target -> coverage 0.5.
        s = [1, 2, 3, 4]
        t = [1, 2, 99]
        assert _multiset_coverage(s, t) == 0.5

    def test_multiset_semantics(self):
        # Source has two copies of 1; target has one. Coverage 0.5.
        s = [1, 1]
        t = [1]
        assert _multiset_coverage(s, t) == 0.5

    def test_empty_source_is_1(self):
        assert _multiset_coverage([], [1, 2, 3]) == 1.0


class TestLocalWindows:
    def test_no_low_windows_when_full_coverage(self):
        tokens = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"] * 30
        hashes = _shingle_hashes(tokens)
        windows = _local_coverage_windows(hashes, set(hashes), window_tokens=50)
        assert windows == []

    def test_dropped_middle_flagged(self):
        # Source has a long body of shared tokens with a 100-token foreign
        # passage spliced in the middle; markdown has just the shared body.
        shared = ["alpha", "beta", "gamma", "delta", "eps"] * 60
        foreign = ["uniqA", "uniqB", "uniqC", "uniqD", "uniqE"] * 20
        src = shared[:150] + foreign + shared[150:]
        md = shared
        src_hashes = _shingle_hashes(src)
        md_hashes = _shingle_hashes(md)
        windows = _local_coverage_windows(
            src_hashes, set(md_hashes), window_tokens=200,
        )
        # At least one window should land inside the foreign block.
        assert windows, "expected at least one low-coverage window"


class TestDedupRegions:
    def test_keeps_disjoint(self):
        regions = [
            MisalignedRegion("source", 0, 10, "a", None),
            MisalignedRegion("source", 20, 30, "b", None),
        ]
        assert _dedup_regions(regions) == regions

    def test_merges_overlap(self):
        regions = [
            MisalignedRegion("source", 0, 10, "a", None),
            MisalignedRegion("source", 5, 20, "b", None),
        ]
        result = _dedup_regions(regions)
        assert len(result) == 1
        assert result[0].token_start == 0
        assert result[0].token_end == 20


class TestStripRepeatingLines:
    def test_drops_repeated_header(self):
        pages = [
            "Running Title\nFirst page content here.",
            "Running Title\nSecond page content here.",
            "Running Title\nThird page content here.",
            "Running Title\nFourth page content here.",
        ]
        cleaned = _strip_repeating_lines(pages)
        for page in cleaned:
            assert "Running Title" not in page

    def test_keeps_unique_lines(self):
        pages = [
            "Running Title\nPage one body.",
            "Running Title\nPage two body.",
        ]
        cleaned = _strip_repeating_lines(pages)
        # Below threshold (need >=2 pages, ratio 0.5 of 2 = 1 -> threshold 2).
        # Two pages with the same header should be dropped.
        for page in cleaned:
            assert "Running Title" not in page

    def test_short_doc_no_op(self):
        assert _strip_repeating_lines([]) == []


class TestMarkdownStream:
    def test_strips_front_matter(self):
        md = (
            "---\n"
            "title: \"Sample\"\n"
            "document: P9999R0\n"
            "---\n"
            "\n"
            "Body text here.\n"
        )
        tokens = _extract_markdown_stream(md)
        assert "title" not in tokens
        assert "body" in tokens
        assert "text" in tokens
        assert "here" in tokens

    def test_extracts_paragraph(self):
        tokens = _extract_markdown_stream("The quick brown fox.")
        assert tokens == ("the", "quick", "brown", "fox")

    def test_extracts_code_block(self):
        md = "Body.\n\n```cpp\nvoid foo();\n```\n"
        tokens = _extract_markdown_stream(md)
        assert "foo" in tokens
        assert "void" in tokens

    def test_extracts_table(self):
        md = (
            "| col_alpha | col_beta |\n"
            "| --------- | -------- |\n"
            "| cell_one  | cell_two |\n"
        )
        tokens = _extract_markdown_stream(md)
        assert "cell_one" in tokens
        assert "cell_two" in tokens
        assert "col_alpha" in tokens

    def test_strips_tomd_uncertain_markers(self):
        md = "Body before.\n\n<!-- tomd:uncertain:L10-L20 -->\n\nBody after.\n"
        tokens = _extract_markdown_stream(md)
        assert "tomd" not in tokens
        assert "uncertain" not in tokens
        assert "before" in tokens
        assert "after" in tokens


# -- Integration tests against a real SqliteBackend ---------------------------

_LONG_BODY = (
    "Section one introduces the proposal. "
    "The motivation section explains why current C++ language facilities "
    "are insufficient for the use case under consideration. "
    "The design notes section walks through the chosen interface in detail. "
    "Implementation experience demonstrates that the design is feasible "
    "on every major standard library implementation in widespread use today. "
    "Acknowledgements are recorded for the reviewers who provided early "
    "feedback on the initial revision of this document. "
    "References list the prior work this proposal builds upon. "
)


def _stage_html_paper(store: SqliteBackend, pid: str, html_body: str, md_body: str) -> None:
    store.upsert_year("2026", [{"paper_id": pid.upper(), "title": "Sample"}])
    html_doc = f"<html><body>{html_body}</body></html>"
    store.put_source(pid, html_doc.encode("utf-8"), suffix=".html")
    store.write_paper_md(pid, md_body)


class TestIntegrationCoverage:
    def test_identical_streams_high_coverage(self, tmp_path):
        store = SqliteBackend(tmp_path)
        body = _LONG_BODY * 3
        _stage_html_paper(store, "P0001", body, body)
        result = check_paper_content("P0001", store)
        assert result.source_format == "html"
        assert result.coverage > 0.95
        assert result.missing_regions == ()

    def test_dropped_paragraph_flagged(self, tmp_path):
        store = SqliteBackend(tmp_path)
        full = _LONG_BODY * 3
        # Drop a contiguous slice from the middle.
        dropped = (
            "Section one introduces the proposal. " * 3
            + "References list the prior work this proposal builds upon. " * 3
        )
        _stage_html_paper(store, "P0002", full, dropped)
        result = check_paper_content("P0002", store)
        assert result.coverage < 0.85
        assert result.missing_regions, "expected at least one missing region"

    def test_hallucinated_content_drifts(self, tmp_path):
        store = SqliteBackend(tmp_path)
        source = _LONG_BODY * 3
        # Markdown contains the source plus a wholly invented passage that
        # shares no shingles with the source.
        fake = (
            "QUANTUM XYLOPHONE DEFENESTRATION VORPAL BANDERSNATCH MIMSY "
            "BOROGOVE OUTGRABE CALLOO CALLAY FRABJOUS JABBERWOCK "
        ) * 8
        md = source + "\n\n" + fake
        _stage_html_paper(store, "P0003", source, md)
        result = check_paper_content("P0003", store)
        assert result.drift > 0.1
        assert result.extra_regions, "expected at least one extra region"

    def test_unsupported_suffix_raises(self, tmp_path):
        store = SqliteBackend(tmp_path)
        store.upsert_year("2026", [{"paper_id": "P0004", "title": "x"}])
        store.put_source("P0004", b"%PDF-bogus", suffix=".pdf")
        # Rewrite the file with a non-PDF extension to trigger the value
        # error path; the backend writes lowercase stems.
        src = store.get_source_path("P0004")
        new_path = src.with_suffix(".txt")
        src.rename(new_path)
        store.record_source("P0004", new_path)
        store.write_paper_md("P0004", "body\n")
        with pytest.raises(CheckContentArgError):
            check_paper_content("P0004", store)


class TestPerformanceBudget:
    """Synthetic 50k-token paper must finish well under p95=5s budget."""

    def test_long_paper_under_budget(self, tmp_path):
        # ~50k tokens of body text with a deliberate 1k-token gap dropped
        # from the markdown side; locality detection must still fire.
        # Tokens are uniquely numbered so dropped content cannot match
        # repeated phrasing elsewhere in the document.
        store = SqliteBackend(tmp_path)
        body_tokens = [f"tok{i:06d}" for i in range(53_000)]
        body_text = " ".join(body_tokens)
        md_tokens = body_tokens[:25_000] + body_tokens[26_000:]
        md_text = " ".join(md_tokens)
        _stage_html_paper(store, "P0500", body_text, md_text)

        t0 = time.monotonic()
        result = check_paper_content("P0500", store)
        elapsed = time.monotonic() - t0

        # Budget per plan: p95 < 5 s/paper.
        assert elapsed < 5.0, f"check_paper_content took {elapsed:.2f}s"
        assert result.missing_regions, "expected to detect the 1k-token drop"
