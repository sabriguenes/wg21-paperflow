#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""LaTeX parser for the C++ standard draft (cplusplus/draft).

Extracts the section hierarchy from .tex files using \\rSec commands,
expands common macros into readable text for search indexing,
preserves raw LaTeX verbatim for faithful citation, and extracts
structured metadata (cross-references, index terms, mechanisms,
grammar rules, defined terms, library declarations, paragraphs).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

RSEC_RE = re.compile(
    r"\\rSec(\d)\[([^\]]+)\]\{(.+)\}",
)

PNUM_RE = re.compile(r"\\pnum\b")

EEL_IS_BASE = "https://eel.is/c++draft"

# ---------------------------------------------------------------------------
# Extraction regexes for structured metadata
# ---------------------------------------------------------------------------

_XREF_RE = re.compile(r"\\(?:iref|ref|xref)\{([^}]*)\}")

_INDEX_EXTRACTORS: dict[str, re.Pattern[str]] = {
    "text": re.compile(r"\\indextext\{([^}]*)\}"),
    "library": re.compile(r"\\indexlibrary\{([^}]*)\}"),
    "grammar": re.compile(r"\\indexgrammar\{([^}]*)\}"),
    "defn": re.compile(r"\\indexdefn\{([^}]*)\}"),
    "impldef": re.compile(r"\\indeximpldef\{([^}]*)\}"),
    "concept": re.compile(r"\\indexconcept\{([^}]*)\}"),
}

_MECHANISM_EXTRACTORS: list[tuple[str, re.Pattern[str]]] = [
    ("keyword", re.compile(r"\\keyword\{([^}]*)\}")),
    ("library", re.compile(r"\\libglobal\{([^}]*)\}")),
    ("library", re.compile(r"\\libmember\{([^}]*)\}\{[^}]*\}")),
    ("library", re.compile(r"\\libconcept\{([^}]*)\}")),
    ("library", re.compile(r"\\deflibconcept\{([^}]*)\}")),
    ("concept", re.compile(r"\\libconcept\{([^}]*)\}")),
    ("concept", re.compile(r"\\deflibconcept\{([^}]*)\}")),
    ("concept", re.compile(r"\\exposconcept\{([^}]*)\}")),
    ("grammar", re.compile(r"\\grammarterm\{([^}]*)\}")),
    ("code", re.compile(r"\\tcode\{([^}]*)\}")),
    ("defn", re.compile(r"\\defn\{([^}]*)\}")),
    ("defn", re.compile(r"\\defnadj\{([^}]*)\}\{([^}]*)\}")),
    ("library", re.compile(r"\\indexlibraryglobal\{([^}]*)\}")),
    ("library", re.compile(r"\\indexlibrarymember\{([^}]*)\}\{[^}]*\}")),
    ("zombie", re.compile(r"\\indexlibraryzombie\{([^}]*)\}")),
]

_TCODE_SKIP_RE = re.compile(r"^[^a-zA-Z_]|^.$")

_DEFN_RE = re.compile(r"\\defn\{([^}]*)\}")
_DEFNADJ_RE = re.compile(r"\\defnadj\{([^}]*)\}\{([^}]*)\}")
_DEFNX_RE = re.compile(r"\\defnx\{([^}]*)\}\{([^}]*)\}")
_DEFINITION_RE = re.compile(r"\\definition\{([^}]*)\}\{([^}]*)\}")

_BNF_ENV_RE = re.compile(
    r"\\begin\{(?:bnf|ncbnf)\}(.*?)\\end\{(?:bnf|ncbnf)\}",
    re.DOTALL,
)
_NONTERMDEF_RE = re.compile(r"\\nontermdef\{([^}]*)\}")

_ITEMDECL_RE = re.compile(
    r"\\begin\{itemdecl\}(.*?)\\end\{itemdecl\}",
    re.DOTALL,
)
_ITEMDESCR_RE = re.compile(
    r"\\begin\{itemdescr\}(.*?)\\end\{itemdescr\}",
    re.DOTALL,
)
_FUNDESC_FIELDS: list[tuple[str, str]] = [
    ("preconditions", "expects"),
    ("effects", "effects"),
    ("postconditions", "ensures"),
    ("returns", "returns"),
    ("throws", "throws"),
    ("mandates", "mandates"),
    ("constraints", "constraints"),
    ("complexity", "complexity"),
    ("remarks", "remarks"),
]
_FUNDESC_ALL_CMDS = [cmd for _, cmd in _FUNDESC_FIELDS]


def _build_fundesc_extractors() -> dict[str, re.Pattern[str]]:
    terminators = "|".join(_FUNDESC_ALL_CMDS) + r"|result|default|pnum|end\{"
    return {
        name: re.compile(rf"\\{cmd}\s*(.*?)(?=\\(?:{terminators})|$)", re.DOTALL)
        for name, cmd in _FUNDESC_FIELDS
    }


_FUNDESC_EXTRACTORS = _build_fundesc_extractors()

_NORMATIVE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("undefined_behavior", re.compile(r"(?:the\s+behavior\s+is\s+undefined|undefined\s+behavior)", re.IGNORECASE)),
    ("ill_formed", re.compile(r"(?:the\s+program\s+is\s+ill.formed|is\s+ill.formed)", re.IGNORECASE)),
    ("ndr", re.compile(r"no\s+diagnostic\s+(?:is\s+)?required", re.IGNORECASE)),
    ("impl_defined", re.compile(r"implementation.defined", re.IGNORECASE)),
    ("requirement", re.compile(r"\bshall\b", re.IGNORECASE)),
]

# ---------------------------------------------------------------------------
# Macro expansion for cleaned_text
# ---------------------------------------------------------------------------

_SIMPLE_MACROS: dict[str, str] = {
    r"\Cpp": "C++",
    r"\CppIII": "C++03",
    r"\CppXI": "C++11",
    r"\CppXIV": "C++14",
    r"\CppXVII": "C++17",
    r"\CppXX": "C++20",
    r"\CppXXIII": "C++23",
    r"\CppXXVI": "C++26",
    r"\opt": "opt",
    r"\shl": "<<",
    r"\shr": ">>",
    r"\~": "~",
    r"\&": "&",
    r"\%": "%",
    r"\$": "$",
    r"\#": "#",
    r"\_": "_",
    r"\{": "{",
    r"\}": "}",
    r"\textbackslash": "\\",
    r"\ell": "l",
}

_BRACE_MACROS_KEEP: list[re.Pattern[str]] = [
    re.compile(r"\\tcode\{([^}]*)\}"),
    re.compile(r"\\texttt\{([^}]*)\}"),
    re.compile(r"\\noncxxtcode\{([^}]*)\}"),
    re.compile(r"\\keyword\{([^}]*)\}"),
    re.compile(r"\\grammarterm\{([^}]*)\}"),
    re.compile(r"\\term\{([^}]*)\}"),
    re.compile(r"\\defnx\{([^}]*)\}\{[^}]*\}"),
    re.compile(r"\\defn\{([^}]*)\}"),
    re.compile(r"\\placeholder\{([^}]*)\}"),
    re.compile(r"\\mathit\{([^}]*)\}"),
    re.compile(r"\\mathsf\{([^}]*)\}"),
    re.compile(r"\\textit\{([^}]*)\}"),
    re.compile(r"\\textbf\{([^}]*)\}"),
    re.compile(r"\\emph\{([^}]*)\}"),
]

_STRIP_MACROS: list[re.Pattern[str]] = [
    re.compile(r"\\indextext\{[^}]*\}"),
    re.compile(r"\\indexlibrary\{[^}]*\}"),
    re.compile(r"\\indexlibrarymember\{[^}]*\}\{[^}]*\}"),
    re.compile(r"\\indexlibraryglobal\{[^}]*\}"),
    re.compile(r"\\indexgrammar\{[^}]*\}"),
    re.compile(r"\\indeximpldef\{[^}]*\}"),
    re.compile(r"\\indexdefn\{[^}]*\}"),
    re.compile(r"\\indexhdr\{[^}]*\}"),
    re.compile(r"\\indexconcept\{[^}]*\}"),
    re.compile(r"\\indexlibraryzombie\{[^}]*\}"),
    re.compile(r"\\label\{[^}]*\}"),
    re.compile(r"\\index\[[^\]]*\]\{[^}]*\}"),
    re.compile(r"\\index\{[^}]*\}"),
    re.compile(r"\\pnum\b"),
]

_NOTE_BEGIN_RE = re.compile(r"\\begin\{note\}")
_NOTE_END_RE = re.compile(r"\\end\{note\}")
_EXAMPLE_BEGIN_RE = re.compile(r"\\begin\{example\}")
_EXAMPLE_END_RE = re.compile(r"\\end\{example\}")

_ENV_CODEBLOCK_RE = re.compile(
    r"\\begin\{codeblock\}(.*?)\\end\{codeblock\}",
    re.DOTALL,
)
_ENV_OUTPUT_RE = re.compile(
    r"\\begin\{outputblock\}(.*?)\\end\{outputblock\}",
    re.DOTALL,
)
_ENV_BNF_CLEAN_RE = re.compile(
    r"\\begin\{(?:n?cbnf|bnf)\}(.*?)\\end\{(?:n?cbnf|bnf)\}",
    re.DOTALL,
)

_REMAINING_COMMANDS_RE = re.compile(r"\\[a-zA-Z]+\{([^}]*)\}")
_LEFTOVER_BACKSLASH_RE = re.compile(r"\\[a-zA-Z]+")
_SMART_OPEN_QUOTE_RE = re.compile(r"(?<!`)``(?!`)")
_SMART_CLOSE_QUOTE_RE = re.compile(r"(?<!`)''(?!`)")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


def expand_macros(text: str) -> str:
    """Expand standard-draft LaTeX macros into readable plain text.

    Note/example environments are converted to visible markers
    ``[Note: ... --end note]`` and ``[Example: ... --end example]``
    so normative text is distinguishable from non-normative.
    """
    for macro, replacement in _SIMPLE_MACROS.items():
        text = text.replace(macro, replacement)

    text = _NOTE_BEGIN_RE.sub("[Note: ", text)
    text = _NOTE_END_RE.sub(" --end note]", text)
    text = _EXAMPLE_BEGIN_RE.sub("[Example: ", text)
    text = _EXAMPLE_END_RE.sub(" --end example]", text)

    for pattern in _STRIP_MACROS:
        text = pattern.sub("", text)

    text = _ENV_CODEBLOCK_RE.sub(lambda m: "\n```cpp\n" + m.group(1).strip() + "\n```\n", text)
    text = _ENV_OUTPUT_RE.sub(lambda m: "\n```\n" + m.group(1).strip() + "\n```\n", text)
    text = _ENV_BNF_CLEAN_RE.sub(lambda m: "\n" + m.group(1).strip() + "\n", text)

    for pattern in _BRACE_MACROS_KEEP:
        text = pattern.sub(r"\1", text)

    text = _XREF_RE.sub(r"[\1]", text)

    text = _REMAINING_COMMANDS_RE.sub(r"\1", text)
    text = _LEFTOVER_BACKSLASH_RE.sub("", text)

    text = text.replace("~", " ")

    text = _SMART_OPEN_QUOTE_RE.sub('"', text)
    text = _SMART_CLOSE_QUOTE_RE.sub('"', text)

    text = _MULTI_BLANK_RE.sub("\n\n", text)
    text = _MULTI_SPACE_RE.sub(" ", text)

    return text.strip()


def eel_is_url(stable_label: str) -> str:
    """Generate an eel.is URL for a stable label."""
    return f"{EEL_IS_BASE}/{stable_label}"


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _clean_latex_term(raw: str) -> str:
    """Strip LaTeX commands from a term, keeping only the text content."""
    cleaned = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", raw)
    cleaned = re.sub(r"[|@!].*", "", cleaned).strip()
    return re.sub(r"\\[a-zA-Z]+", "", cleaned).strip()


def extract_xrefs(raw_latex: str) -> list[str]:
    """Extract cross-reference targets from raw LaTeX.

    Handles comma-separated lists inside \\iref (e.g. \\iref{a, b}).
    """
    targets: list[str] = []
    for match in _XREF_RE.finditer(raw_latex):
        for label in match.group(1).split(","):
            label = label.strip()
            if label:
                targets.append(label)
    return sorted(set(targets))


def extract_index_terms(raw_latex: str) -> list[tuple[str, str]]:
    """Extract index terms as (category, term) pairs."""
    terms: list[tuple[str, str]] = []
    for category, pattern in _INDEX_EXTRACTORS.items():
        for match in pattern.finditer(raw_latex):
            cleaned = _clean_latex_term(match.group(1))
            if cleaned:
                terms.append((category, cleaned))
    return terms


def extract_mechanisms(raw_latex: str) -> list[tuple[str, str]]:
    """Extract mechanism names as (category, name) pairs.

    Filters out single characters, operators, and punctuation from
    \\tcode extractions.
    """
    seen: set[tuple[str, str]] = set()
    mechanisms: list[tuple[str, str]] = []
    for category, pattern in _MECHANISM_EXTRACTORS:
        for match in pattern.finditer(raw_latex):
            if category == "defn" and match.lastindex and match.lastindex >= 2:
                name = f"{match.group(1)} {match.group(2)}"
            else:
                name = match.group(1).strip()
            if not name:
                continue
            if category == "code" and _TCODE_SKIP_RE.match(name):
                continue
            cleaned = _clean_latex_term(name)
            if not cleaned:
                continue
            key = (category, cleaned)
            if key not in seen:
                seen.add(key)
                mechanisms.append(key)
    return mechanisms


def extract_defined_terms(raw_latex: str) -> list[str]:
    """Extract defined term names from \\defn, \\defnadj, \\defnx macros."""
    terms: list[str] = []
    for match in _DEFN_RE.finditer(raw_latex):
        terms.append(match.group(1).strip())
    for match in _DEFNADJ_RE.finditer(raw_latex):
        terms.append(f"{match.group(1).strip()} {match.group(2).strip()}")
    for match in _DEFNX_RE.finditer(raw_latex):
        terms.append(match.group(1).strip())
    for match in _DEFINITION_RE.finditer(raw_latex):
        terms.append(match.group(1).strip())
    return terms


def extract_grammar_rules(raw_latex: str) -> list[tuple[str, str]]:
    """Extract grammar rules as (nonterminal, raw_rule) pairs."""
    rules: list[tuple[str, str]] = []
    for env_match in _BNF_ENV_RE.finditer(raw_latex):
        body = env_match.group(1)
        nonterminals = _NONTERMDEF_RE.findall(body)
        for nt in nonterminals:
            rules.append((nt.strip(), body.strip()))
    return rules


@dataclass
class LibraryDeclaration:
    """A parsed itemdecl/itemdescr pair."""

    declaration: str
    description: str
    preconditions: str | None = None
    effects: str | None = None
    postconditions: str | None = None
    returns: str | None = None
    throws: str | None = None
    mandates: str | None = None
    constraints: str | None = None
    complexity: str | None = None
    remarks: str | None = None


def extract_library_declarations(raw_latex: str) -> list[LibraryDeclaration]:
    """Extract itemdecl/itemdescr pairs with Fundesc requirement labels."""
    decls = list(_ITEMDECL_RE.finditer(raw_latex))
    descrs = list(_ITEMDESCR_RE.finditer(raw_latex))

    results: list[LibraryDeclaration] = []
    for i, decl_match in enumerate(decls):
        declaration = decl_match.group(1).strip()
        description = ""
        fundesc: dict[str, str | None] = {}

        if i < len(descrs):
            description = descrs[i].group(1).strip()
            for fname, fpattern in _FUNDESC_EXTRACTORS.items():
                fmatch = fpattern.search(description)
                if fmatch:
                    fundesc[fname] = fmatch.group(1).strip()

        results.append(LibraryDeclaration(
            declaration=declaration,
            description=description,
            **fundesc,
        ))
    return results


@dataclass
class Paragraph:
    """A parsed paragraph within a section."""

    number: int
    raw_latex: str
    cleaned_text: str
    normative_force: str


def extract_paragraphs(raw_latex: str) -> list[Paragraph]:
    """Split a section's raw LaTeX at \\pnum boundaries into paragraphs.

    Each paragraph is tagged with its normative force.
    """
    parts = PNUM_RE.split(raw_latex)
    if not parts:
        return []

    paragraphs: list[Paragraph] = []
    for i, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue
        if i == 0 and not PNUM_RE.search(raw_latex[:20]):
            continue

        cleaned = expand_macros(part)
        force = _classify_normative_force(part)
        paragraphs.append(Paragraph(
            number=len(paragraphs) + 1,
            raw_latex=part,
            cleaned_text=cleaned,
            normative_force=force,
        ))

    return paragraphs


def _classify_normative_force(raw_latex: str) -> str:
    """Classify a paragraph's normative force from its raw LaTeX."""
    if _NOTE_BEGIN_RE.search(raw_latex):
        return "note"
    if _EXAMPLE_BEGIN_RE.search(raw_latex):
        return "example"
    for force, pattern in _NORMATIVE_PATTERNS:
        if pattern.search(raw_latex):
            return force
    return "normative"


# ---------------------------------------------------------------------------
# Section dataclass
# ---------------------------------------------------------------------------


@dataclass
class Section:
    """A parsed section of the C++ standard."""

    stable_label: str
    title: str
    depth: int
    parent_label: str | None
    chapter_file: str
    raw_latex: str
    cleaned_text: str = ""
    paragraph_count: int = 0
    section_number: str | None = None
    is_deprecated: bool = False
    is_synopsis: bool = False
    xrefs: list[str] = field(default_factory=list)
    index_terms: list[tuple[str, str]] = field(default_factory=list)
    mechanisms: list[tuple[str, str]] = field(default_factory=list)
    defined_terms: list[str] = field(default_factory=list)
    grammar_rules: list[tuple[str, str]] = field(default_factory=list)
    library_declarations: list[LibraryDeclaration] = field(default_factory=list)
    paragraphs: list[Paragraph] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.cleaned_text:
            self.cleaned_text = expand_macros(self.raw_latex)
        if self.paragraph_count == 0:
            self.paragraph_count = len(PNUM_RE.findall(self.raw_latex))
        if not self.xrefs:
            self.xrefs = extract_xrefs(self.raw_latex)
        if not self.index_terms:
            self.index_terms = extract_index_terms(self.raw_latex)
        if not self.mechanisms:
            self.mechanisms = extract_mechanisms(self.raw_latex)
        if not self.defined_terms:
            self.defined_terms = extract_defined_terms(self.raw_latex)
        if not self.grammar_rules:
            self.grammar_rules = extract_grammar_rules(self.raw_latex)
        if not self.library_declarations:
            self.library_declarations = extract_library_declarations(self.raw_latex)
        if not self.paragraphs:
            self.paragraphs = extract_paragraphs(self.raw_latex)
        if not self.is_deprecated:
            self.is_deprecated = self.stable_label.startswith("depr.")
        if not self.is_synopsis:
            self.is_synopsis = (
                self.stable_label.endswith(".syn")
                or self.stable_label.endswith(".synopsis")
                or "synopsis" in self.title.lower()
            )


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------


def _split_sections(text: str, chapter_file: str) -> list[Section]:
    """Split a .tex file into sections based on \\rSec commands."""
    matches = list(RSEC_RE.finditer(text))
    if not matches:
        return []

    parent_stack: list[str | None] = [None]
    sections: list[Section] = []

    for i, match in enumerate(matches):
        depth = int(match.group(1))
        stable_label = match.group(2)
        title = match.group(3)

        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw_latex = text[start:end].strip()

        while len(parent_stack) > depth + 1:
            parent_stack.pop()
        while len(parent_stack) < depth + 1:
            parent_stack.append(parent_stack[-1])
        parent_label = parent_stack[depth] if depth > 0 else None

        if len(parent_stack) <= depth + 1:
            parent_stack.append(stable_label)
        else:
            parent_stack[depth + 1] = stable_label

        section = Section(
            stable_label=stable_label,
            title=title,
            depth=depth,
            parent_label=parent_label,
            chapter_file=chapter_file,
            raw_latex=raw_latex,
        )
        sections.append(section)

    return sections


def _assign_section_numbers(sections: list[Section]) -> None:
    """Assign computed section numbers (e.g. '6.7.7') to sections in place.

    Walks sections in document order, maintaining a counter stack by depth.
    """
    counters: list[int] = []
    for section in sections:
        depth = section.depth
        while len(counters) <= depth:
            counters.append(0)
        counters = counters[: depth + 1]
        counters[depth] += 1
        section.section_number = ".".join(str(c) for c in counters)


def parse_file(path: Path) -> list[Section]:
    """Parse a single .tex file and return its sections."""
    text = path.read_text(encoding="utf-8")
    return _split_sections(text, path.name)


def parse_directory(source_dir: Path) -> list[Section]:
    """Parse all .tex files in a standard draft source directory.

    Reads source_dir/std.tex to determine include order, then parses
    each included file. Assigns section numbers after parsing.
    """
    std_tex = source_dir / "std.tex"
    if std_tex.exists():
        include_re = re.compile(r"\\(?:include|input)\{(\w+)\}")
        std_text = std_tex.read_text(encoding="utf-8")
        ordered_names = include_re.findall(std_text)
        ordered_files = []
        for name in ordered_names:
            candidate = source_dir / f"{name}.tex"
            if candidate.exists():
                ordered_files.append(candidate)
        seen = {f.name for f in ordered_files}
        for tex in sorted(source_dir.glob("*.tex")):
            if tex.name not in seen and tex.name != "std.tex":
                ordered_files.append(tex)
    else:
        ordered_files = sorted(source_dir.glob("*.tex"))

    all_sections: list[Section] = []
    for tex_path in ordered_files:
        if tex_path.name in ("std.tex", "macros.tex", "layout.tex", "styles.tex", "config.tex", "tables.tex"):
            continue
        all_sections.extend(parse_file(tex_path))

    _assign_section_numbers(all_sections)
    return all_sections
