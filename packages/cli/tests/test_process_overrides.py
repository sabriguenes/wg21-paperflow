#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for ``_process._parse_service_overrides`` and
``_process._parse_classifier_overrides``."""

from __future__ import annotations

from cli._process import (
    _parse_classifier_overrides,
    _parse_service_overrides,
)


def test_service_overrides_none():
    assert _parse_service_overrides(None) is None
    assert _parse_service_overrides([]) is None


def test_service_overrides_bare_applies_to_all_slots():
    out = _parse_service_overrides(["b200-r1"])
    assert out == {"fast": "b200-r1", "default": "b200-r1", "tool": "b200-r1"}


def test_service_overrides_slot_equals_name():
    out = _parse_service_overrides(["fast=b200-r1", "tool=b200-llama"])
    assert out == {"fast": "b200-r1", "tool": "b200-llama"}


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
