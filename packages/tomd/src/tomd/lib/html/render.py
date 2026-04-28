"""DOM-to-Markdown rendering for WG21 HTML papers."""

import re
import urllib.parse

from bs4 import BeautifulSoup, Comment, Tag, NavigableString

from .. import strip_format_chars, SECTION_NUM_PREFIX_RE, ALLOWED_LINK_SCHEMES

_BOLD_WRAP_RE = re.compile(r"^\*\*(.+)\*\*$")
_COLLAPSE_WS_RE = re.compile(r"\s+")

_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_LIST_CONTAINER_TAGS = frozenset({"ul", "ol"})
_BLOCK_TAGS = frozenset({
    "p", "pre", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "ul", "ol", "blockquote", "div", "section",
    "dl", "hr", "figure",
})


_INLINE_PARENT_TAGS = frozenset({
    "p", "span", "a", "em", "i", "strong", "b",
}) | _HEADING_TAGS


def _fix_misnested_blocks(soup: BeautifulSoup) -> None:
    """Repair block elements wrongly nested inside inline parents by html.parser.

    html.parser does not auto-close inline-context tags (``<p>``, ``<h3>``,
    ``<em>``, etc.) when it encounters a block element. This pulls block
    children out to siblings, preserving surrounding inline content in
    wrapper elements of the same type.  Runs iteratively until stable.
    """
    changed = True
    while changed:
        changed = False
        for parent_tag in list(soup.find_all(_INLINE_PARENT_TAGS)):
            if not any(
                isinstance(c, Tag) and c.name in _BLOCK_TAGS
                for c in parent_tag.children
            ):
                continue
            outer = parent_tag.parent
            if outer is None:
                continue
            changed = True
            tag_name = parent_tag.name
            tag_attrs = dict(parent_tag.attrs) if parent_tag.attrs else {}
            collected_inline: list = []

            def _flush_inline():
                if not collected_inline:
                    return
                if not any(
                    (isinstance(n, Tag) and n.get_text(strip=True))
                    or (isinstance(n, NavigableString) and str(n).strip())
                    for n in collected_inline
                ):
                    collected_inline.clear()
                    return
                wrapper = soup.new_tag(tag_name, **tag_attrs)
                for node in collected_inline:
                    wrapper.append(node.extract())
                parent_tag.insert_before(wrapper)
                collected_inline.clear()

            children = list(parent_tag.children)
            for child in children:
                if isinstance(child, Tag) and child.name in _BLOCK_TAGS:
                    _flush_inline()
                    parent_tag.insert_before(child.extract())
                else:
                    collected_inline.append(child)
            _flush_inline()
            parent_tag.decompose()


def render_body(soup: BeautifulSoup, generator: str) -> str:
    """Render the HTML body to Markdown.

    Warning: this function may mutate the soup tree (extracting nested
    list elements). Do not reuse the soup object after calling this.
    """
    _fix_misnested_blocks(soup)
    body = soup.find("body") or soup
    parts: list[str] = []
    _render_children(body, parts, generator)
    return "\n\n".join(p for p in parts if p.strip())


def _render_children(element, parts: list[str], generator: str):
    """Render each child of element, appending Markdown strings to parts."""
    for child in element.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                parts.append(text)
        elif isinstance(child, Tag):
            rendered = _render_element(child, generator)
            if rendered is not None:
                parts.append(rendered)


def _render_element(el: Tag, generator: str) -> str | None:
    """Render a single HTML element to Markdown."""
    tag = el.name

    if tag in ("style", "script", "link", "meta", "head"):
        return None

    if tag in _HEADING_TAGS:
        return _render_heading(el)

    if tag == "p":
        return _render_paragraph(el)

    if tag == "pre":
        return _render_pre(el, generator)

    if tag == "code-block":
        return _render_code_block_custom(el)

    if tag == "div":
        return _render_div(el, generator)

    if tag == "ul":
        return _render_list(el, "-", generator)

    if tag == "ol":
        return _render_list(el, "1.", generator)

    if tag == "table":
        return _render_table(el)

    if tag == "blockquote":
        return _render_blockquote(el, generator)

    if tag == "dl":
        return _render_dl(el, generator)

    if tag == "hr":
        return "---"

    if tag == "section":
        parts: list[str] = []
        _render_children(el, parts, generator)
        return "\n\n".join(p for p in parts if p.strip())

    if tag in ("main", "article", "aside", "figure", "figcaption",
               "header", "footer", "nav", "details", "summary"):
        parts = []
        _render_children(el, parts, generator)
        return "\n\n".join(p for p in parts if p.strip())

    if tag in ("example-block", "note-block", "bug-block"):
        parts = []
        _render_children(el, parts, generator)
        inner = "\n\n".join(p for p in parts if p.strip())
        if inner:
            return "> " + inner.replace("\n", "\n> ")
        return None

    if tag == "tt-":
        text = el.get_text()
        return f"`{text}`" if text.strip() else None

    if tag == "code":
        code_div = el.find("div", class_="code")
        if code_div:
            text = code_div.get_text()
            text = text.strip("\n")
            return f"```cpp\n{text}\n```"

    if tag in ("span", "a", "code", "em", "strong", "b", "i", "sub", "sup",
               "ins", "del", "mark", "small", "s", "u", "abbr", "cite",
               "dfn", "var", "kbd", "samp", "time", "data", "wbr",
               "h-", "f-serif"):
        return _render_inline(el)

    parts = []
    _render_children(el, parts, generator)
    result = "\n\n".join(p for p in parts if p.strip())
    return result if result else None


_HEADING_SKIP_CLASSES = frozenset({"header-section-number", "secno", "self-link"})


def _render_heading(el: Tag) -> str | None:
    """Render a heading element to ATX Markdown."""
    level = int(el.name[1])
    text = _inline_text_excluding(el, _HEADING_SKIP_CLASSES).strip()
    if not text:
        return None
    text = text.replace("\n", " ")
    text = re.sub(r"  +", " ", text)
    text = SECTION_NUM_PREFIX_RE.sub("", text)
    text = _BOLD_WRAP_RE.sub(r"\1", text)
    return f"{'#' * level} {text}"


def _is_code_paragraph(el: Tag) -> bool:
    """True if <p> contains only <span class="code"> children.

    The dascandy/fiets generator uses this pattern for standalone code
    declarations (e.g. constructor signatures). These should be emitted
    as fenced code blocks, not flattened to prose paragraphs.

    Targets <span class="code"> specifically, NOT <code> which is inline
    formatting in Bikeshed and other generators.
    """
    has_code_span = False
    for child in el.children:
        if isinstance(child, NavigableString):
            if child.strip():
                return False
        elif child.name == "span" and "code" in (child.get("class") or []):
            has_code_span = True
        else:
            return False
    return has_code_span


def _render_paragraph(el: Tag) -> str | None:
    """Render a paragraph to a single unwrapped line."""
    if _is_code_paragraph(el):
        text = el.get_text().strip()
        return f"```cpp\n{text}\n```" if text else None
    text = _collapse_whitespace(_inline_text(el))
    return text if text else None


def _collapse_whitespace(text: str) -> str:
    """Collapse runs of whitespace to single spaces, strip format chars."""
    text = strip_format_chars(text)
    return _COLLAPSE_WS_RE.sub(" ", text).strip()


def _render_pre(el: Tag, generator: str) -> str:
    """Render a preformatted block as a fenced code block."""
    code_el = el.find("code")
    if code_el:
        lang = _detect_code_language(code_el, generator)
        text = code_el.get_text()
    else:
        lang = ""
        text = el.get_text()
    text = text.strip("\n")
    return f"```{lang}\n{text}\n```"


def _render_code_block_custom(el: Tag) -> str:
    """Render a <code-block> custom element (Jan Schultke's generator) as fenced code."""
    text = el.get_text()
    text = text.strip("\n")
    return f"```cpp\n{text}\n```"


def _detect_code_language(code_el: Tag, generator: str) -> str:
    """Detect the programming language from code element classes."""
    classes = code_el.get("class", [])
    for cls in classes:
        if cls.startswith("sourceCode"):
            lang = cls[len("sourceCode"):]
            if lang:
                return lang.lower()
        if cls.startswith("language-"):
            return cls[len("language-"):].lower()
        if cls in ("cpp", "c", "python", "javascript", "rust", "go",
                    "java", "bash", "shell", "json", "yaml", "xml"):
            return cls
    parent = code_el.parent
    if parent and parent.name == "pre":
        for cls in parent.get("class", []):
            if cls.startswith("sourceCode"):
                lang = cls[len("sourceCode"):]
                if lang:
                    return lang.lower()
    if generator == "mpark":
        return "cpp"
    return ""


def _render_div(el: Tag, generator: str) -> str | None:
    """Render a div - dispatch by class."""
    classes = el.get("class", [])

    if "sourceCode" in classes:
        pre = el.find("pre")
        if pre:
            return _render_pre(pre, generator)

    if "code" in classes:
        text = el.get_text()
        text = text.strip("\n")
        return f"```cpp\n{text}\n```"

    if any(c in classes for c in ("note", "example", "advisement")):
        parts = []
        _render_children(el, parts, generator)
        inner = "\n\n".join(p for p in parts if p.strip())
        if inner:
            return "> " + inner.replace("\n", "\n> ")

    if any(c in classes for c in ("wording", "wording-add", "wording-remove")):
        return _render_wording_div(el, generator)

    parts = []
    _render_children(el, parts, generator)
    result = "\n\n".join(p for p in parts if p.strip())
    return result if result else None


def _render_wording_div(el: Tag, generator: str) -> str:
    """Render a wording section with Pandoc fenced div markers."""
    classes = el.get("class", [])
    if "wording-add" in classes:
        fence = ":::wording-add"
    elif "wording-remove" in classes:
        fence = ":::wording-remove"
    else:
        fence = ":::wording"
    parts = []
    _render_children(el, parts, generator)
    inner = "\n\n".join(p for p in parts if p.strip())
    return f"{fence}\n\n{inner}\n\n:::"


_CODE_BLOCK_TAGS = frozenset({"pre", "code-block"})


def _render_list(el: Tag, marker: str, generator: str) -> str | None:
    """Render an ordered or unordered list."""
    items = []
    for i, li in enumerate(el.find_all("li", recursive=False)):
        prefix = f"{i + 1}." if marker == "1." else "-"
        # Detach nested sublists before capturing inline text so they are not
        # walked into by _inline_text (which would duplicate their contents).
        subs = [sub.extract()
                for sub in li.find_all(_LIST_CONTAINER_TAGS, recursive=False)]
        nested_parts = []
        for sub in subs:
            sub_rendered = _render_element(sub, generator)
            if sub_rendered:
                indented = "\n".join("  " + line for line in sub_rendered.split("\n"))
                nested_parts.append(indented)

        # Extract code blocks before inlining so they are rendered as
        # fenced blocks rather than flattened to inline text.
        code_parts = []
        for cb in li.find_all(_CODE_BLOCK_TAGS, recursive=False):
            rendered = _render_element(cb.extract(), generator)
            if rendered:
                code_parts.append(rendered)

        text = _collapse_whitespace(_inline_text(li))
        if text:
            items.append(f"{prefix} {text}")
        for cp in code_parts:
            items.append(cp)
        for np in nested_parts:
            items.append(np)
    return "\n".join(items) if items else None


def _render_table(el: Tag) -> str | None:
    """Render a table as a Markdown pipe table.

    Tables whose cells contain <pre> or <code-block> elements cannot be
    represented as pipe tables. For those, extract the code blocks as
    fenced code and skip the table structure.
    """
    if el.find(_CODE_BLOCK_TAGS):
        return _render_code_table(el)

    rows: list[list[str]] = []
    for tr in el.find_all("tr"):
        if tr.find_parent("table") != el:
            continue
        cells = []
        for td in tr.find_all(["th", "td"]):
            cells.append(_inline_text(td).strip().replace("|", "\\|"))
        if cells:
            rows.append(cells)

    if not rows:
        return None

    num_cols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < num_cols:
            r.append("")

    lines = []
    lines.append("| " + " | ".join(rows[0]) + " |")
    lines.append("| " + " | ".join(["---"] * num_cols) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _render_code_table(el: Tag) -> str | None:
    """Extract fenced code blocks from a table containing <pre> or <code-block>.

    Some generators (dascandy/fiets, Bikeshed, Schultke) wrap code inside
    table cells. Emit every non-empty block as its own fenced block so
    before/after comparisons and multi-snippet tables are preserved.
    """
    blocks: list[str] = []
    for cb in el.find_all(_CODE_BLOCK_TAGS):
        text = cb.get_text().strip()
        if text:
            blocks.append(f"```cpp\n{text}\n```")
    return "\n\n".join(blocks) if blocks else None


def _render_blockquote(el: Tag, generator: str) -> str | None:
    """Render a blockquote with > prefix."""
    parts = []
    _render_children(el, parts, generator)
    inner = "\n\n".join(p for p in parts if p.strip())
    if not inner:
        return None
    return "> " + inner.replace("\n", "\n> ")


def _render_dl(el: Tag, generator: str) -> str | None:
    """Render a definition list."""
    items = []
    for child in el.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "dt":
            text = _inline_text(child).strip()
            if text:
                items.append(f"**{text}**")
        elif child.name == "dd":
            code_parts = []
            for cb in child.find_all(_CODE_BLOCK_TAGS, recursive=False):
                rendered = _render_element(cb.extract(), generator)
                if rendered:
                    code_parts.append(rendered)
            text = _inline_text(child).strip()
            if text:
                items.append(f": {text}")
            items.extend(code_parts)
    return "\n".join(items) if items else None


def _render_inline(el: Tag) -> str:
    """Render an inline element."""
    return _inline_text(el)


def _inline_text_excluding(el: Tag, skip_classes: frozenset[str]) -> str:
    """Like _inline_text but skips child elements with any class in skip_classes."""
    parts = []
    for child in el.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            child_classes = set(child.get("class", []))
            if child_classes & skip_classes:
                continue
            parts.append(_inline_text(child))
    return "".join(parts)


def _inline_text(el: Tag) -> str:
    """Convert an element's content to inline Markdown text."""
    parts = []
    for child in el.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            tag = child.name

            if tag in ("style", "script"):
                continue

            inner = _inline_text(child)

            if tag == "code":
                stripped = inner.strip()
                if stripped:
                    parts.append(f"`{stripped}`")
                continue

            if tag in ("strong", "b"):
                stripped = inner.strip()
                if stripped:
                    parts.append(f"**{stripped}**")
                continue

            if tag in ("em", "i"):
                stripped = inner.strip()
                if stripped:
                    parts.append(f"*{stripped}*")
                continue

            if tag == "a":
                href = child.get("href", "")
                text = inner.strip()
                if href and text:
                    if href.startswith("#"):
                        parts.append(text)
                    else:
                        scheme = urllib.parse.urlparse(href).scheme.lower()
                        if scheme in ALLOWED_LINK_SCHEMES:
                            parts.append(f"[{text}]({href})")
                        else:
                            parts.append(text)
                elif text:
                    parts.append(text)
                continue

            if tag == "br":
                parts.append("\n")
                continue

            if tag == "ins":
                parts.append(f"<ins>{inner}</ins>")
                continue

            if tag == "del":
                parts.append(f"<del>{inner}</del>")
                continue

            if tag == "sub":
                parts.append(f"<sub>{inner}</sub>")
                continue

            if tag == "sup":
                parts.append(f"<sup>{inner}</sup>")
                continue

            if tag == "tt-":
                stripped = inner.strip()
                if stripped:
                    parts.append(f"`{stripped}`")
                continue

            if tag in ("span", "div", "td", "th", "li", "dt", "dd",
                       "mark", "small", "s", "u", "abbr", "cite",
                       "dfn", "var", "kbd", "samp", "time", "data",
                       "wbr", "p", "figure", "figcaption",
                       "h-", "f-serif", "c-"):
                parts.append(inner)
                continue

            if tag in _HEADING_TAGS:
                parts.append(inner)
                continue

            parts.append(inner)

    return "".join(parts)
