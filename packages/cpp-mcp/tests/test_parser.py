#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for the LaTeX parser."""

from __future__ import annotations

from cpp_mcp.parser import _split_sections, expand_macros


BASIC_TEX = r"""
\rSec0[basic]{Basic concepts}

Some introductory text.

\pnum
First paragraph of basic concepts.

\rSec1[basic.life]{Object lifetime}

\pnum
The lifetime of an object begins when storage is obtained.

\pnum
The lifetime of an object ends when the destructor call starts.

\rSec2[basic.life.general]{General}

\pnum
General rules about object lifetime.

\rSec1[basic.types]{Types}

\pnum
Types are fundamental to \Cpp{}.
"""

LIBRARY_TEX = r"""
\rSec0[containers]{Containers library}

\rSec1[containers.general]{General}

\pnum
This clause describes containers.

\rSec2[vector]{Class template vector}

\begin{itemdecl}
template<class T, class Allocator = allocator<T>>
class vector;
\end{itemdecl}

\begin{itemdescr}
\pnum
A vector is a sequence container.
\end{itemdescr}
"""


def test_rsec_extraction():
    sections = _split_sections(BASIC_TEX, "basic.tex")
    assert len(sections) == 4
    assert sections[0].stable_label == "basic"
    assert sections[0].depth == 0
    assert sections[1].stable_label == "basic.life"
    assert sections[1].depth == 1
    assert sections[2].stable_label == "basic.life.general"
    assert sections[2].depth == 2
    assert sections[3].stable_label == "basic.types"
    assert sections[3].depth == 1


def test_parent_hierarchy():
    sections = _split_sections(BASIC_TEX, "basic.tex")
    by_label = {s.stable_label: s for s in sections}
    assert by_label["basic"].parent_label is None
    assert by_label["basic.life"].parent_label == "basic"
    assert by_label["basic.life.general"].parent_label == "basic.life"
    assert by_label["basic.types"].parent_label == "basic"


def test_paragraph_count():
    sections = _split_sections(BASIC_TEX, "basic.tex")
    by_label = {s.stable_label: s for s in sections}
    assert by_label["basic"].paragraph_count == 1
    assert by_label["basic.life"].paragraph_count == 2
    assert by_label["basic.life.general"].paragraph_count == 1
    assert by_label["basic.types"].paragraph_count == 1


def test_chapter_file_set():
    sections = _split_sections(BASIC_TEX, "basic.tex")
    assert all(s.chapter_file == "basic.tex" for s in sections)


def test_raw_latex_verbatim():
    sections = _split_sections(BASIC_TEX, "basic.tex")
    types_section = next(s for s in sections if s.stable_label == "basic.types")
    assert r"\Cpp" in types_section.raw_latex


def test_cleaned_text_expanded():
    sections = _split_sections(BASIC_TEX, "basic.tex")
    types_section = next(s for s in sections if s.stable_label == "basic.types")
    assert "C++" in types_section.cleaned_text
    assert r"\Cpp" not in types_section.cleaned_text


def test_title_extraction():
    sections = _split_sections(BASIC_TEX, "basic.tex")
    assert sections[0].title == "Basic concepts"
    assert sections[1].title == "Object lifetime"


def test_empty_input():
    sections = _split_sections("", "empty.tex")
    assert sections == []


def test_no_rsec():
    sections = _split_sections("Just some text without sections.", "nosec.tex")
    assert sections == []


def test_itemdecl_preserved_in_section():
    sections = _split_sections(LIBRARY_TEX, "containers.tex")
    vector_section = next(s for s in sections if s.stable_label == "vector")
    assert "itemdecl" in vector_section.raw_latex
    assert "itemdescr" in vector_section.raw_latex


# -----------------------------------------------------------------------
# Macro expansion tests
# -----------------------------------------------------------------------


def test_expand_tcode():
    assert "vector" in expand_macros(r"\tcode{vector}")
    assert r"\tcode" not in expand_macros(r"\tcode{vector}")


def test_expand_cpp():
    assert expand_macros(r"\Cpp") == "C++"


def test_expand_grammarterm():
    result = expand_macros(r"\grammarterm{expression}")
    assert "expression" in result
    assert r"\grammarterm" not in result


def test_expand_iref():
    result = expand_macros(r"\iref{basic.life}")
    assert "[basic.life]" in result


def test_strip_indextext():
    result = expand_macros(r"\indextext{lifetime}The lifetime")
    assert "indextext" not in result
    assert "The lifetime" in result


def test_strip_label():
    result = expand_macros(r"\label{sec:basic}Content here")
    assert "label" not in result
    assert "Content here" in result


def test_codeblock_to_fenced():
    text = r"""
\begin{codeblock}
int x = 42;
\end{codeblock}
"""
    result = expand_macros(text)
    assert "```cpp" in result
    assert "int x = 42;" in result


def test_bnf_preserved():
    text = r"""
\begin{bnf}
expression: assignment-expression
\end{bnf}
"""
    result = expand_macros(text)
    assert "expression: assignment-expression" in result


def test_deeply_nested_rsec():
    """Depths beyond 3 (rSec4, rSec5) should still parse."""
    tex = r"""
\rSec0[top]{Top}
\rSec1[mid]{Mid}
\rSec2[low]{Low}
\rSec3[lower]{Lower}
\rSec4[lowest]{Lowest}
\rSec5[bottom]{Bottom}
"""
    sections = _split_sections(tex, "deep.tex")
    assert len(sections) == 6
    assert sections[4].depth == 4
    assert sections[5].depth == 5


def test_section_with_no_pnum():
    tex = r"""
\rSec0[empty]{Empty section}

Just text, no paragraph markers.
"""
    sections = _split_sections(tex, "nopnum.tex")
    assert sections[0].paragraph_count == 0
