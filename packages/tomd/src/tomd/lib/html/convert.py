"""HTML to Markdown converter for WG21 papers."""

import logging
import os
import re
from pathlib import Path

from .. import format_front_matter, dedup_paragraphs, strip_redundant_body_meta, strip_leading_h1
from . import extract as _extract
from . import render as _render

_log = logging.getLogger(__name__)

_PID_BASE_RE = re.compile(r"([DPN])(\d{3,5})(?:R(\d+))?", re.IGNORECASE)


def _override_revision_from_filename(metadata: dict, path: Path) -> None:
    """Override document revision from filename when the base paper number
    matches but revisions differ. Skip when the extracted document has a
    D-prefix (draft), since D/P mismatches are expected WG21 workflow."""
    if "document" not in metadata:
        return
    doc_m = _PID_BASE_RE.search(metadata["document"])
    stem_m = _PID_BASE_RE.search(path.stem)
    if not doc_m or not stem_m:
        return
    if doc_m.group(1).upper() == "D":
        return
    if doc_m.group(2) != stem_m.group(2):
        return
    stem_rev = stem_m.group(3)
    doc_rev = doc_m.group(3)
    if stem_rev is not None and stem_rev != doc_rev:
        prefix = stem_m.group(1).upper()
        number = stem_m.group(2)
        metadata["document"] = f"{prefix}{number}R{stem_rev}"
        _log.debug("Overrode document revision from filename: %s -> %s",
                   f"{doc_m.group(0)}", metadata["document"])


def convert_html(path: Path | os.PathLike[str]) -> tuple[str, list[str] | None]:
    """Convert an HTML file to Markdown.

    Reads the file as UTF-8 (with replacement for decode errors). Returns
    ``(markdown_text, prompts_or_none)`` where ``prompts_or_none`` is a
    list of self-contained LLM reconcile prompts (one per flagged HTML
    conversion issue) or ``None`` when conversion was fully clean.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")

    soup = _extract.parse_html(text)
    generator = _extract.detect_generator(soup)
    _log.debug("Generator: %s", generator)

    metadata = _extract.extract_metadata(soup, generator)
    if metadata and "document" not in metadata:
        from .. import DOC_NUM_RE
        stem_match = DOC_NUM_RE.search(path.stem)
        if stem_match:
            metadata["document"] = stem_match.group(1).upper()
    if metadata and "document" in metadata:
        _override_revision_from_filename(metadata, path)
    problems = _extract.strip_boilerplate(soup, generator)
    # Suppress the "unknown generator" warning when extraction produced usable
    # metadata - the generic extractor handled it well enough.
    if generator == "unknown" and metadata:
        problems = [p for p in problems if "Unrecognized" not in p]

    body_md = _render.render_body(soup, generator)

    if metadata and "title" not in metadata:
        h_match = re.search(r"^##\s+(.+)$", body_md, re.MULTILINE)
        if h_match:
            metadata["title"] = h_match.group(1).strip()

    parts = []
    if metadata:
        parts.append(format_front_matter(metadata))

    body_stripped = body_md.strip()
    while body_stripped.startswith("---") and (
        len(body_stripped) == 3 or body_stripped[3] in ("\n", "\r")
    ):
        body_stripped = body_stripped[3:].lstrip("\n\r")
    if body_stripped:
        parts.append(body_stripped)

    md = "\n\n".join(parts)
    md = dedup_paragraphs(md)

    title = metadata.get("title", "") if metadata else ""
    if metadata:
        fm_end = md.find("---", 4)
        if fm_end >= 0:
            fm_end = md.find("\n", fm_end)
            if fm_end >= 0:
                body = md[fm_end + 1:]
                body = strip_leading_h1(body, title)
                md = md[:fm_end + 1] + body

    md = strip_redundant_body_meta(md)

    if metadata:
        fm_end = md.find("---", 4)
        if fm_end >= 0:
            fm_end = md.find("\n", fm_end)
            if fm_end >= 0:
                body = md[fm_end + 1:]
                body = strip_leading_h1(body, title)
                md = md[:fm_end + 1] + body

    md = md.rstrip() + "\n"

    prompts: list[str] | None = None
    if problems:
        prompts = [
            (
                "The HTML-to-Markdown conversion encountered the following issue. "
                "Review and correct the affected region in the converted "
                "Markdown.\n\n"
                f"{problem}"
            )
            for problem in problems
        ]

    return md, prompts
