# cpp-mcp - Agent Rules

## What this is

MCP server for the C++ standard (ISO/IEC 14882). Parses the LaTeX source from cplusplus/draft, extracts rich structured data mechanically (no LLM), stores in SQLite with FTS5, and serves via FastMCP over HTTP.

## Module layout

- `backend.py` -- `StandardBackend` ABC plus frozen dataclasses: `SectionRow`, `DraftInfo`, `IndexTermRow`, `MechanismRow`, `GrammarRuleRow`, `DefinedTermRow`, `LibraryDeclRow`, `ParagraphRow`.
- `sqlite_backend.py` -- `SqliteStandardBackend` (production implementation, SQLite + FTS5 + 7 auxiliary tables).
- `parser.py` -- LaTeX parser with extraction of: sections, cross-references, index terms, mechanisms, grammar rules, defined terms, library declarations, paragraphs, normative force, note/example markers.
- `ingest.py` -- Clone cplusplus/draft, parse, extract, load into backend. Atomic ingestion (stage-then-rename). Skip-if-unchanged for trunk.
- `versions.py` -- Tag-to-standard-version mapping (n5046 -> C++26, etc.) and version shorthand resolution.
- `server.py` -- FastMCP server with 18+ tools, auth middleware, MCP prompts and resources, guide_query routing.
- `__main__.py` -- CLI entry point (`cpp-mcp ingest`, `cpp-mcp serve`).

## Database tables

- `standard_sections` -- sections with raw_latex, cleaned_text, section_number, is_deprecated, is_synopsis
- `drafts` -- version metadata including standard_version and version_note
- `sections_fts` -- FTS5 virtual table for full-text search (C++-aware tokenizer)
- `section_xrefs` -- cross-reference graph (from_label -> to_label)
- `section_index_terms` -- index terms by category (text, library, grammar, defn, impldef, concept)
- `mechanisms` -- named entities that exist in the standard (code, keyword, library, concept, grammar, defn, zombie)
- `grammar_rules` -- BNF nonterminal definitions
- `defined_terms` -- defined term glossary
- `library_declarations` -- itemdecl/itemdescr pairs with structured Fundesc fields
- `section_paragraphs` -- individual paragraphs with normative force tags

## Invariants

- **All extraction is mechanical.** No LLM calls during ingestion. The C++ standard's LaTeX is the source of truth.
- **`SqliteStandardBackend` is the default backend.** A Postgres backend is a future addition.
- **Raw LaTeX is verbatim.** Never expand macros in `raw_latex`.
- **Multi-version by design.** The `draft_tag` column scopes everything.
- **Atomic ingestion.** Stage-then-rename ensures queries never see partial data.
- **Auth is explicit.** `--keys-file` (production) or `--no-auth` (local dev). Omitting both is an error.
- **Version shorthands.** All tools accept `draft="C++23"` as well as `draft="n4950"`.

## CLI

```bash
cpp-mcp ingest --tag n5046       # ingest a draft
cpp-mcp ingest --tag main        # ingest trunk
cpp-mcp serve --no-auth          # HTTP on localhost:8001 (local dev)
cpp-mcp serve --keys-file /etc/cpp-mcp/keys  # production with auth
```
