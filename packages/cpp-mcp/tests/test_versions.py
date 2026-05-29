#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for standard version resolution."""

from __future__ import annotations

import pytest

from cpp_mcp.versions import resolve_draft_for_version, resolve_version


class TestResolveVersion:
    @pytest.mark.parametrize(
        "tag, expected_version, expected_note",
        [
            ("main", "C++29", "bleeding edge trunk"),
            ("n5046", "C++26", "final working draft"),
            ("n4950", "C++23", "final working draft"),
            ("n4861", "C++20", "final working draft"),
            ("n4659", "C++17", "final working draft"),
            ("n4140", "C++14", "final working draft"),
            ("n3337", "C++11", "post-publication draft"),
        ],
    )
    def test_known_tags(
        self, tag: str, expected_version: str, expected_note: str
    ) -> None:
        version, note = resolve_version(tag)
        assert version == expected_version
        assert note == expected_note

    @pytest.mark.parametrize(
        "tag, expected_version",
        [
            ("n5100", "C++29"),
            ("n5010", "C++26"),
            ("n4900", "C++23"),
            ("n4700", "C++20"),
            ("n4200", "C++17"),
            ("n3500", "C++14"),
            ("n3000", "C++11"),
        ],
    )
    def test_numeric_range_inference(self, tag: str, expected_version: str) -> None:
        version, note = resolve_version(tag)
        assert version == expected_version
        assert note == "working draft"

    def test_non_numeric_tag(self) -> None:
        version, note = resolve_version("some-branch")
        assert version == "unknown"
        assert note == "unknown tag"

    def test_boundary_above_c26_max(self) -> None:
        version, note = resolve_version("n5047")
        assert version == "C++29"
        assert note == "working draft"

    def test_boundary_at_c26_max_is_known(self) -> None:
        version, note = resolve_version("n5046")
        assert version == "C++26"
        assert note == "final working draft"


class TestResolveDraftForVersion:
    def test_match_found(self) -> None:
        result = resolve_draft_for_version("C++23", ["n4950", "n5046"])
        assert result == "n4950"

    def test_picks_highest_matching_tag(self) -> None:
        result = resolve_draft_for_version("C++26", ["n5001", "n5046", "n5008"])
        assert result == "n5046"

    def test_no_match(self) -> None:
        result = resolve_draft_for_version("C++11", ["n4950", "n5046"])
        assert result is None

    def test_case_insensitive(self) -> None:
        result = resolve_draft_for_version("c++23", ["n4950", "n5046"])
        assert result == "n4950"

    def test_empty_available_tags(self) -> None:
        result = resolve_draft_for_version("C++23", [])
        assert result is None

    def test_all_tags_different_version(self) -> None:
        result = resolve_draft_for_version("C++20", ["n4950", "n5046", "n5001"])
        assert result is None

    def test_single_matching_tag(self) -> None:
        result = resolve_draft_for_version("C++26", ["n5001"])
        assert result == "n5001"
