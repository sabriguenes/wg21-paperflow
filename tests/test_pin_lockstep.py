#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Assert pymupdf is pinned identically in tomd and dissect.

Both packages import ``fitz``; mismatched pins in editable installs in
the same venv let ``uv`` resolve one version while partial rebuilds
drift. See ``packages/tomd/src/tomd/CLAUDE.md`` and
``packages/dissect/src/dissect/CLAUDE.md`` for the rule.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


_PIN_RE = re.compile(r"^pymupdf([~=<>!].*)$")


def _read_pymupdf_pin(pyproject: Path) -> str:
    deps = tomllib.loads(pyproject.read_text())["project"]["dependencies"]
    for dep in deps:
        m = _PIN_RE.match(dep)
        if m:
            return m.group(1)
    raise AssertionError(f"pymupdf not declared in {pyproject}")


def test_pymupdf_pin_lockstep():
    root = Path(__file__).resolve().parent.parent
    tomd_pin = _read_pymupdf_pin(root / "packages/tomd/pyproject.toml")
    dissect_pin = _read_pymupdf_pin(
        root / "packages/dissect/pyproject.toml"
    )
    assert tomd_pin == dissect_pin, (
        f"pymupdf pins drifted: tomd={tomd_pin!r}, "
        f"dissect={dissect_pin!r}. Bump both in the same PR."
    )
