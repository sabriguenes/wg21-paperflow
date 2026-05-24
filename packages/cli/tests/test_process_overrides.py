#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for ``_process._parse_classifier_overrides`` and the now-removed
``--service`` CLI flag.

Model selection now lives in each pipeline's markdown file under
``## Services``; the ``--service`` flag is gone. These tests pin that
removal so a future revert can't sneak it back in without failing CI.
"""

from __future__ import annotations

import subprocess
import sys

from cli._process import _parse_classifier_overrides


def test_service_overrides_function_removed():
    """The CLI no longer parses ``--service``; the helper is gone."""
    import cli._process as proc

    assert not hasattr(proc, "_parse_service_overrides")


def test_service_flag_rejected_by_cli():
    """``paperflow assay --service ...`` is no longer a recognized flag."""
    result = subprocess.run(
        [sys.executable, "-m", "cli", "assay", "P0000R0", "--service", "x"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    combined = (result.stdout + result.stderr).lower()
    assert "--service" in combined or "unrecognized" in combined


def test_classifier_overrides_none():
    assert _parse_classifier_overrides(None) is None
    assert _parse_classifier_overrides([]) is None


def test_classifier_overrides_bare_applies_to_selector():
    """A bare classifier name applies to the default 'selector' slot."""
    out = _parse_classifier_overrides(["zeroshot-base"])
    assert out == {"selector": "zeroshot-base"}


def test_classifier_overrides_slot_equals_name():
    out = _parse_classifier_overrides(["selector=zeroshot-large"])
    assert out == {"selector": "zeroshot-large"}


def test_classifier_overrides_multiple_slots():
    out = _parse_classifier_overrides([
        "selector=zeroshot-large",
        "evidence_selector=nli-small",
    ])
    assert out == {
        "selector": "zeroshot-large",
        "evidence_selector": "nli-small",
    }


def test_classifier_overrides_whitespace_stripped():
    out = _parse_classifier_overrides(["  selector  =  zeroshot-base  "])
    assert out == {"selector": "zeroshot-base"}
