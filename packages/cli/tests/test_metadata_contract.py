"""Tests for the index-authoritative metadata contract.

Covers:
- _validate_targets: target type validation for the paperflow CLI.
- _infer_intent: title-prefix detection for the mailing scraper.

These are pure-function tests; no network, no LLM, no filesystem.
"""

import pytest

from cli.errors import EmptyTargetsError, MixedTargetsError
from cli.jobs import _validate_targets
from mailing.scrape import _infer_intent


class TestValidateTargets:
    def test_all(self):
        assert _validate_targets(["all"]) == "all"

    def test_years(self):
        assert _validate_targets(["2026"]) == "years"
        assert _validate_targets(["2025", "2026"]) == "years"

    def test_papers(self):
        assert _validate_targets(["P3642R4"]) == "papers"
        assert _validate_targets(["P3642R4", "P3700R0"]) == "papers"

    def test_mixing_raises(self):
        with pytest.raises(MixedTargetsError, match="mix"):
            _validate_targets(["2026", "P3642R4"])

    def test_empty_raises(self):
        with pytest.raises(EmptyTargetsError):
            _validate_targets([])


class TestInferIntent:
    def test_info_prefix_returns_info(self):
        assert _infer_intent("Info: Some Informational Topic") == "info"

    def test_ask_prefix_returns_ask(self):
        assert _infer_intent("Ask: Should we do X?") == "ask"

    def test_neutral_title_returns_none(self):
        assert _infer_intent("Carry-less product: std::clmul") is None

    def test_empty_title_returns_none(self):
        assert _infer_intent("") is None

    def test_info_prefix_requires_exact_case(self):
        assert _infer_intent("info: lowercase") is None

    def test_ask_prefix_requires_exact_case(self):
        assert _infer_intent("ask: lowercase") is None

    def test_info_not_mid_title(self):
        assert _infer_intent("Some Info: thing") is None
