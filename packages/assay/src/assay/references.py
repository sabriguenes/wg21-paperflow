#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Mechanical reference extraction and verification.

Line-by-line scan for WG21 paper numbers (D/P/N) and URLs.
No LLM, no HTTP. Paperstore lookups for cross-check and stale detection.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

_PAPER_NUMBER_RE = re.compile(r"\b([DPN]\d{4,5}(?:R\d{1,2})?)\b", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+")
_LINK_URL_RE = re.compile(r"\]\([^)]*\)")


@dataclass
class RefEntry:
    """A mechanically extracted reference."""

    paper_id: str
    urls: list[str] = field(default_factory=list)
    lines: list[int] = field(default_factory=list)
    count: int = 0
    in_paperstore: bool = False
    stale: bool = False
    self_cite: bool = False


def extract_references(source: str) -> list[RefEntry]:
    """Extract all D/P/N paper numbers and URLs from paper source.

    Returns one RefEntry per unique paper number, sorted by mention
    count descending.
    """
    stripped = _LINK_URL_RE.sub("]", source)
    lines = source.splitlines()

    paper_lines: dict[str, list[int]] = {}
    paper_urls: dict[str, list[str]] = {}
    counts: Counter[str] = Counter()

    for i, line in enumerate(lines):
        line_num = i + 1
        for m in _PAPER_NUMBER_RE.finditer(line):
            pid = m.group(1).upper()
            counts[pid] += 1
            paper_lines.setdefault(pid, []).append(line_num)

        for m in _URL_RE.finditer(line):
            url = m.group(0).rstrip(".,;:)>\"'")
            for pm in _PAPER_NUMBER_RE.finditer(line):
                pid = pm.group(1).upper()
                if pid not in paper_urls:
                    paper_urls[pid] = []
                if url not in paper_urls[pid]:
                    paper_urls[pid].append(url)

    refs = []
    for pid in sorted(counts, key=lambda p: -counts[p]):
        refs.append(RefEntry(
            paper_id=pid,
            urls=paper_urls.get(pid, []),
            lines=sorted(set(paper_lines.get(pid, []))),
            count=counts[pid],
        ))

    return refs


def verify_references(
    refs: list[RefEntry],
    backend,
    authors: list[str],
) -> list[RefEntry]:
    """Enrich RefEntry list with paperstore cross-check, stale, self-cite.

    Resolves bare paper numbers (no R-suffix) to their latest revision.
    Merges duplicates created by resolution (counts are summed).
    """
    author_lower = {a.lower() for a in authors}

    for ref in refs:
        result = backend.resolve_year_for_paper(ref.paper_id)
        if result is not None:
            ref.in_paperstore = True
            _, paper = result
            if paper.previous_version:
                prev_upper = paper.previous_version.upper()
                if ref.paper_id == prev_upper:
                    ref.stale = True
            paper_authors = {a.lower() for a in (paper.authors or [])}
            if author_lower & paper_authors:
                ref.self_cite = True
        else:
            base = re.sub(r"R\d+$", "", ref.paper_id, flags=re.IGNORECASE)
            if base == ref.paper_id:
                latest = backend.find_latest_revision(ref.paper_id)
                if latest:
                    ref.paper_id = latest.upper()
                    ref.in_paperstore = True
                    result2 = backend.resolve_year_for_paper(latest)
                    if result2:
                        _, paper = result2
                        paper_authors = {a.lower() for a in (paper.authors or [])}
                        if author_lower & paper_authors:
                            ref.self_cite = True
            else:
                result2 = backend.resolve_year_for_paper(base)
                if result2 is not None:
                    ref.in_paperstore = True

    seen: dict[str, RefEntry] = {}
    merged: list[RefEntry] = []
    for ref in refs:
        pid = ref.paper_id.upper()
        if pid in seen:
            existing = seen[pid]
            existing.count += ref.count
            existing.lines = sorted(set(existing.lines + ref.lines))
            existing.urls = list(dict.fromkeys(existing.urls + ref.urls))
        else:
            seen[pid] = ref
            merged.append(ref)

    return merged
