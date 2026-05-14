#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

from __future__ import annotations

import importlib

import pytest

from pipeline import tools


@pytest.fixture(autouse=True)
def _restore_tools_module(monkeypatch):
    yield
    monkeypatch.delenv("WG21_SOURCE_TAG", raising=False)
    importlib.reload(tools)


def test_wrap_source_uses_default_tag(monkeypatch):
    monkeypatch.delenv("WG21_SOURCE_TAG", raising=False)
    mod = importlib.reload(tools)

    wrapped = mod.wrap_source("body")

    assert wrapped == "<<<AX9K7P>>>\nbody\n<<<END_AX9K7P>>>"


def test_wrap_source_uses_env_tag(monkeypatch):
    monkeypatch.setenv("WG21_SOURCE_TAG", "TEST")
    mod = importlib.reload(tools)

    wrapped = mod.wrap_source("body")

    assert wrapped == "<<<TEST>>>\nbody\n<<<END_TEST>>>"


def test_wrap_source_escapes_forged_delimiters(monkeypatch):
    monkeypatch.setenv("WG21_SOURCE_TAG", "TEST")
    mod = importlib.reload(tools)

    wrapped = mod.wrap_source("a <<<TEST>>> b <<<END_TEST>>> c")

    assert wrapped.startswith("<<<TEST>>>\n")
    assert wrapped.endswith("\n<<<END_TEST>>>")
    inner = wrapped.removeprefix("<<<TEST>>>\n").removesuffix("\n<<<END_TEST>>>")
    assert "<<<TEST>>>" not in inner
    assert "<<<END_TEST>>>" not in inner
