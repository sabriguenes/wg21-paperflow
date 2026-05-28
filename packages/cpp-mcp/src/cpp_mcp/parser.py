#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""LaTeX parser for the C++ standard draft (cplusplus/draft).

Extracts the section hierarchy from .tex files using \\rSec commands,
expands common macros into readable text for search indexing, and
preserves raw LaTeX verbatim for faithful citation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

RSEC_RE = re.compile(
    r"\\rSec(\d)\[([^\]]+)\]\{(.+)\}",
)

PNUM_RE = re.compile(r"\\pnum\b")

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

_XREF_RE = re.compile(r"\\(?:iref|ref|xref)\{([^}]*)\}")

_STRIP_MACROS: list[re.Pattern[str]] = [
    re.compile(r"\\indextext\{[^}]*\}"),
    re.compile(r"\\indexlibrary\{[^}]*\}"),
    re.compile(r"\\indexlibrarymember\{[^}]*\}\{[^}]*\}"),
    re.compile(r"\\indexlibraryglobal\{[^}]*\}"),
    re.compile(r"\\indexgrammar\{[^}]*\}"),
    re.compile(r"\\indeximpldef\{[^}]*\}"),
    re.compile(r"\\indexdefn\{[^}]*\}"),
    re.compile(r"\\indexhdr\{[^}]*\}"),
    re.compile(r"\\label\{[^}]*\}"),
    re.compile(r"\\index\{[^}]*\}"),
    re.compile(r"\\enterexample"),
    re.compile(r"\\exitexample"),
    re.compile(r"\\enternote"),
    re.compile(r"\\exitnote"),
    re.compile(r"\\pnum\b"),
]

_ENV_CODEBLOCK_RE = re.compile(
    r"\\begin\{codeblock\}(.*?)\\end\{codeblock\}",
    re.DOTALL,
)
_ENV_OUTPUT_RE = re.compile(
    r"\\begin\{outputblock\}(.*?)\\end\{outputblock\}",
    re.DOTALL,
)
_ENV_BNF_RE = re.compile(
    r"\\begin\{(?:n?cbnf|bnf)\}(.*?)\\end\{(?:n?cbnf|bnf)\}",
    re.DOTALL,
)

_REMAINING_COMMANDS_RE = re.compile(r"\\[a-zA-Z]+\{([^}]*)\}")
_LEFTOVER_BACKSLASH_RE = re.compile(r"\\[a-zA-Z]+")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


def expand_macros(text: str) -> str:
    """Expand standard-draft LaTeX macros into readable plain text."""
    for macro, replacement in _SIMPLE_MACROS.items():
        text = text.replace(macro, replacement)

    for pattern in _STRIP_MACROS:
        text = pattern.sub("", text)

    text = _ENV_CODEBLOCK_RE.sub(lambda m: "\n```cpp\n" + m.group(1).strip() + "\n```\n", text)
    text = _ENV_OUTPUT_RE.sub(lambda m: "\n```\n" + m.group(1).strip() + "\n```\n", text)
    text = _ENV_BNF_RE.sub(lambda m: "\n" + m.group(1).strip() + "\n", text)

    for pattern in _BRACE_MACROS_KEEP:
        text = pattern.sub(r"\1", text)

    text = _XREF_RE.sub(r"[\1]", text)

    text = _REMAINING_COMMANDS_RE.sub(r"\1", text)
    text = _LEFTOVER_BACKSLASH_RE.sub("", text)

    text = text.replace("~", " ")

    # LaTeX smart quotes -> ASCII. Must run after code block expansion
    # to avoid mangling triple backticks.
    text = re.sub(r"(?<!`)``(?!`)", '"', text)
    text = re.sub(r"(?<!`)''(?!`)", '"', text)

    text = _MULTI_BLANK_RE.sub("\n\n", text)
    text = _MULTI_SPACE_RE.sub(" ", text)

    return text.strip()


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

    def __post_init__(self) -> None:
        if not self.cleaned_text:
            self.cleaned_text = expand_macros(self.raw_latex)
        if self.paragraph_count == 0:
            self.paragraph_count = len(PNUM_RE.findall(self.raw_latex))


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------

_ITEMDECL_RE = re.compile(
    r"\\begin\{itemdecl\}.*?\\end\{itemdescr\}",
    re.DOTALL,
)


def _split_sections(text: str, chapter_file: str) -> list[Section]:
    """Split a .tex file into sections based on \\rSec commands.

    Returns sections in document order. Each section's raw_latex contains
    the text between its \\rSec header and the next \\rSec (or EOF).
    """
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


def parse_file(path: Path) -> list[Section]:
    """Parse a single .tex file and return its sections."""
    text = path.read_text(encoding="utf-8")
    return _split_sections(text, path.name)


def parse_directory(source_dir: Path) -> list[Section]:
    """Parse all .tex files in a standard draft source directory.

    Reads source_dir/std.tex to determine include order, then parses
    each included file. Falls back to alphabetical order if std.tex
    is missing or unparseable.
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

    return all_sections
