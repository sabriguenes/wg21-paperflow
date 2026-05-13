#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Smoke tests for the ``paperflow advocatus`` subcommand wiring."""

from __future__ import annotations

import subprocess
import sys


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "cli", *args],
        capture_output=True,
        text=True,
    )


def test_verb_listed_in_top_level_help():
    """The verb appears in the top-level help so users can discover it."""
    result = _run("--help")
    assert result.returncode == 0
    assert "advocatus" in result.stdout


def test_advocatus_help_exits_zero():
    result = _run("advocatus", "--help")
    assert result.returncode == 0, (
        f"`paperflow advocatus --help` exited {result.returncode}\n"
        f"stderr: {result.stderr}"
    )
    # The two flags exposed for advocatus are inherited from the dissect
    # shape: --debug and --trace [N].
    assert "--debug" in result.stdout
    assert "--trace" in result.stdout


def test_verb_in_constants_module():
    """The verb is registered in the constants the parser reads."""
    from cli.__main__ import _COMMANDS, _VERB_FLAGS, _VERB_NAMES

    assert "advocatus" in _VERB_NAMES
    assert "advocatus" in _COMMANDS
    assert _VERB_FLAGS["advocatus"] == {"debug", "trace"}


def test_advocatus_rejects_year_target():
    result = _run("advocatus", "2026")
    assert result.returncode == 1
    assert "year" in result.stderr.lower() or "all" in result.stderr.lower()


def test_advocatus_rejects_all_target():
    result = _run("advocatus", "all")
    assert result.returncode == 1


def test_advocatus_rejects_multiple_targets():
    result = _run("advocatus", "P1234R0", "P5678R0")
    assert result.returncode == 1
    assert "exactly one target" in result.stderr


def test_advocatus_accepts_paper_id_target_shape():
    """A paper ID passes target validation (will then fail later because
    the workspace is empty, but validation didn't reject it)."""
    # Use --workspace-dir to point at a tmp dir so we don't touch real data.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [
                sys.executable, "-m", "cli",
                "--workspace-dir", tmp,
                "advocatus", "P9999R0",
            ],
            capture_output=True,
            text=True,
        )
    # Exit 1 because the paper isn't in the (empty) store; but the message
    # should be the AdvocatusError-style "Advocatus failed", not a target
    # validation rejection.
    assert result.returncode == 1
    assert "advocatus failed" in result.stderr.lower()
