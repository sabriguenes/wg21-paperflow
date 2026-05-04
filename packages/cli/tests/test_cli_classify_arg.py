#
# Copyright (c) 2026 C++ Alliance (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for target validation in jobs.py."""

from __future__ import annotations

import pytest

from cli.jobs import _validate_targets


def test_all_target():
    assert _validate_targets(["all"]) == "all"


def test_year_targets():
    assert _validate_targets(["2026"]) == "years"
    assert _validate_targets(["2025", "2026"]) == "years"


def test_paper_targets():
    assert _validate_targets(["P3000R5"]) == "papers"
    assert _validate_targets(["P3000R5", "P3100R1"]) == "papers"
    assert _validate_targets(["p3000r5"]) == "papers"


def test_mixed_raises():
    with pytest.raises(ValueError, match="mix"):
        _validate_targets(["2026", "P3000R5"])


def test_empty_raises():
    with pytest.raises(ValueError):
        _validate_targets([])
