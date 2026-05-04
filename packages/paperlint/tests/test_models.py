#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Shape tests for the output-schema dataclasses in paperlint.models."""

from __future__ import annotations

from dataclasses import fields

from paperlint.models import (
    Paper,
)


def test_paper_matches_schema() -> None:
    """Pin Paper's field names to the papers table schema."""
    expected_names = {
        "document_id", "year", "title", "authors", "mailing_date",
        "document_date", "audience", "intent", "url", "source_file", "markdown_path",
    }
    actual_names = {f.name for f in fields(Paper)}
    assert actual_names == expected_names


def test_paper_instantiates_with_all_fields() -> None:
    p = Paper(
        document_id="P3642R4",
        year="2026",
        title="Carry-less product: std::clmul",
        authors=["Jan Schultke"],
        mailing_date="2026-02-15",
        document_date="2026-01-15",
        audience="LEWG",
        intent="ask",
        url="https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2026/p3642r4.html",
        source_file="",
        markdown_path="",
    )
    assert p.document_id == "P3642R4"
    assert p.audience == "LEWG"
    assert p.intent == "ask"
    assert p.year == "2026"
