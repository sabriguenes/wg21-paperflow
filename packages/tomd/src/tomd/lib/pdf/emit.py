"""Markdown and companion prompts file generation."""

import logging

from .. import format_front_matter, dedup_paragraphs, strip_redundant_body_meta, strip_leading_h1
from .cleanup import normalize_whitespace
from .types import Line, Span, Section, SectionKind, BULLET_CHARS

_log = logging.getLogger(__name__)


def _render_span(span: Span, skip_bold: bool = False) -> str:
    """Render a single non-monospace span with inline Markdown formatting.

    Monospace spans are handled by _render_line_spans via group merging.
    """
    text = span.text
    if not text.strip():
        return text

    stripped = text.strip()
    leading = text[:len(text) - len(text.lstrip())]
    trailing = text[len(text.rstrip()):]

    bold = span.bold and not skip_bold

    if span.link_url:
        inner = f"[{stripped}]({span.link_url})"
        if bold and span.italic:
            inner = f"***{inner}***"
        elif bold:
            inner = f"**{inner}**"
        elif span.italic:
            inner = f"*{inner}*"
        return f"{leading}{inner}{trailing}"

    if bold and span.italic:
        return f"{leading}***{stripped}***{trailing}"
    if bold:
        return f"{leading}**{stripped}**{trailing}"
    if span.italic:
        return f"{leading}*{stripped}*{trailing}"

    return text


def _render_line_spans(line: Line, in_code_section: bool = False,
                       suppress_bold: bool = False) -> str:
    """Render all spans in a line with inline formatting applied.

    Merges consecutive monospace spans into a single backtick pair
    to avoid fragmented output like `std``::``:stop_token`.
    """
    if in_code_section:
        return "".join(s.text for s in line.spans)

    groups: list[tuple[bool, list[Span]]] = []
    for span in line.spans:
        is_mono = span.monospace and span.text.strip()
        if groups and groups[-1][0] == is_mono:
            groups[-1][1].append(span)
        else:
            groups.append((is_mono, [span]))

    parts = []
    for is_mono, spans in groups:
        if is_mono:
            merged_text = "".join(s.text for s in spans)
            stripped = merged_text.strip()
            if stripped:
                leading = merged_text[:len(merged_text) - len(merged_text.lstrip())]
                trailing = merged_text[len(merged_text.rstrip()):]
                parts.append(f"{leading}`{stripped}`{trailing}")
            else:
                parts.append(merged_text)
        else:
            for span in spans:
                if suppress_bold:
                    parts.append(_render_span(span, skip_bold=True))
                else:
                    parts.append(_render_span(span))
    return "".join(parts)


def _render_paragraph_spans(sec: Section) -> str:
    """Render a paragraph section using span-level formatting, then unwrap."""
    rendered_lines = []
    for line in sec.lines:
        rendered_lines.append(_render_line_spans(line))
    text = "\n".join(rendered_lines)
    text = normalize_whitespace(text)
    lines = text.split("\n")
    return " ".join(ln.strip() for ln in lines if ln.strip())


def _render_heading_spans(sec: Section) -> str:
    """Render a heading using span-level formatting for the first line.

    Bold is suppressed because the ATX prefix already conveys heading weight.
    """
    prefix = "#" * sec.heading_level
    if sec.lines:
        text = _render_line_spans(sec.lines[0], suppress_bold=True)
    else:
        text = sec.text.split("\n")[0]
    return f"{prefix} {text.strip()}"


def _normalize_bullet(char: str) -> str:
    """Replace Unicode bullet characters with *."""
    if char in BULLET_CHARS:
        return "*"
    return char


def _normalize_bullets(text: str) -> str:
    """Replace Unicode bullet characters with * throughout text."""
    return "".join(_normalize_bullet(ch) for ch in text)


def _render_list_spans(sec: Section) -> str:
    """Render a list section with span formatting and normalized bullets."""
    if sec.lines:
        result_lines = []
        for line in sec.lines:
            rendered = _render_line_spans(line).rstrip()
            if rendered:
                result_lines.append(_normalize_bullets(rendered))
        return "\n".join(result_lines)

    return _normalize_bullets(sec.text.rstrip())


_DEFAULT_CHAR_WIDTH = 6.0


def _estimate_char_width(sec_lines: list) -> float:
    """Estimate monospace character width from span bbox and text length."""
    for line in sec_lines:
        for span in line.spans:
            n = len(span.text.replace(" ", ""))
            if n >= 2:
                w = span.bbox[2] - span.bbox[0]
                if w > 0:
                    return w / n
    return _DEFAULT_CHAR_WIDTH


def _render_code_block(sec: Section) -> str:
    """Render a code section as a fenced code block.

    Uses glyph x-positions to calculate indentation: the offset
    of each line's first character from the block's left margin,
    divided by the monospace character width.
    """
    lang = sec.fence_lang or "cpp"
    if not sec.lines:
        return f"```{lang}\n{sec.text}\n```"

    char_w = _estimate_char_width(sec.lines)
    content_x = [
        ln.spans[0].bbox[0] for ln in sec.lines
        if ln.spans and ln.spans[0].text.strip()
    ]
    base_x = min(content_x) if content_x else 0.0

    lines = []
    for line in sec.lines:
        raw = _render_line_spans(line, in_code_section=True)
        if line.spans:
            first_text = line.spans[0].text
            text_indent = len(first_text) - len(first_text.lstrip())

            first_nonspace = line.spans[0]
            for sp in line.spans:
                if sp.text.strip():
                    first_nonspace = sp
                    break
            x0 = first_nonspace.bbox[0]
            x_indent = round((x0 - base_x) / char_w) if char_w > 0 else 0
            x_indent = max(x_indent, 0)

            if text_indent > 0 and x_indent > 0 and text_indent == x_indent:
                indent = text_indent
            else:
                indent = x_indent if x_indent > 0 else text_indent

            lines.append(" " * indent + raw.lstrip())
        else:
            lines.append(raw)
    code = "\n".join(lines)
    return f"```{lang}\n{code}\n```"


def _render_wording_line(line: Line) -> str:
    """Render a wording line, merging consecutive same-role spans.

    Whitespace-only spans between two same-role spans are absorbed into
    the group; whitespace between different roles is emitted as-is.
    Both ins and del use the role name directly as the HTML tag.
    """
    def _render_group(role: str | None, spans: list[Span]) -> str:
        text = "".join(s.text for s in spans)
        if role in ("ins", "del"):
            s = text.strip()
            lead = text[:len(text) - len(text.lstrip())]
            trail = text[len(text.rstrip()):]
            return f"{lead}<{role}>{s}</{role}>{trail}"
        return "".join(
            f"`{s.text.strip()}`" if s.monospace and s.text.strip() else s.text
            for s in spans
        )

    parts: list[str] = []
    group: list[Span] = []
    group_role: str | None = None
    ws_buf: list[Span] = []

    for span in line.spans:
        role = span.wording_role if span.text.strip() else None
        if role is None:
            ws_buf.append(span)
        elif role == group_role:
            group.extend(ws_buf)
            ws_buf.clear()
            group.append(span)
        else:
            if group:
                parts.append(_render_group(group_role, group))
            parts.extend(s.text for s in ws_buf)
            ws_buf.clear()
            group_role, group = role, [span]

    if group:
        parts.append(_render_group(group_role, group))
    parts.extend(s.text for s in ws_buf)
    return "".join(parts)


def _render_wording_section(sec: Section) -> str:
    """Render a wording section with Pandoc fenced div markers."""
    div_class = sec.kind.value
    rendered_lines = []
    for line in sec.lines:
        rendered_lines.append(_render_wording_line(line))
    text = normalize_whitespace("\n".join(rendered_lines))
    inner = " ".join(ln.strip() for ln in text.split("\n") if ln.strip())
    return f":::{div_class}\n\n{inner}\n\n:::"


def _render_cell_spans(spans: list, suppress_bold: bool = False) -> str:
    """Render a table cell's spans with inline formatting."""
    line = Line(spans=spans)
    return _render_line_spans(line, suppress_bold=suppress_bold).strip()


def _render_table(sec: Section) -> str:
    """Render a table section as a Markdown table."""
    if not sec.columns:
        return sec.text

    rows = sec.columns
    num_cols = max(len(row) for row in rows)

    header = rows[0]
    header_cells = [_render_cell_spans(cell, suppress_bold=True) for cell in header]
    while len(header_cells) < num_cols:
        header_cells.append("")

    lines = []
    lines.append("| " + " | ".join(header_cells) + " |")
    lines.append("| " + " | ".join(["---"] * num_cols) + " |")

    for row in rows[1:]:
        cells = [_render_cell_spans(cell) for cell in row]
        while len(cells) < num_cols:
            cells.append("")
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


def _render_section_md(sec: Section) -> str:
    """Render a single section to Markdown."""
    if sec.kind in (SectionKind.TITLE, SectionKind.HEADING):
        return _render_heading_spans(sec)

    if sec.kind == SectionKind.TABLE:
        return _render_table(sec)

    if sec.kind == SectionKind.CODE:
        return _render_code_block(sec)

    if sec.kind == SectionKind.LIST:
        return _render_list_spans(sec)

    if sec.kind in (SectionKind.WORDING, SectionKind.WORDING_ADD,
                    SectionKind.WORDING_REMOVE):
        return _render_wording_section(sec)

    if sec.kind == SectionKind.PARAGRAPH:
        return _render_paragraph_spans(sec)

    return sec.text


def emit_markdown(metadata: dict, sections: list[Section]) -> str:
    """Generate the output Markdown from structured sections.

    Confident sections are clean Markdown. Uncertain sections emit
    the MuPDF version marked with an HTML comment.
    """
    parts: list[str] = []

    fm = format_front_matter(metadata)
    if fm:
        parts.append(fm)

    line_num = fm.count("\n") + 3 if fm else 1

    for sec in sections:
        if sec.kind == SectionKind.UNCERTAIN:
            text = sec.text.rstrip()
            text_lines = text.count("\n") + 1
            text_start = line_num + 2
            comment = f"<!-- tomd:uncertain:L{text_start}-L{text_start + text_lines - 1} -->"
            parts.append(comment)
            parts.append(text)
            line_num += text_lines + 3
            continue

        rendered = _render_section_md(sec)
        if not rendered.strip():
            continue
        parts.append(rendered)
        line_num += rendered.count("\n") + 2

    md = "\n\n".join(parts)
    md = dedup_paragraphs(md)

    if fm:
        title = metadata.get("title", "")
        fm_end = md.find("---", 4)
        if fm_end >= 0:
            fm_end = md.find("\n", fm_end)
            if fm_end >= 0:
                body = md[fm_end + 1:]
                body = strip_leading_h1(body, title)
                md = md[:fm_end + 1] + body

    md = strip_redundant_body_meta(md)

    if fm:
        fm_end = md.find("---", 4)
        if fm_end >= 0:
            fm_end = md.find("\n", fm_end)
            if fm_end >= 0:
                body = md[fm_end + 1:]
                body = strip_leading_h1(body, title)
                md = md[:fm_end + 1] + body

    md = md.rstrip() + "\n"
    return md


def emit_prompts(sections: list[Section]) -> list[str] | None:
    """Generate self-contained LLM reconcile prompts for uncertain regions.

    Each returned element is a complete prompt the operator can paste into
    any LLM verbatim. Returns ``None`` when there are no uncertain regions.
    """
    uncertain = [(idx, s) for idx, s in enumerate(sections)
                 if s.kind == SectionKind.UNCERTAIN]
    if not uncertain:
        return None

    prompts: list[str] = []
    for idx, sec in uncertain:
        ctx_before = ""
        ctx_after = ""
        if idx > 0 and sections[idx - 1].kind != SectionKind.UNCERTAIN:
            ctx_before = sections[idx - 1].text[:200].strip()
        if idx + 1 < len(sections) and sections[idx + 1].kind != SectionKind.UNCERTAIN:
            ctx_after = sections[idx + 1].text[:200].strip()

        parts: list[str] = []
        parts.append(
            "You are reconciling text extracted from a PDF using two independent "
            "methods that produced different results. Reconcile them into clean "
            "Markdown."
        )
        parts.append("")
        parts.append(
            "CRITICAL: Keep ALL data verbatim. Do not summarize, omit, or paraphrase "
            "any text. Every word from the source must appear in your output. You are "
            "only fixing structure (paragraphs, headings, lists, formatting) - never "
            "content."
        )
        parts.append("")
        parts.append(f"This region is on page {sec.page_num}.")
        parts.append("")

        if ctx_before:
            parts.append("Context (preceding confident section):")
            parts.append(f"> {ctx_before}")
            parts.append("")

        parts.append("MuPDF extraction:")
        parts.append("```")
        parts.append(sec.mupdf_text or sec.text)
        parts.append("```")
        parts.append("")
        parts.append("Spatial extraction:")
        parts.append("```")
        parts.append(sec.spatial_text or sec.text)
        parts.append("```")

        if ctx_after:
            parts.append("")
            parts.append("Context (following confident section):")
            parts.append(f"> {ctx_after}")

        prompts.append("\n".join(parts))

    return prompts
