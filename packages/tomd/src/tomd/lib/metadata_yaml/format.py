# Copyright 2026 The C++ Alliance, Inc.
# SPDX-License-Identifier: BSL-1.0
"""YAML front matter formatting for WG21 paper metadata.

Canonical field order and YAML serialization. Single source of truth
for the strict-order contract described in CLAUDE.md.
"""

import re

# These regexes are also defined in shared.py. Duplicated here to avoid
# circular imports (shared.py re-exports from this module).
_PID_REVISION_RE = re.compile(r"[PDpd]\d{3,5}[Rr](\d+)")

_TITLE_LABEL_RE = re.compile(
    r"(?:Paper\s*Number|Document(?:\s*Number)?|Title|Authors?|"
    r"Acknowledgements?|Reply[- ]?to|Audience|Date)\s*:",
    re.IGNORECASE,
)

_DOUBLE_ANGLE_RE = re.compile(r"<\s*<([^>]+)>")

_NON_AUTHOR_RE = re.compile(
    r"^(?:Target|Proposed|Wording|Structures?|Version|Contents?|"
    r"Read-Copy|Abstract|Introduction|Overview|Revision)\b",
    re.IGNORECASE,
)


FRONT_MATTER_ORDER = ("title", "document", "date", "intent", "audience", "reply-to")


def extract_revision(document: str) -> int | None:
    """Extract revision number from a paper ID like P2583R3 -> 3."""
    m = _PID_REVISION_RE.search(document)
    return int(m.group(1)) if m else None


def sanitize_metadata(metadata: dict) -> dict:
    """Clean up extracted metadata values before formatting.

    Fixes: embedded newlines in title, metadata labels in title text,
    double angle brackets in reply-to entries, non-author reply-to values.
    """
    md = dict(metadata)

    if "title" in md and not isinstance(md["title"], str):
        md["title"] = str(md["title"])

    if "title" in md:
        title = md["title"]
        title = title.replace("\n", " ").replace("\r", " ")
        title = re.sub(r"\s{2,}", " ", title).strip()
        title_label_m = re.search(r"\bTitle\s*:\s*", title, re.IGNORECASE)
        if title_label_m:
            after_title = title[title_label_m.end():].strip()
            next_label = _TITLE_LABEL_RE.search(after_title)
            if next_label:
                title = after_title[:next_label.start()].rstrip(" ,;")
            elif after_title:
                title = after_title
        else:
            m = _TITLE_LABEL_RE.search(title)
            if m and m.start() > 0:
                title = title[:m.start()].rstrip(" ,;")
            elif m and m.start() == 0:
                after_label = title[m.end():].strip()
                next_label = _TITLE_LABEL_RE.search(after_label)
                if next_label:
                    title = after_label[:next_label.start()].rstrip(" ,;")
                elif after_label:
                    title = after_label
        md["title"] = title.strip()

    if isinstance(md.get("reply-to"), str):
        md["reply-to"] = [md["reply-to"]]

    if "reply-to" in md and isinstance(md["reply-to"], list):
        cleaned = []
        for entry in md["reply-to"]:
            entry = _DOUBLE_ANGLE_RE.sub(r"<\1>", entry)
            if _NON_AUTHOR_RE.match(entry.strip()):
                continue
            cleaned.append(entry)
        if cleaned:
            md["reply-to"] = cleaned
        else:
            del md["reply-to"]

    return md


def _yaml_escape(s: str) -> str:
    """Escape a string for safe inclusion in double-quoted YAML."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _yaml_value(key: str, val) -> str:
    """Format a single YAML value, quoting where needed."""
    if isinstance(val, list):
        items = [f'  - "{_yaml_escape(str(v))}"' for v in val]
        return f"{key}:\n" + "\n".join(items)
    val = str(val) if not isinstance(val, str) else val
    if key == "title" or any(c in val for c in ':{}[]#&*?|>!%@`"\'\n\\'):
        return f'{key}: "{_yaml_escape(val)}"'
    return f"{key}: {val}"


def format_front_matter(metadata: dict) -> str:
    """Format metadata dict as YAML front matter in strict canonical order.

    Strict-order contract: keys are emitted exactly in the order
    ``title, document, date, intent, audience, reply-to``. Missing keys
    are skipped (no placeholders, no blank lines). Unknown keys appear
    after ``audience`` so ``reply-to`` is always last. Callers and
    downstream tools may rely on this ordering for diffs and parsing.

    Title and values containing YAML-special characters are double-quoted
    with backslash-escaping for embedded quotes, backslashes, and newlines.
    Reply-to is a YAML list of double-quoted strings. Returns the empty
    string when ``metadata`` is empty.
    """
    if not metadata:
        return ""
    metadata = dict(metadata)
    if "title" in metadata and isinstance(metadata["title"], str):
        title = metadata["title"].replace("\n", " ")
        title = re.sub(r"\s*::\s*", "::", title)
        title = re.sub(r"  +", " ", title).strip()
        metadata["title"] = title

    if "intent" not in metadata:
        title = metadata.get("title", "")
        if isinstance(title, str):
            t = title.strip()
            if t.startswith("Info:"):
                metadata["intent"] = "info"
            elif t.startswith("Ask:"):
                metadata["intent"] = "ask"

    lines = ["---"]
    pre_reply_to: list[str] = []
    reply_to_line: str | None = None
    for key in FRONT_MATTER_ORDER:
        if key not in metadata:
            continue
        rendered = _yaml_value(key, metadata[key])
        if key == "reply-to":
            reply_to_line = rendered
        else:
            pre_reply_to.append(rendered)
    lines.extend(pre_reply_to)
    for key, val in metadata.items():
        if key not in FRONT_MATTER_ORDER:
            lines.append(_yaml_value(key, val))
    if reply_to_line is not None:
        lines.append(reply_to_line)
    lines.append("---")
    return "\n".join(lines)
