"""Quality assurance metrics and scoring for Markdown conversion output.

Parses the Markdown output with mistune to validate structure from
the reader's perspective, not from pipeline internals.

DESIGN CONSTRAINT: compute_metrics() takes only a Markdown string.
No page count, no file format, no pipeline internals. Every signal
must be derivable from the Markdown text alone. This keeps scoring
format-agnostic (works on PDF output, HTML output, or any Markdown)
and prevents coupling between the scorer and the converter.
"""

import json
import logging
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ftfy.badness import badness as _ftfy_badness
import mistune

__all__ = ["QAMetrics", "compute_metrics", "run_qa_report"]

_log = logging.getLogger(__name__)

_FRONT_MATTER_RE = re.compile(r"^---\n(.+?\n)---", re.DOTALL)
_UNCERTAIN_MARKER = "tomd:uncertain"
_LOSSY_TABLE_MARKER = "tomd:lossy-table"

_FRONT_MATTER_FIELDS = frozenset({"title", "document", "date", "reply-to", "audience"})
_WG21_DOC_NUM_RE = re.compile(r"[DPN]\d{3,5}R?\d*", re.IGNORECASE)

_WORDING_DIV_RE = re.compile(r"^:::wording", re.MULTILINE)

# Intentionally broader than structure.py's _STRUCTURAL_CODE_RE.
# qa.py uses it for *detection* (scoring), so false positives just
# inflate a metric. structure.py uses it for *rescue* (promoting
# paragraphs to code blocks), where false positives corrupt output.
_STRUCTURAL_CODE_RE = re.compile(
    r"^\s*[{}]|"               # standalone brace lines
    r";\s*$|"                  # trailing semicolons (code statements)
    r"#include\s*<|"           # preprocessor includes
    r"\w+\s*\([^)]*\)\s*\{|"  # function_name(...) {
    r"\w+\s*\([^)]*\)\s*;|"   # declaration: name(...);
    r"^\s*template\s*<|"       # template declarations
    r"^\s*(?:namespace|class|struct|enum)\s+\w+\s*[:{]",  # type decl with brace or colon
    re.MULTILINE,
)

# ISO C++ normative specification-element labels from [structure.specifications],
# [requirements] section, and historical labels (C++17 "Requires").
# Source: https://eel.is/c++draft/structure#specifications
# Paragraphs starting with these labels are standard wording, not unfenced code,
# even when they end with semicolons (e.g. "Returns: substr(0).compare(str);").
_STANDARDESE_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"Effects|Returns|Equivalent to|Preconditions|Postconditions|"
    r"Constraints|Mandates|Complexity|Throws|Remarks|"
    r"Default|Expects|Result|Ensures|Let|"
    r"Constant When|Hardened preconditions|Synchronization|Error conditions|"
    r"Required behavior|Default behavior|Recommended practice|"
    r"Requires"
    r")\s*:",
    re.IGNORECASE,
)


@dataclass
class QAMetrics:
    """Per-document quality metrics computed purely from Markdown text."""
    file: str = ""
    total_chars: int = 0
    heading_count: int = 0
    max_heading_level: int = 0
    code_block_count: int = 0
    list_count: int = 0
    table_count: int = 0
    front_matter_count: int = 0
    has_doc_number: bool = False
    uncertain_count: int = 0
    unfenced_code_lines: int = 0
    paragraph_count: int = 0
    mojibake_count: int = 0
    heading_level_skips: int = 0
    wording_section_count: int = 0
    table_parse_errors: int = 0
    lossy_table_count: int = 0
    empty_output: bool = False
    score: int = 100
    issues: list[str] = field(default_factory=list)


def _parse_front_matter(md_text: str) -> dict[str, str]:
    """Extract YAML front matter fields and values from Markdown text."""
    m = _FRONT_MATTER_RE.match(md_text)
    if not m:
        return {}
    fields: dict[str, str] = {}
    for line in m.group(1).split("\n"):
        if ":" in line:
            key = line.split(":")[0].strip().lower()
            val = line.split(":", 1)[1].strip() if ":" in line else ""
            if key:
                fields[key] = val
    return fields


def _paragraph_plain_text(node: dict) -> str:
    """Get plain text from a paragraph, excluding inline code and HTML."""
    parts = []
    for child in node.get("children", []):
        if child["type"] == "text":
            parts.append(child.get("raw", ""))
    return " ".join(parts)


def _has_wording_markup(node: dict) -> bool:
    """True if a paragraph contains <ins> or <del> wording tags."""
    for child in node.get("children", []):
        if child["type"] == "inline_html":
            raw = child.get("raw", "")
            if raw.startswith(("<ins", "<del", "</ins", "</del")):
                return True
    return False


def _looks_like_code(node: dict) -> bool:
    """True if a paragraph looks like an unfenced code block.

    Checks only the plain-text children (not codespan or inline_html).
    Looks for structural code patterns — braces, semicolons, function
    declarations — not single keywords that appear naturally in prose.
    Wording sections (<ins>/<del> markup) and standardese specification
    labels (Effects:, Returns:, etc.) are excluded since trailing
    semicolons in normative prose are expected, not missed code.
    """
    children = node.get("children", [])
    if not children:
        return False
    if _has_wording_markup(node):
        return False
    text_children = [c for c in children if c["type"] == "text"]
    code_children = [c for c in children if c["type"] == "codespan"]
    if code_children and not text_children:
        return False
    text = _paragraph_plain_text(node)
    if text.strip().startswith(":::"):
        return False
    if _STANDARDESE_PREFIX_RE.match(text.strip()):
        return False
    return bool(_STRUCTURAL_CODE_RE.search(text))


def _count_unfenced_code(paragraphs: list[dict]) -> int:
    """Count paragraphs that look like unfenced code blocks."""
    return sum(1 for p in paragraphs if _looks_like_code(p))


_MOJIBAKE_BADNESS_THRESHOLD = 3


_CODE_FENCE_RE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)

_INLINE_CODE_RE = re.compile(r"``[^`]+``|`[^`]+`")


_UNICODE_TOPIC_RE = re.compile(
    r"utf|unicode|transcod|encoding|charconv|replacement.character",
    re.IGNORECASE,
)



def _count_mojibake(md_text: str) -> int:
    """Count encoding corruption signals in the markdown text.

    Two-layer detection:
    1. U+FFFD (replacement character): always means bytes were lost
       during decoding. Zero false positives. Source:
       https://bytetunnels.com/posts/some-characters-could-not-be-decoded-fixing-replacement-character-errors/
    2. ftfy.badness(): scores unlikely Unicode sequences that indicate
       UTF-8 decoded as Latin-1/CP-1252. Uses ~400 character classes
       tuned over years with ~1 false positive per 6M texts.
       We use the integer score, not the boolean is_bad(), because
       is_bad() has length-dependent false-positive rate on long
       technical documents.

    We require badness >= 3 to flag, because a score of 1-2 on a
    large document with math symbols or diacritics can be noise.

    U+FFFD inside fenced code blocks and inline code spans is
    suppressed: papers about encoding (e.g. P3904R1, P2728R11)
    intentionally demonstrate replacement characters in code.

    For papers whose title indicates they discuss Unicode encoding,
    U+FFFD counting is suppressed entirely since the replacement
    character is the paper's subject matter. ftfy.badness() still
    provides an independent safety net for real encoding corruption.

    Decision: ftfy over custom regex. See plans/QA-001-extend-qa-scoring.md,
    Research Finding #1. Custom byte-pattern regex (e.g. [\\xc0-\\xdf][\\x80-\\xbf])
    false-positives on valid multi-byte Unicode in author names, math
    symbols, and C++ template syntax.

    Known limitations:
    - ftfy is NOT Markdown-aware (Research Finding #5). Math symbols
      in the 'numeric' category could interact with mojibake patterns,
      but the threshold of >= 3 mitigates this.
    - Does not detect encoding issues inside images or binary blobs.
    """
    prose = _CODE_FENCE_RE.sub("", md_text)
    prose = _INLINE_CODE_RE.sub("", prose)
    front = _parse_front_matter(md_text)
    title = front.get("title", "")
    if _UNICODE_TOPIC_RE.search(title):
        count = 0
    else:
        count = prose.count("\ufffd")
    badness = _ftfy_badness(md_text)
    if badness >= _MOJIBAKE_BADNESS_THRESHOLD:
        count += 1
    return count


def _heading_level_skips(tokens: list[dict]) -> int:
    """Count heading level skips (ascending only).

    Matches markdownlint MD001 (heading-increment) semantics:
    only flags when heading level increases by more than 1.
    Decreasing levels (closing a subsection) are always allowed.
    Source: https://github.com/DavidAnson/markdownlint/blob/main/doc/md001.md
    W3C WAI: https://www.w3.org/WAI/tutorials/page-structure/headings/

    Limitation: only scans top-level tokens. Headings nested inside
    blockquotes or list items (in 'children' arrays) are not checked.
    For WG21 papers this is acceptable because headings never appear
    inside blockquotes. See plans/QA-001-extend-qa-scoring.md,
    Research Finding #4 (Mistune AST completeness).
    """
    headings = [t for t in tokens if t["type"] == "heading"]
    if len(headings) < 2:
        return 0
    skips = 0
    for i in range(1, len(headings)):
        prev_level = headings[i - 1]["attrs"]["level"]
        curr_level = headings[i]["attrs"]["level"]
        if curr_level > prev_level and curr_level - prev_level > 1:
            skips += 1
    return skips


def _count_table_parse_errors(tokens: list[dict]) -> int:
    """Count tables with inconsistent column counts across rows."""
    errors = 0
    for t in tokens:
        if t["type"] != "table":
            continue
        children = t.get("children", [])
        col_counts: set[int] = set()
        for child in children:
            if child["type"] in ("table_head", "table_body"):
                for row in child.get("children", []):
                    if row["type"] == "table_row":
                        col_counts.add(len(row.get("children", [])))
        if len(col_counts) > 1:
            errors += 1
    return errors



def compute_metrics(md_text: str, file: str = "") -> QAMetrics:
    """Compute QA metrics by parsing the Markdown output with mistune.

    Takes only the Markdown text. No page count, no format hints.
    Everything is derived from the text itself.
    """
    m = QAMetrics(file=file)
    m.total_chars = len(md_text)
    m.empty_output = m.total_chars == 0 or not md_text.strip()

    if m.empty_output:
        m.score, m.issues = 0, ["empty output"]
        return m

    ast_renderer = mistune.create_markdown(renderer="ast", plugins=["table"])
    tokens = ast_renderer(md_text)

    headings = [t for t in tokens if t["type"] == "heading"]
    m.heading_count = len(headings)
    if headings:
        m.max_heading_level = max(t["attrs"]["level"] for t in headings)

    m.code_block_count = sum(1 for t in tokens if t["type"] == "block_code")

    m.list_count = sum(1 for t in tokens if t["type"] == "list")

    m.table_count = sum(1 for t in tokens if t["type"] == "table")

    # Front matter: mistune doesn't parse YAML, so we check raw text.
    # The AST's leading thematic_break confirms the --- opener.
    fm_fields = _parse_front_matter(md_text)
    m.front_matter_count = sum(1 for f in _FRONT_MATTER_FIELDS if f in fm_fields)
    doc_val = fm_fields.get("document", "")
    m.has_doc_number = bool(_WG21_DOC_NUM_RE.search(doc_val))

    m.uncertain_count = sum(
        1 for t in tokens
        if t["type"] == "block_html" and _UNCERTAIN_MARKER in t.get("raw", "")
    )

    m.lossy_table_count = sum(
        1 for t in tokens
        if t["type"] == "block_html" and _LOSSY_TABLE_MARKER in t.get("raw", "")
    )

    paragraphs = [t for t in tokens if t["type"] == "paragraph"]
    m.paragraph_count = len(paragraphs)
    m.unfenced_code_lines = _count_unfenced_code(paragraphs)

    m.mojibake_count = _count_mojibake(md_text)
    m.heading_level_skips = _heading_level_skips(tokens)

    m.wording_section_count = len(_WORDING_DIV_RE.findall(md_text))
    m.table_parse_errors = _count_table_parse_errors(tokens)

    m.score, m.issues = _score(m)
    return m


_LONG_DOC_PARAGRAPHS = 10


def _score(m: QAMetrics) -> tuple[int, list[str]]:
    """Compute 0-100 quality score purely from Markdown structure."""
    score = 100
    issues: list[str] = []

    if m.empty_output:
        return 0, ["empty output"]

    is_long = m.paragraph_count >= _LONG_DOC_PARAGRAPHS

    if m.uncertain_count > 0:
        penalty = min(20, 5 * m.uncertain_count)
        score -= penalty
        issues.append(f"{m.uncertain_count} uncertain regions")

    if m.lossy_table_count > 0:
        issues.append(f"{m.lossy_table_count} lossy tables")

    if m.heading_count == 0 and is_long:
        score -= 25
        issues.append("no headings")

    if m.front_matter_count == 0 and is_long:
        score -= 10
        issues.append("no front matter")

    if m.unfenced_code_lines > 5:
        penalty = min(15, m.unfenced_code_lines)
        if m.code_block_count == 0 and m.unfenced_code_lines > 20:
            penalty = min(30, penalty + 15)
        score -= penalty
        issues.append(f"{m.unfenced_code_lines} unfenced code lines")

    has_structure = (m.heading_count > 0) + (m.code_block_count > 0) + \
                    (m.list_count > 0) + (m.table_count > 0)
    if is_long and has_structure <= 1:
        score -= 10
        issues.append(f"low variety ({has_structure} structural types)")

    # Mojibake: encoding corruption is always a conversion bug.
    # Capped at 20 to avoid dominating the score on documents
    # with a single corrupted paragraph.
    # Decision: plans/QA-001-extend-qa-scoring.md, Phase 2.
    if m.mojibake_count > 0:
        penalty = min(20, 5 * m.mojibake_count)
        score -= penalty
        issues.append(f"{m.mojibake_count} mojibake sequences")

    # Heading level skips: matches markdownlint MD001.
    # A skip usually means the converter mis-detected heading depth.
    # Capped at 15 because heading structure is important but not
    # as severe as encoding corruption (mojibake).
    # Decision: plans/QA-001-extend-qa-scoring.md, Phase 3.
    if m.heading_level_skips > 0:
        penalty = min(15, 5 * m.heading_level_skips)
        score -= penalty
        issues.append(f"{m.heading_level_skips} heading level skips")

    return max(0, score), issues


def _qa_one(item: tuple[str, str]) -> dict:
    """Score the Markdown for a single ``(paper_id, markdown_text)`` pair."""
    paper_id, md_text = item
    try:
        m = compute_metrics(md_text, file=paper_id)
        return asdict(m)
    except Exception as exc:
        _log.error("QA failed for %s: %s", paper_id, exc)
        m = QAMetrics(file=paper_id, score=0,
                      issues=[f"qa error: {exc}"])
        return asdict(m)


def _qa_metrics_from_dict(d: dict) -> QAMetrics:
    return QAMetrics(**d)


def run_qa_report(
    items: list[tuple[str, str]],
    json_path: Path | None = None,
    workers: int = 1,
    timeout: int = 120,
) -> None:
    """Score a batch of converted papers and print a ranked report.

    *items* is a list of ``(paper_id, markdown_text)`` pairs. Each markdown
    string is scored independently via :func:`compute_metrics`. Uses
    *workers* parallel processes (default 1 = sequential); *timeout* is
    seconds of no progress before aborting remaining items.
    """
    total = len(items)
    results: list[QAMetrics] = []
    t0 = time.monotonic()

    if workers > 1:
        done_count = 0
        with ProcessPoolExecutor(max_workers=workers) as pool:
            future_to_id = {pool.submit(_qa_one, it): it[0] for it in items}
            pending = set(future_to_id.keys())
            last_completion = time.monotonic()

            while pending:
                newly_done = {f for f in pending if f.done()}

                if newly_done:
                    last_completion = time.monotonic()
                    for f in newly_done:
                        pending.discard(f)
                        done_count += 1
                        pid = future_to_id[f]
                        elapsed = time.monotonic() - t0
                        rate = done_count / elapsed if elapsed > 0 else 0
                        eta = (total - done_count) / rate if rate > 0 else 0
                        print(f"\r  [{done_count}/{total}] {pid:<40} "
                              f"{rate:.1f} files/s  ETA {eta/60:.0f}m",
                              end="", file=sys.stderr)
                        sys.stderr.flush()
                        try:
                            d = f.result()
                            results.append(_qa_metrics_from_dict(d))
                        except Exception as exc:
                            results.append(QAMetrics(
                                file=pid, score=0,
                                issues=[f"error: {exc}"]))

                elif time.monotonic() - last_completion > timeout:
                    timed_out: list[str] = []
                    for f in pending:
                        pid = future_to_id[f]
                        timed_out.append(pid)
                        f.cancel()
                        results.append(QAMetrics(
                            file=pid, score=0,
                            issues=[f"timeout (no progress for {timeout}s)"]))
                    done_count += len(pending)
                    print(f"\n  TIMEOUT: {len(timed_out)} files aborted: "
                          f"{', '.join(timed_out)}", file=sys.stderr)
                    break

                else:
                    time.sleep(0.5)

            pool.shutdown(wait=False, cancel_futures=True)
    else:
        for i, item in enumerate(items, 1):
            pid = item[0]
            elapsed = time.monotonic() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / rate if rate > 0 else 0
            print(f"\r  [{i}/{total}] {pid:<40} "
                  f"{rate:.1f} files/s  ETA {eta/60:.0f}m",
                  end="", file=sys.stderr)
            sys.stderr.flush()
            d = _qa_one(item)
            results.append(_qa_metrics_from_dict(d))

    wall = time.monotonic() - t0
    avg = wall / total if total else 0.0
    print(f"\n  Finished in {wall/60:.1f} minutes "
          f"({avg:.1f}s/file avg)\n", file=sys.stderr)

    results.sort(key=lambda r: r.score)

    buckets = {"90-100": 0, "70-89": 0, "50-69": 0, "0-49": 0}
    for r in results:
        if r.score >= 90:
            buckets["90-100"] += 1
        elif r.score >= 70:
            buckets["70-89"] += 1
        elif r.score >= 50:
            buckets["50-69"] += 1
        else:
            buckets["0-49"] += 1

    print(f"\ntomd QA Report: {total} files")
    print("=" * 40)
    print("\nScore Distribution:")
    for label, count in buckets.items():
        pct = 100 * count / total if total else 0
        print(f"  {label}: {count:>6}  ({pct:.1f}%)")

    needs_review = sum(1 for r in results if r.score < 70)
    print(f"\nFiles needing review (score < 70): {needs_review}")
    print(f"Files probably OK (score >= 70):   {total - needs_review}")

    worst = [r for r in results if r.score < 100][:30]
    if worst:
        print(f"\nWorst {len(worst)} files:")
        print(f"  {'Score':>5}  {'File':<40}  Issues")
        print(f"  {'-----':>5}  {'-' * 40}  {'-' * 40}")
        for r in worst:
            name = r.file
            if len(name) > 40:
                name = name[:37] + "..."
            issue_str = ", ".join(r.issues) if r.issues else "ok"
            print(f"  {r.score:>5}  {name:<40}  {issue_str}")

    if json_path is not None:
        rows = [asdict(r) for r in results]
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nDetailed metrics written to {json_path}")
