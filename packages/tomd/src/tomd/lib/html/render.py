"""DOM-to-Markdown rendering for WG21 HTML papers."""

import html as _html
import re
import urllib.parse
from collections import deque

from bs4 import BeautifulSoup, Comment, Tag, NavigableString

from .. import strip_format_chars, ALLOWED_LINK_SCHEMES

_BOLD_WRAP_RE = re.compile(r"^\*\*(.+)\*\*$")
_LOSSY_TABLE_MARKER = "<!-- tomd:lossy-table -->"
_MIXED_TABLE_MARKER = "<!-- tomd:mixed-table -->"
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
    wrapper elements of the same type.

    Uses a worklist to avoid rescanning the entire DOM on each fix.
    """

    def _has_block_child(tag: Tag) -> bool:
        return any(isinstance(c, Tag) and c.name in _BLOCK_TAGS for c in tag.children)

    worklist: deque[Tag] = deque(
        tag for tag in soup.find_all(_INLINE_PARENT_TAGS)
        if _has_block_child(tag)
    )

    while worklist:
        parent_tag = worklist.popleft()
        # A container can be queued twice (two children promoting blocks
        # into it); the second pop sees it already decomposed.
        if parent_tag.decomposed or parent_tag.parent is None:
            continue
        if not _has_block_child(parent_tag):
            continue

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
        container = parent_tag.parent
        parent_tag.decompose()
        # The promoted blocks now live in the enclosing element. If that
        # is itself an inline parent (block nested two or more inline
        # levels deep), re-queue it so the repair cascades upward.
        if (
            isinstance(container, Tag)
            and container.name in _INLINE_PARENT_TAGS
            and _has_block_child(container)
        ):
            worklist.append(container)



def _fix_misnested_list_items(soup: BeautifulSoup) -> None:
    """Promote <li> elements directly nested inside other <li> to siblings.

    html.parser does not auto-close <li> when it encounters a new <li>,
    causing successive list items to nest inside the first one. This walks
    all <li> tags and moves any direct child <li> to the correct sibling
    position. Properly wrapped sublists (<li><ul><li>...</li></ul></li>)
    are left alone so the renderer can indent them.

    Uses a worklist to avoid rescanning the entire DOM on each fix.
    """

    def _direct_li_children(li: Tag) -> list[Tag]:
        return li.find_all("li", recursive=False)

    worklist: deque[Tag] = deque(
        li for li in soup.find_all("li")
        if _direct_li_children(li)
    )

    while worklist:
        li = worklist.popleft()
        if li.parent is None:
            continue
        nested = _direct_li_children(li)
        if not nested:
            continue
        parent = li.parent
        for nested_li in nested:
            parent.append(nested_li.extract())
            if _direct_li_children(nested_li):
                worklist.append(nested_li)


_TABLE_CELL_TAGS = frozenset({"td", "th"})


def _fix_misnested_table_cells(soup: BeautifulSoup) -> None:
    """Repair table rows and cells wrongly nested by html.parser.

    html.parser does not auto-close ``<td>``, ``<th>``, or ``<tr>`` when
    it encounters a new opening tag of the same type. This causes two
    kinds of mangling:

    1. ``<tr>`` nested inside ``<td>``/``<th>`` instead of being a sibling
       row. We promote these to direct children of the table container
       (``<tbody>``, ``<thead>``, ``<tfoot>``, or ``<table>``).

    2. ``<td>``/``<th>`` nested inside another ``<td>``/``<th>`` instead
       of being siblings within the same ``<tr>``. We flatten these by
       repeatedly extracting nested cells.
    """
    for table in soup.find_all("table"):
        container = (
            table.find(["tbody", "thead", "tfoot"], recursive=False)
            or table
        )
        # Phase 1: promote <tr> elements trapped inside cells to the
        # table container level.
        for cell in table.find_all(_TABLE_CELL_TAGS):
            nested_trs = cell.find_all("tr", recursive=False)
            for nested_tr in nested_trs:
                container.append(nested_tr.extract())

        # Phase 2: flatten <td>/<th> nested inside other <td>/<th>
        # within each <tr>.
        for tr in table.find_all("tr"):
            changed = True
            while changed:
                changed = False
                for cell in tr.find_all(_TABLE_CELL_TAGS, recursive=False):
                    nested = cell.find_all(
                        _TABLE_CELL_TAGS, recursive=False,
                    )
                    if nested:
                        for nested_cell in reversed(nested):
                            cell.insert_after(nested_cell.extract())
                        changed = True


_EELIS_MARGIN_CLASS = "marginalizedparent"
_EELIS_CHROME_DIV_CLASSES = (_EELIS_MARGIN_CLASS, "sourceLinkParent")


_EELIS_WORDING_DIV_CLASSES = ["wording", "hana_wording"]
_EELIS_HEADING_DEMOTION = 2


def _remove_class_token(tag: Tag, token: str) -> None:
    """Drop one class token, keeping co-occurring classes intact."""
    classes = tag.get("class", [])
    if token in classes:
        classes.remove(token)
    if not classes and "class" in tag.attrs:
        del tag["class"]


def _texpara_number_index(target: Tag) -> int:
    """Index in ``target.contents`` where a folded paragraph number lands.

    A texpara can start with block children (a table, a nested sub-para);
    prepending the number there would strand it in its own wrapper when
    _fix_misnested_blocks later promotes the block out. Anchor the number
    before the first prose child instead: a non-empty text node, an inline
    tag, or a ``div.sentence`` (still a div at fold time, renamed to span
    later in this pass).
    """
    for i, child in enumerate(target.contents):
        if isinstance(child, NavigableString):
            if str(child).strip():
                return i
        elif isinstance(child, Tag):
            if child.name not in _BLOCK_TAGS:
                return i
            if "sentence" in (child.get("class") or []):
                return i
    return 0


def _normalize_eelis_wording(soup: BeautifulSoup) -> None:
    """Normalize eel.is-style standardese markup into renderable structure.

    Papers that paste wording from eel.is (or hana_wording derivatives) carry
    a div-per-sentence structure with navigation chrome that the generic div
    walk shreds into one paragraph per inline node. This pass rewrites that
    markup in place:

    - Headings inside ``div.wording``/``div.hana_wording`` demote by two
      levels (h1 -> h3, capped at h6) so clause titles nest under the
      paper's own section heading instead of colliding with the H1 title.
    - ``span.texttt`` (TeX monospace, e.g. header names like ``<memory>``)
      becomes ``<code>`` so the angle brackets survive as Markdown code
      spans instead of leaking as raw HTML tags.
    - ``span.codeblock`` (multi-line synopses) becomes ``<pre>`` with a
      ``<code class="cpp">`` child so the code renders as a highlighted
      fenced block instead of collapsing into prose.
    - Paragraph numbers from ``a.marginalized`` fold inline as a text prefix
      of their paragraph.
    - Navigation chrome (``div.marginalizedparent`` paragraph-number/link
      columns, ``div.sourceLinkParent`` GitHub ``#`` anchors) is dropped.
      This also removes the ``a.itemDeclLink`` link glyphs inside table cells.
    - ``div.sentence`` becomes ``<span>`` and ``div.texpara`` becomes ``<p>``
      so each prose paragraph renders as one coherent line.
    - The caption nodes of ``div.numberedTable`` ("Table 47 -- ...") wrap in
      one ``<p>`` instead of fragmenting per inline node.

    Gated on the presence of ``div.marginalizedparent``, which only this
    markup family produces. Documents without it are left untouched. The
    gate is document-wide on purpose: the renamed class names do not occur
    outside this family in the corpus, and region-scoping would miss
    wording fragments pasted outside a wording container.
    """
    margins = soup.find_all("div", class_=_EELIS_MARGIN_CLASS)
    if not margins:
        return

    # Demote wording-internal headings below the paper's own section
    # structure. The id-set guards against double demotion when
    # div.hana_wording nests inside div.wording.
    demoted: set[int] = set()
    for wording in soup.find_all("div", class_=_EELIS_WORDING_DIV_CLASSES):
        for heading in wording.find_all(_HEADING_TAGS):
            if id(heading) in demoted:
                continue
            demoted.add(id(heading))
            level = min(int(heading.name[1]) + _EELIS_HEADING_DEMOTION, 6)
            heading.name = f"h{level}"

    # Multi-line code synopses live in <span class='codeblock'> under
    # div.texpara, sometimes inside anonymous <span> wrappers. Rename to
    # <pre> with a code.cpp child so they render as highlighted fenced
    # blocks; _fix_misnested_blocks later promotes them out of the
    # paragraph that the texpara rename below creates.
    for span in soup.find_all("span", class_="codeblock"):
        if not span.get_text(strip=True) and span.find(True) is None:
            continue
        span.name = "pre"
        _remove_class_token(span, "codeblock")
        # _render_pre reads the code child via get_text(), which drops
        # <br> elements; materialize them as newlines first.
        for br in span.find_all("br"):
            br.replace_with("\n")
        code = soup.new_tag("code")
        code["class"] = ["cpp"]
        for child in list(span.children):
            code.append(child.extract())
        span.append(code)

    # Skip texttt runs inside the renamed <pre> blocks (their text is
    # already covered by the pre's code child) and texttt runs wrapping
    # a <pre> (a <code> ancestor would backtick-flatten the fence; the
    # span stays inline and the misnested-blocks repair promotes the
    # pre out of it).
    for span in soup.find_all("span", class_="texttt"):
        if span.find_parent("pre") is not None or span.find("pre") is not None:
            continue
        span.name = "code"
        _remove_class_token(span, "texttt")

    # Fidelity guard, before the number fold: content trapped inside a
    # chrome div (stray text, a misnested texpara) is rescued to siblings
    # after the div, where the fold below can still find and number it.
    for cls in _EELIS_CHROME_DIV_CLASSES:
        for div in soup.find_all("div", class_=cls):
            rescued = [
                child for child in list(div.contents)
                if (isinstance(child, Tag) and child.name != "a")
                or (isinstance(child, NavigableString) and str(child).strip())
            ]
            for child in reversed(rescued):
                div.insert_after(child.extract())

    # Fold paragraph and list-item numbers ("1", "(1.1)") into the content
    # they belong to. Prefer the sibling texpara (sibling-scoped on purpose:
    # a margin must not number a texpara nested in a following table or
    # sub-para, and a texpara takes at most one number). List items carry
    # their text as bare siblings of the margin, so margins inside a
    # content container (li, div.para) drop the number inline instead.
    # Margins with neither target lose their number along with the chrome.
    numbered: set[int] = set()
    for margin in margins:
        num_anchor = margin.find("a", class_="marginalized")
        number = num_anchor.get_text(strip=True) if num_anchor else ""
        if not number:
            continue
        parent = margin.parent
        in_content_container = isinstance(parent, Tag) and (
            parent.name == "li"
            or "para" in (parent.get("class") or [])
        )
        target = margin.find_next_sibling("div", class_="texpara")
        if target is not None and id(target) not in numbered:
            numbered.add(id(target))
            target.insert(_texpara_number_index(target), f"{number} ")
        elif in_content_container:
            # Padded on both sides: the margin can follow its content
            # (number as trailing marker), and whitespace collapse swallows
            # the extra space in the leading case.
            margin.insert_after(f" {number} ")

    for cls in _EELIS_CHROME_DIV_CLASSES:
        for div in soup.find_all("div", class_=cls):
            div.decompose()

    for div in soup.find_all("div", class_="sentence"):
        div.name = "span"
    for div in soup.find_all("div", class_="texpara"):
        div.name = "p"

    for div in soup.find_all("div", class_="numberedTable"):
        caption = soup.new_tag("p")
        for node in list(div.children):
            if isinstance(node, Tag) and node.name == "table":
                break
            caption.append(node.extract())
        if caption.get_text(strip=True) or caption.find(True) is not None:
            div.insert(0, caption)


def render_body(soup: BeautifulSoup, generator: str) -> str:
    """Render the HTML body to Markdown.

    Warning: this function may mutate the soup tree (extracting nested
    list elements). Do not reuse the soup object after calling this.
    """
    # Must run before _fix_misnested_blocks: the rename of div.texpara to
    # <p> can leave block children (tables, nested divs) inside the new
    # paragraph, which _fix_misnested_blocks then promotes to siblings.
    _normalize_eelis_wording(soup)
    _fix_misnested_blocks(soup)
    _fix_misnested_list_items(soup)
    _fix_misnested_table_cells(soup)
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

    if tag == "img":
        return _render_img(el)

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

    if tag == "abstract-block":
        parts = []
        _render_children(el, parts, generator)
        inner = "\n\n".join(p for p in parts if p.strip())
        if inner:
            return f"## Abstract\n\n{inner}"
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


_ALT_TEXT_ESCAPE_RE = re.compile(r"([\[\]\\])")


def _render_img(el: Tag) -> str | None:
    """Render ``<img>`` as ``![alt](src)``. Skips when ``src`` is absent."""
    src = (el.get("src") or "").strip()
    if not src:
        return None
    alt = (el.get("alt") or "").strip()
    alt = _ALT_TEXT_ESCAPE_RE.sub(r"\\\1", alt)
    return f"![{alt}]({src})"


def rewrite_imgs_via_manifest(
    soup: BeautifulSoup,
    src_to_entry: dict,
) -> None:
    """Mutate each ``<img>`` so its ``src`` and ``alt`` reflect the manifest."""
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        entry = src_to_entry.get(src)
        if entry is None:
            if "src" in img.attrs:
                del img.attrs["src"]
            continue
        img["src"] = entry.stored_filename
        alt = entry.caption_text or entry.alt_attr or ""
        img["alt"] = alt


_HEADING_SKIP_CLASSES = frozenset({"header-section-number", "secno", "self-link"})


def _render_heading(el: Tag) -> str | None:
    """Render a heading element to ATX Markdown."""
    if len(el.name) < 2 or not el.name[1].isdigit():
        return ""
    level = int(el.name[1])
    text = _inline_text(el, _HEADING_SKIP_CLASSES).strip()
    if not text:
        return None
    text = text.replace("\n", " ")
    text = re.sub(r"  +", " ", text)
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


def _has_spans(el: Tag) -> bool:
    """Return True if any cell uses colspan or rowspan."""
    for cell in el.find_all(["th", "td"]):
        if cell.get("colspan") or cell.get("rowspan"):
            return True
    return False



def _needs_flat_reconstruction(el: Tag) -> bool:
    """Return True for tables that need the descendant-walking flat path.

    This covers: nested <table> elements, parser-mangled nested cells,
    and block-level content inside cells.
    """
    if el.find("table"):
        return True
    for cell in el.find_all(["th", "td"]):
        if cell.find(["th", "td"]):
            return True
        if cell.find(["ol", "ul", "blockquote"]):
            return True
        if cell.find("p") and len(cell.find_all("p")) > 1:
            return True
    return False




def _cell_own_text(cell: Tag) -> str:
    """Get text directly owned by a cell, excluding nested cells/rows."""
    parts = []
    for child in cell.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag) and child.name not in ("td", "th", "tr",
                                                            "thead", "tbody",
                                                            "tfoot", "table"):
            parts.append(child.get_text(" ", strip=True))
    return " ".join(parts).strip()


def _denormalize_table(el: Tag) -> list[list[str]]:
    """Expand rowspan/colspan into a flat rectangular grid of cell texts.

    Two-pass algorithm: builds a None-initialized 2D matrix, then fills it
    by walking <tr> elements and tracking pending rowspans per column.
    """
    trs = []
    containers = el.find_all(["thead", "tbody", "tfoot"], recursive=False)
    if containers:
        for c in containers:
            trs.extend(c.find_all("tr", recursive=False))
    else:
        trs = el.find_all("tr", recursive=False)

    if not trs:
        return []

    # First pass: determine grid dimensions
    max_cols = 0
    for tr in trs:
        col_count = 0
        for cell in tr.find_all(["th", "td"], recursive=False):
            try:
                col_count += int(cell.get("colspan", 1))
            except (ValueError, TypeError):
                col_count += 1
        if col_count > max_cols:
            max_cols = col_count
    num_rows = len(trs)

    if max_cols == 0:
        return []

    grid: list[list[str | None]] = [[None] * max_cols for _ in range(num_rows)]

    # Second pass: fill the grid
    for row_idx, tr in enumerate(trs):
        col_idx = 0
        for cell in tr.find_all(["th", "td"], recursive=False):
            # Skip columns already filled by previous rowspans
            while col_idx < max_cols and grid[row_idx][col_idx] is not None:
                col_idx += 1
            if col_idx >= max_cols:
                break

            text = _inline_text(cell).strip().replace("|", "\\|")
            text = _COLLAPSE_WS_RE.sub(" ", text)
            try:
                rs = int(cell.get("rowspan", 1))
            except (ValueError, TypeError):
                rs = 1
            try:
                cs = int(cell.get("colspan", 1))
            except (ValueError, TypeError):
                cs = 1

            for dr in range(rs):
                for dc in range(cs):
                    r, c = row_idx + dr, col_idx + dc
                    if r < num_rows and c < max_cols:
                        grid[r][c] = text

            col_idx += cs

    # Replace any remaining None with empty string
    return [[cell if cell is not None else "" for cell in row] for row in grid]


def _render_denormalized_table(el: Tag) -> str | None:
    """Render a table with rowspan/colspan as a flat denormalized pipe table."""
    rows = _denormalize_table(el)
    if not rows:
        return None

    num_cols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < num_cols:
            r.append("")

    headers = [_BOLD_WRAP_RE.sub(r"\1", cell) for cell in rows[0]]

    lines = [_LOSSY_TABLE_MARKER]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * num_cols) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _render_table_flat(el: Tag) -> str:
    """Render a table as a pipe table, handling parser-mangled DOM.

    When html.parser has mangled the tree (nested cells due to missing
    closing tags), we collect ALL <td>/<th> descendants in document order,
    extract their direct text via _cell_own_text, and use <tr> boundaries
    to reconstruct rows. Output is a standard Markdown pipe table.
    """
    all_cells = el.find_all(["td", "th"])

    if not all_cells:
        return el.get_text(" ", strip=True)

    rows: list[list[str]] = []
    current_row: list[str] = []

    seen: set[int] = set()
    for node in el.descendants:
        if not isinstance(node, Tag):
            continue
        if node.name == "tr" and current_row:
            rows.append(current_row)
            current_row = []
        elif node.name in ("td", "th"):
            nid = id(node)
            if nid in seen:
                continue
            seen.add(nid)
            text = _cell_own_text(node)
            text = _COLLAPSE_WS_RE.sub(" ", text).strip().replace("|", "\\|")
            current_row.append(text)
    if current_row:
        rows.append(current_row)

    if not rows:
        return el.get_text(" ", strip=True)

    num_cols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < num_cols:
            r.append("")

    headers = [_BOLD_WRAP_RE.sub(r"\1", cell) for cell in rows[0]]

    lines = [_LOSSY_TABLE_MARKER]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * num_cols) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _is_pure_code_table(el: Tag) -> bool:
    """Return True when the table is a headerless pure code dump.

    A table qualifies as pure code only when ALL of:
    1. It has no ``<th>`` header cells (no column labels to preserve).
    2. Every ``<td>`` data cell contains ``<pre>`` or ``<code-block>``.

    Tables with headers always go to the mixed renderer so that
    Before/After, Current/Proposed, and other comparison labels
    are preserved in the output.
    """
    if el.find("th"):
        return False
    td_cells = el.find_all("td")
    if not td_cells:
        return True
    return all(td.find(list(_CODE_BLOCK_TAGS)) for td in td_cells)


_ALLOWED_CELL_TAGS = frozenset({
    "a", "ins", "del", "em", "strong", "b", "i", "code",
    "sub", "sup", "br", "span", "mark", "s", "u",
})


def _cell_inner_html(cell: Tag) -> str:
    """Return sanitized inner HTML for a non-code table cell.

    Keeps safe inline tags (links, ins/del, emphasis) as HTML so they
    render correctly inside an HTML table. Strips all other tags but
    keeps their text content. Text nodes are HTML-escaped.
    """
    parts: list[str] = []
    for child in cell.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            parts.append(_html.escape(str(child)))
        elif isinstance(child, Tag):
            if child.name in _ALLOWED_CELL_TAGS:
                parts.append(str(child))
            else:
                parts.append(_html.escape(child.get_text()))
    return _COLLAPSE_WS_RE.sub(" ", "".join(parts)).strip()


def _render_mixed_code_table(el: Tag) -> str | None:
    """Render a table with mixed code and text cells as an HTML table.

    Preserves tabular structure with ``<pre><code>`` for code cells and
    plain escaped text for non-code cells. Keeps headers, row associations,
    and non-code content (checkmarks, status, URLs) that ``_render_code_table``
    would discard.
    """
    trs = []
    containers = el.find_all(["thead", "tbody", "tfoot"], recursive=False)
    if containers:
        for container in containers:
            trs.extend(container.find_all("tr", recursive=False))
    else:
        trs = el.find_all("tr", recursive=False)

    if not trs:
        return None

    num_cols = max(
        len(tr.find_all(["td", "th"], recursive=False)) for tr in trs
    )
    if num_cols == 0:
        return None

    col_w = f"{100 // num_cols}%"
    _S = (f"border: 1px solid #999; padding: 6px 10px; "
          f"vertical-align: top; width: {col_w};")
    parts: list[str] = [
        _MIXED_TABLE_MARKER,
        '<table border="1" rules="all" cellpadding="6" cellspacing="0"'
        ' style="border-collapse: collapse; width: 100%;">',
    ]

    for tr in trs:
        parts.append("<tr>")
        cells = tr.find_all(["td", "th"], recursive=False)
        for cell in cells:
            tag = cell.name
            code_el = cell.find(list(_CODE_BLOCK_TAGS))
            if code_el:
                code_text = code_el.get_text().strip()
                escaped = _html.escape(code_text)
                parts.append(
                    f'<{tag} style="{_S}">'
                    f'<pre style="margin: 0;"><code>{escaped}</code></pre>'
                    f'</{tag}>')
            else:
                inner = _cell_inner_html(cell)
                parts.append(f'<{tag} style="{_S}">{inner}</{tag}>')
        parts.append("</tr>")

    parts.append("</table>")
    return "\n".join(parts)


def _render_table(el: Tag) -> str | None:
    """Render a table as a Markdown pipe table.

    Tables whose cells contain <pre> or <code-block> elements are routed
    based on code-cell ratio: pure code tables (>=80% code data cells) go
    to ``_render_code_table``; mixed-content tables go to
    ``_render_mixed_code_table`` which preserves tabular structure.

    Tables with rowspan/colspan are denormalized into flat pipe tables.
    Tables with parser-mangled DOM (nested cells from unclosed tags) are
    reconstructed via descendant walking. Only tables with nested <table>
    elements or block-level cell content (pre, lists) fall back to the
    flat reconstruction path.
    """
    if el.find(_CODE_BLOCK_TAGS):
        if _is_pure_code_table(el):
            return _render_code_table(el)
        return _render_mixed_code_table(el)

    if _needs_flat_reconstruction(el):
        return _render_table_flat(el)

    if _has_spans(el):
        return _render_denormalized_table(el)

    rows: list[list[str]] = []
    containers = el.find_all(["thead", "tbody", "tfoot"], recursive=False)
    if containers:
        tr_sources = containers
    else:
        tr_sources = [el]
    for src in tr_sources:
        for tr in src.find_all("tr", recursive=False):
            cells = []
            for td in tr.find_all(["th", "td"], recursive=False):
                cell_text = _inline_text(td).strip()
                cell_text = _COLLAPSE_WS_RE.sub(" ", cell_text)
                cells.append(cell_text.replace("|", "\\|"))
            if cells:
                rows.append(cells)

    if not rows:
        return None

    num_cols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < num_cols:
            r.append("")

    headers = [_BOLD_WRAP_RE.sub(r"\1", cell) for cell in rows[0]]

    lines = []
    lines.append("| " + " | ".join(headers) + " |")
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
    if not blocks:
        return None
    return _LOSSY_TABLE_MARKER + "\n\n" + "\n\n".join(blocks)


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


def _inline_text(el: Tag, skip_classes: frozenset[str] = frozenset()) -> str:
    """Convert an element's content to inline Markdown text.

    `skip_classes` drops direct children carrying any of those classes (used
    by headings to strip section-number and self-link spans) while preserving
    inline formatting such as <code> on the remaining children.
    """
    parts = []
    for child in el.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            tag = child.name

            if skip_classes and skip_classes.intersection(child.get("class") or []):
                continue

            if tag in ("style", "script"):
                continue

            if tag in ("table", "thead", "tbody", "tfoot", "tr"):
                parts.append(child.get_text(" ", strip=True))
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

            if tag == "img":
                # Inline-context <img> (typical: inside <p>). Routed
                # through the same _render_img + manifest rewrite the
                # block-level path uses, so an <img> nested in a
                # paragraph still becomes ![alt](filename) and inherits
                # the cap + truncation behaviour.
                rendered = _render_img(child)
                if rendered:
                    parts.append(rendered)
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
