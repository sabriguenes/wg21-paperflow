#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Regenerate the vendored ``Emoji_Presentation`` range table.

Parses the Unicode ``emoji-data.txt`` (UTS #51) and emits the compact
``(start, end)`` ranges of codepoints carrying the ``Emoji_Presentation``
property into
``packages/tomd/src/tomd/lib/pdf/data/emoji_presentation.py``.

``Emoji_Presentation=Yes`` means the codepoint defaults to emoji (colour
glyph) rendering, which is the right set for the glyph-placeholder
coincidence filter: a text-layer codepoint at the same on-page position as
a raster glyph means the glyph is redundant decoration.

Usage::

    # Download the canonical source (the generator is study-side; the
    # converter never fetches at runtime):
    curl -o emoji-data.txt \\
        https://www.unicode.org/Public/UCD/latest/ucd/emoji/emoji-data.txt
    python generate_emoji_presentation.py emoji-data.txt

Re-run on Unicode version bumps. The vendored module records the source
version so drift is visible.

Note: the output module lives directly under ``lib/pdf/`` (not a
``data/`` subdirectory) because the repo's ``.gitignore`` ignores
``data/``; a runtime table must be a normally-tracked source file.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_LINE_RE = re.compile(
    r"^([0-9A-Fa-f]+)(?:\.\.([0-9A-Fa-f]+))?\s*;\s*Emoji_Presentation\b"
)
_VERSION_RE = re.compile(r"^#\s*Version:\s*(.+)$")

# Where the vendored module lands, relative to the repo root (this script
# lives at study/emoji-glyphs/). Directly under lib/pdf/ - NOT a data/
# subdir, which .gitignore would exclude from commits.
_OUT_PATH = (
    Path(__file__).resolve().parents[2]
    / "packages/tomd/src/tomd/lib/pdf/emoji_data.py"
)


def _parse(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Return (unicode_version, sorted-merged ranges) from emoji-data text."""
    version = "unknown"
    points: list[tuple[int, int]] = []
    for line in text.splitlines():
        vm = _VERSION_RE.match(line)
        if vm:
            version = vm.group(1).strip()
        m = _LINE_RE.match(line)
        if not m:
            continue
        start = int(m.group(1), 16)
        end = int(m.group(2), 16) if m.group(2) else start
        points.append((start, end))

    points.sort()
    merged: list[tuple[int, int]] = []
    for start, end in points:
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return version, merged


def _render(version: str, ranges: list[tuple[int, int]]) -> str:
    body = "\n".join(f"    (0x{s:04X}, 0x{e:04X})," for s, e in ranges)
    return f'''#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Vendored Unicode ``Emoji_Presentation`` ranges (GENERATED - do not edit).

Regenerate with ``study/emoji-glyphs/generate_emoji_presentation.py``.
Source: Unicode ``emoji-data.txt`` (UTS #51), Version: {version}.

A codepoint with ``Emoji_Presentation=Yes`` defaults to emoji (colour
glyph) rendering. ``tomd.lib.pdf.glyphs`` uses these ranges to detect
text-layer emoji for the placeholder coincidence filter.
"""

# (start, end) inclusive codepoint ranges, sorted and merged.
EMOJI_PRESENTATION_RANGES: tuple[tuple[int, int], ...] = (
{body}
)

UNICODE_EMOJI_VERSION = "{version}"
'''


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    src = Path(argv[1]).read_text(encoding="utf-8")
    version, ranges = _parse(src)
    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(_render(version, ranges), encoding="utf-8")
    total = sum(e - s + 1 for s, e in ranges)
    print(f"wrote {_OUT_PATH} ({len(ranges)} ranges, {total} codepoints, "
          f"Unicode {version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
