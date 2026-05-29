#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for the LaTeX parser."""

from __future__ import annotations

from cpp_mcp.parser import (
    Section,
    _assign_section_numbers,
    _classify_normative_force,
    _clean_latex_term,
    _split_sections,
    eel_is_url,
    expand_macros,
    extract_defined_terms,
    extract_grammar_rules,
    extract_index_terms,
    extract_library_declarations,
    extract_mechanisms,
    extract_paragraphs,
)


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


# -----------------------------------------------------------------------
# _clean_latex_term
# -----------------------------------------------------------------------


def test_clean_latex_term_strips_brace_command():
    assert _clean_latex_term(r"\idxcode{vector}") == "vector"


def test_clean_latex_term_removes_index_suffixes():
    assert _clean_latex_term(r"vector|see{container}") == "vector"
    assert _clean_latex_term(r"lifetime@concept") == "lifetime"
    assert _clean_latex_term(r"swap!member") == "swap"


def test_clean_latex_term_strips_leftover_command():
    assert _clean_latex_term(r"\idxcode") == ""


# -----------------------------------------------------------------------
# eel_is_url
# -----------------------------------------------------------------------


def test_eel_is_url():
    assert eel_is_url("basic.life") == "https://eel.is/c++draft/basic.life"
    assert eel_is_url("expr.prim.lambda") == "https://eel.is/c++draft/expr.prim.lambda"


# -----------------------------------------------------------------------
# extract_index_terms
# -----------------------------------------------------------------------


def test_extract_index_terms_indextext():
    terms = extract_index_terms(r"\indextext{object lifetime}")
    assert ("text", "object lifetime") in terms


def test_extract_index_terms_indexlibrary():
    terms = extract_index_terms(r"\indexlibrary{vector}")
    assert ("library", "vector") in terms


def test_extract_index_terms_indexdefn():
    terms = extract_index_terms(r"\indexdefn{trivially copyable}")
    assert ("defn", "trivially copyable") in terms


def test_extract_index_terms_indeximpldef():
    terms = extract_index_terms(r"\indeximpldef{size of bool}")
    assert ("impldef", "size of bool") in terms


def test_extract_index_terms_indexconcept():
    terms = extract_index_terms(r"\indexconcept{same_as}")
    assert ("concept", "same_as") in terms


def test_extract_index_terms_indexgrammar():
    terms = extract_index_terms(r"\indexgrammar{expression}")
    assert ("grammar", "expression") in terms


def test_extract_index_terms_empty():
    assert extract_index_terms("") == []


# -----------------------------------------------------------------------
# extract_mechanisms
# -----------------------------------------------------------------------


def test_extract_mechanisms_keyword():
    mechs = extract_mechanisms(r"\keyword{auto}")
    assert ("keyword", "auto") in mechs


def test_extract_mechanisms_tcode():
    mechs = extract_mechanisms(r"\tcode{vector}")
    assert ("code", "vector") in mechs


def test_extract_mechanisms_libglobal():
    mechs = extract_mechanisms(r"\libglobal{swap}")
    assert ("library", "swap") in mechs


def test_extract_mechanisms_grammarterm():
    mechs = extract_mechanisms(r"\grammarterm{expression}")
    assert ("grammar", "expression") in mechs


def test_extract_mechanisms_defn():
    mechs = extract_mechanisms(r"\defn{lifetime}")
    assert ("defn", "lifetime") in mechs


def test_extract_mechanisms_defnadj():
    mechs = extract_mechanisms(r"\defnadj{trivially}{copyable}")
    assert ("defn", "trivially copyable") in mechs


def test_extract_mechanisms_tcode_single_char_filtered():
    mechs = extract_mechanisms(r"\tcode{x}")
    assert not any(cat == "code" and name == "x" for cat, name in mechs)


def test_extract_mechanisms_dedup():
    mechs = extract_mechanisms(r"\keyword{auto} some text \keyword{auto}")
    auto_entries = [m for m in mechs if m == ("keyword", "auto")]
    assert len(auto_entries) == 1


# -----------------------------------------------------------------------
# extract_defined_terms
# -----------------------------------------------------------------------


def test_extract_defined_terms_defn():
    terms = extract_defined_terms(r"\defn{lifetime}")
    assert "lifetime" in terms


def test_extract_defined_terms_defnadj():
    terms = extract_defined_terms(r"\defnadj{trivially}{copyable}")
    assert "trivially copyable" in terms


def test_extract_defined_terms_defnx():
    terms = extract_defined_terms(r"\defnx{object}{object model}")
    assert "object" in terms


def test_extract_defined_terms_definition():
    terms = extract_defined_terms(r"\definition{well-formed}{defns.well.formed}")
    assert "well-formed" in terms


# -----------------------------------------------------------------------
# extract_grammar_rules
# -----------------------------------------------------------------------


def test_extract_grammar_rules_bnf():
    tex = (
        r"\begin{bnf}" "\n"
        r"\nontermdef{expr}" "\n"
        r"assignment-expression" "\n"
        r"\end{bnf}"
    )
    rules = extract_grammar_rules(tex)
    assert len(rules) == 1
    assert rules[0][0] == "expr"
    assert "assignment-expression" in rules[0][1]


def test_extract_grammar_rules_multiple_nonterminals():
    tex = (
        r"\begin{bnf}" "\n"
        r"\nontermdef{primary-expression}" "\n"
        r"literal" "\n"
        r"\nontermdef{postfix-expression}" "\n"
        r"primary-expression" "\n"
        r"\end{bnf}"
    )
    rules = extract_grammar_rules(tex)
    assert len(rules) == 2
    nts = {r[0] for r in rules}
    assert "primary-expression" in nts
    assert "postfix-expression" in nts


def test_extract_grammar_rules_ncbnf():
    tex = (
        r"\begin{ncbnf}" "\n"
        r"\nontermdef{type-specifier}" "\n"
        r"simple-type-specifier" "\n"
        r"\end{ncbnf}"
    )
    rules = extract_grammar_rules(tex)
    assert len(rules) == 1
    assert rules[0][0] == "type-specifier"


def test_extract_grammar_rules_no_nontermdef():
    tex = (
        r"\begin{bnf}" "\n"
        r"just some raw grammar text" "\n"
        r"\end{bnf}"
    )
    rules = extract_grammar_rules(tex)
    assert rules == []


# -----------------------------------------------------------------------
# extract_library_declarations
# -----------------------------------------------------------------------


def test_extract_library_declarations_pair():
    tex = (
        r"\begin{itemdecl}" "\n"
        r"void swap(T& a, T& b);" "\n"
        r"\end{itemdecl}" "\n"
        r"\begin{itemdescr}" "\n"
        r"\effects Exchanges values stored in a and b." "\n"
        r"\end{itemdescr}"
    )
    decls = extract_library_declarations(tex)
    assert len(decls) == 1
    assert "swap" in decls[0].declaration
    assert decls[0].effects is not None


def test_extract_library_declarations_fundesc_fields():
    tex = (
        r"\begin{itemdecl}" "\n"
        r"T pop();" "\n"
        r"\end{itemdecl}" "\n"
        r"\begin{itemdescr}" "\n"
        r"\expects The container is not empty." "\n"
        r"\effects Removes the last element." "\n"
        r"\returns A copy of the removed element." "\n"
        r"\throws Nothing." "\n"
        r"\mandates T is MoveConstructible." "\n"
        r"\constraints sizeof(T) > 0." "\n"
        r"\complexity Constant." "\n"
        r"\remarks Invalidates iterators." "\n"
        r"\end{itemdescr}"
    )
    decls = extract_library_declarations(tex)
    assert len(decls) == 1
    d = decls[0]
    assert d.preconditions is not None
    assert d.effects is not None
    assert d.returns is not None
    assert d.throws is not None
    assert d.mandates is not None
    assert d.constraints is not None
    assert d.complexity is not None
    assert d.remarks is not None


def test_extract_library_declarations_more_decls_than_descrs():
    tex = (
        r"\begin{itemdecl}" "\n"
        r"void f();" "\n"
        r"\end{itemdecl}" "\n"
        r"\begin{itemdecl}" "\n"
        r"void g();" "\n"
        r"\end{itemdecl}" "\n"
        r"\begin{itemdescr}" "\n"
        r"Description of f." "\n"
        r"\end{itemdescr}"
    )
    decls = extract_library_declarations(tex)
    assert len(decls) == 2
    assert decls[0].description != ""
    assert decls[1].description == ""


# -----------------------------------------------------------------------
# extract_paragraphs
# -----------------------------------------------------------------------


def test_extract_paragraphs_split():
    tex = r"\pnum First paragraph." "\n" r"\pnum Second paragraph."
    paras = extract_paragraphs(tex)
    assert len(paras) == 2
    assert "First" in paras[0].cleaned_text
    assert "Second" in paras[1].cleaned_text


def test_extract_paragraphs_numbering():
    tex = r"\pnum A" "\n" r"\pnum B" "\n" r"\pnum C"
    paras = extract_paragraphs(tex)
    assert [p.number for p in paras] == [1, 2, 3]


def test_extract_paragraphs_content_before_first_pnum_skipped():
    tex = "This preamble text is long enough to push pnum past twenty characters.\n" r"\pnum Real paragraph."
    paras = extract_paragraphs(tex)
    assert len(paras) == 1
    assert "preamble" not in paras[0].cleaned_text
    assert "Real" in paras[0].cleaned_text


# -----------------------------------------------------------------------
# _classify_normative_force
# -----------------------------------------------------------------------


def test_classify_normative_force_note():
    assert _classify_normative_force(r"\begin{note}This is a note.\end{note}") == "note"


def test_classify_normative_force_example():
    assert _classify_normative_force(r"\begin{example}int x = 0;\end{example}") == "example"


def test_classify_normative_force_shall():
    assert _classify_normative_force("The implementation shall do X.") == "requirement"


def test_classify_normative_force_undefined_behavior():
    assert _classify_normative_force("the behavior is undefined") == "undefined_behavior"


def test_classify_normative_force_ill_formed():
    assert _classify_normative_force("the program is ill-formed") == "ill_formed"


def test_classify_normative_force_ndr():
    assert _classify_normative_force("no diagnostic required") == "ndr"


def test_classify_normative_force_impl_defined():
    assert _classify_normative_force("implementation-defined") == "impl_defined"


def test_classify_normative_force_default():
    assert _classify_normative_force("Types are fundamental to C++.") == "normative"


# -----------------------------------------------------------------------
# _assign_section_numbers
# -----------------------------------------------------------------------


def test_assign_section_numbers_hierarchy():
    sections = [
        Section(stable_label="a", title="A", depth=0, parent_label=None, chapter_file="a.tex", raw_latex=""),
        Section(stable_label="a.b", title="B", depth=1, parent_label="a", chapter_file="a.tex", raw_latex=""),
        Section(stable_label="a.c", title="C", depth=1, parent_label="a", chapter_file="a.tex", raw_latex=""),
        Section(stable_label="a.c.d", title="D", depth=2, parent_label="a.c", chapter_file="a.tex", raw_latex=""),
        Section(stable_label="e", title="E", depth=0, parent_label=None, chapter_file="a.tex", raw_latex=""),
    ]
    _assign_section_numbers(sections)
    assert sections[0].section_number == "1"
    assert sections[1].section_number == "1.1"
    assert sections[2].section_number == "1.2"
    assert sections[3].section_number == "1.2.1"
    assert sections[4].section_number == "2"


def test_assign_section_numbers_depth_reset():
    sections = [
        Section(stable_label="ch1", title="Ch1", depth=0, parent_label=None, chapter_file="a.tex", raw_latex=""),
        Section(stable_label="ch1.s1", title="S1", depth=1, parent_label="ch1", chapter_file="a.tex", raw_latex=""),
        Section(stable_label="ch1.s1.sub", title="Sub", depth=2, parent_label="ch1.s1", chapter_file="a.tex", raw_latex=""),
        Section(stable_label="ch2", title="Ch2", depth=0, parent_label=None, chapter_file="b.tex", raw_latex=""),
        Section(stable_label="ch2.s1", title="S1", depth=1, parent_label="ch2", chapter_file="b.tex", raw_latex=""),
    ]
    _assign_section_numbers(sections)
    assert sections[3].section_number == "2"
    assert sections[4].section_number == "2.1"


# -----------------------------------------------------------------------
# expand_macros: note / example markers
# -----------------------------------------------------------------------


def test_expand_macros_note_markers():
    result = expand_macros(r"\begin{note}This is advisory.\end{note}")
    assert result == "[Note: This is advisory. --end note]"


def test_expand_macros_example_markers():
    result = expand_macros(r"\begin{example}int x = 0;\end{example}")
    assert result == "[Example: int x = 0; --end example]"
