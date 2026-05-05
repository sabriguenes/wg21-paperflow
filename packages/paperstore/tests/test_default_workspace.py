#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Tests for ``paperstore.default_workspace_dir``."""

from __future__ import annotations

from pathlib import Path

import pytest

from paperstore import WORKSPACE_ENV_VAR, SqliteBackend, default_workspace_dir


def test_default_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WORKSPACE_ENV_VAR, raising=False)
    with pytest.raises(EnvironmentError, match="WG21_DATA_DIR is not set"):
        default_workspace_dir()


def test_env_var_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(WORKSPACE_ENV_VAR, str(tmp_path))
    assert default_workspace_dir() == tmp_path


def test_empty_env_var_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WORKSPACE_ENV_VAR, "   ")
    with pytest.raises(EnvironmentError, match="WG21_DATA_DIR is not set"):
        default_workspace_dir()


def test_from_env_reads_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WG21_DATA_DIR", str(tmp_path))
    backend = SqliteBackend.from_env()
    assert backend.workspace_dir == tmp_path


def test_from_env_raises_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WG21_DATA_DIR", raising=False)
    with pytest.raises(EnvironmentError, match="WG21_DATA_DIR"):
        SqliteBackend.from_env()
