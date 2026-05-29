#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Standard version metadata for C++ draft tags.

Maps draft document numbers to C++ standard versions. The mapping is
stable -- the committee does not renumber retroactively. Tags above
the highest known C++26 tag are assumed to be C++29 working drafts
until the next standard is finalised.
"""

from __future__ import annotations

_TAG_TO_VERSION: dict[str, tuple[str, str]] = {
    "main": ("C++29", "bleeding edge trunk"),
    "n5046": ("C++26", "final working draft"),
    "n5032": ("C++26", "working draft"),
    "n5014": ("C++26", "working draft"),
    "n5008": ("C++26", "working draft"),
    "n5001": ("C++26", "working draft"),
    "n4993": ("C++26", "working draft"),
    "n4988": ("C++26", "working draft"),
    "n4986": ("C++26", "working draft"),
    "n4981": ("C++26", "working draft"),
    "n4971": ("C++26", "working draft"),
    "n4950": ("C++23", "final working draft"),
    "n4861": ("C++20", "final working draft"),
    "n4659": ("C++17", "final working draft"),
    "n4140": ("C++14", "final working draft"),
    "n3337": ("C++11", "post-publication draft"),
}

_C26_MAX_TAG = 5046


def resolve_version(tag: str) -> tuple[str, str]:
    """Return (standard_version, version_note) for a draft tag.

    Known tags use the hardcoded mapping. Unknown numeric tags above
    n5046 are assumed C++29 working drafts.
    """
    if tag in _TAG_TO_VERSION:
        return _TAG_TO_VERSION[tag]

    if tag.startswith("n") and tag[1:].isdigit():
        num = int(tag[1:])
        if num > _C26_MAX_TAG:
            return ("C++29", "working draft")
        if num > 4950:
            return ("C++26", "working draft")
        if num > 4861:
            return ("C++23", "working draft")
        if num > 4659:
            return ("C++20", "working draft")
        if num > 4140:
            return ("C++17", "working draft")
        if num > 3337:
            return ("C++14", "working draft")
        return ("C++11", "working draft")

    return ("unknown", "unknown tag")


def resolve_draft_for_version(
    version: str, available_tags: list[str]
) -> str | None:
    """Find the highest-numbered tag matching a C++ version shorthand.

    Accepts inputs like ``"C++23"``, ``"c++26"``, ``"C++20"``.
    Returns ``None`` if no matching tag is found.
    """
    version_upper = version.upper().replace(" ", "")
    matching = [
        tag for tag in available_tags
        if resolve_version(tag)[0].upper().replace(" ", "") == version_upper
    ]
    if not matching:
        return None
    return sorted(matching)[-1]
