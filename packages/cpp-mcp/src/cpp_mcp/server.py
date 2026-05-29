#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""FastMCP server exposing C++ standard lookup, search, and analysis tools."""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import threading
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware

from cpp_mcp.backend import SectionRow, StandardBackend
from cpp_mcp.parser import eel_is_url
from cpp_mcp.sqlite_backend import SqliteStandardBackend
from cpp_mcp.versions import resolve_draft_for_version

log = logging.getLogger(__name__)

DEFAULT_PORT = 8001
DEFAULT_DATA_DIR = Path.home() / ".cpp-mcp"
DATA_DIR_ENV = "CPP_MCP_DATA_DIR"
DEFAULT_DRAFT_ENV = "CPP_MCP_DEFAULT_DRAFT"
KEYS_FILE_ENV = "CPP_MCP_KEYS_FILE"

SNIPPET_TRUNCATION_CHARS = 200

_VERSION_PREFIX_RE = re.compile(r"^c\+\+", re.IGNORECASE)

_GUIDE_LABEL_RE = re.compile(r"\[([a-z][a-z0-9._]+)\]")
_GUIDE_EXIST_RE = re.compile(
    r"\b(?:does|is)\b.*\b(?:exist|in the standard)\b", re.IGNORECASE
)
_GUIDE_DEFINE_RE = re.compile(
    r"\bwhat does\b.*\bmean\b|\bdefine\b|\bdefinition of\b", re.IGNORECASE
)
_GUIDE_GRAMMAR_RE = re.compile(
    r"\bgrammar\b|\bproduction\b|\bBNF\b", re.IGNORECASE
)
_GUIDE_SPEC_RE = re.compile(
    r"\bprecondition\b|\beffect\b|\breturn\b|\bspecification of\b",
    re.IGNORECASE,
)
_GUIDE_XREF_RE = re.compile(
    r"\breference\b|\bcross-reference\b|\bconflicts? with\b", re.IGNORECASE
)

_SERVER_INSTRUCTIONS = """\
Search and browse the C++ standard (ISO/IEC 14882).

Tool selection guide:
- Exact section by stable label (e.g. [basic.life]) -> lookup_section or lookup_sections (batch)
- Check if a C++ mechanism exists (type, function, keyword) -> verify_mechanism
- Conceptual/natural-language queries -> semantic_search
- Keyword-exact queries -> search_standard
- Which sections define a term or concept -> search_index
- Definition of a standard-defined term -> lookup_definition
- Library API specifications (preconditions, effects, etc.) -> lookup_declaration
- Grammar rules (BNF productions) -> search_grammar
- Relationships between sections -> get_cross_references
- Specific paragraph -> lookup_paragraph
- Not sure which tool? -> guide_query

All tools accept an optional 'draft' parameter. Use list_drafts to see available versions.
You can pass a version shorthand like 'C++23' or 'C++26' instead of a tag number.\
"""


def _load_keys(keys_path: str | Path | None) -> set[str]:
    """Load bearer tokens from a keys file (one per line, # comments)."""
    if keys_path is None:
        return set()
    path = Path(keys_path)
    if not path.is_file():
        log.warning("Keys file %s does not exist; auth disabled", path)
        return set()
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            keys.add(stripped)
    log.info("Loaded %d API key(s) from %s", len(keys), path)
    return keys


def _format_section(row: SectionRow) -> dict:
    """Convert a SectionRow to a JSON-friendly dict."""
    return {
        "stable_label": row.stable_label,
        "title": row.title,
        "depth": row.depth,
        "section_number": row.section_number,
        "parent_label": row.parent_label,
        "chapter_file": row.chapter_file,
        "draft_tag": row.draft_tag,
        "raw_latex": row.raw_latex,
        "cleaned_text": row.cleaned_text,
        "paragraph_count": row.paragraph_count,
    }


def _format_section_brief(row: SectionRow) -> dict:
    """Compact section representation for list results."""
    return {
        "stable_label": row.stable_label,
        "title": row.title,
        "depth": row.depth,
        "chapter_file": row.chapter_file,
        "draft_tag": row.draft_tag,
    }


def _format_with_url(d: dict) -> dict:
    """Add an eel.is URL for any dict that carries a stable_label."""
    if "stable_label" in d:
        d["url"] = eel_is_url(d["stable_label"])
    return d


class _BearerKeyMiddleware(Middleware):
    """Reject requests without a valid bearer token."""

    def __init__(self, keys: set[str], keys_lock: threading.Lock) -> None:
        self._keys = keys
        self._keys_lock = keys_lock

    @property
    def keys(self) -> set[str]:
        with self._keys_lock:
            return set(self._keys)

    def update_keys(self, new_keys: set[str]) -> None:
        with self._keys_lock:
            self._keys = new_keys

    async def on_request(self, context, call_next):
        try:
            from fastmcp.server.dependencies import get_http_request
            request = get_http_request()
            auth_header = request.headers.get("authorization", "")
        except (LookupError, RuntimeError):
            # No HTTP context (stdio transport or test) -- skip auth
            return await call_next(context)

        if not auth_header.lower().startswith("bearer "):
            raise Exception("Unauthorized: missing or invalid Authorization header")

        token = auth_header[7:]
        if token not in self.keys:
            raise Exception("Unauthorized: invalid API key")

        return await call_next(context)


def create_server(
    backend: StandardBackend,
    default_draft: str | None = None,
    keys_file: str | Path | None = None,
    no_auth: bool = False,
) -> FastMCP:
    """Build the FastMCP server with all tools registered.

    Authentication is required unless *no_auth* is ``True``. When
    *no_auth* is ``False``, a *keys_file* containing at least one key
    must be provided or a ``ValueError`` is raised.
    """

    _keys_lock = threading.Lock()
    _auth_middleware: _BearerKeyMiddleware | None = None

    if no_auth:
        log.warning("Authentication disabled (--no-auth). Do not use in production.")
    else:
        _keys = _load_keys(keys_file)
        if not _keys:
            raise ValueError(
                "No API keys loaded. Provide a --keys-file with at least one key, "
                "or pass --no-auth to explicitly disable authentication."
            )
        _auth_middleware = _BearerKeyMiddleware(_keys, _keys_lock)

    mcp = FastMCP("C++ Standard", instructions=_SERVER_INSTRUCTIONS)

    if _auth_middleware is not None:
        mcp.add_middleware(_auth_middleware)

    def _reload_keys(signum: int, frame: object) -> None:
        if _auth_middleware is None:
            return
        new_keys = _load_keys(keys_file)
        _auth_middleware.update_keys(new_keys)
        log.info("Reloaded API keys on signal %d", signum)

    if keys_file and os.name != "nt":
        signal.signal(signal.SIGHUP, _reload_keys)

    # -----------------------------------------------------------------
    # Draft resolution
    # -----------------------------------------------------------------

    def _resolve_version_shorthand(draft: str) -> str:
        """Resolve 'C++23' style shorthands to a draft tag, or return as-is."""
        if _VERSION_PREFIX_RE.match(draft):
            available = [d.draft_tag for d in backend.list_drafts()]
            resolved = resolve_draft_for_version(draft, available)
            if resolved is not None:
                return resolved
        return draft

    def _resolve_draft(draft: str | None) -> str | None:
        if draft is not None:
            return _resolve_version_shorthand(draft)
        if default_draft is not None:
            return default_draft
        return backend.default_draft_tag()

    _NO_DRAFTS = {"error": "No drafts ingested. Run 'cpp-mcp ingest' first."}

    # =================================================================
    # Existing tools (enhanced)
    # =================================================================

    @mcp.tool()
    def lookup_section(stable_label: str, draft: str | None = None) -> str:
        """Look up a C++ standard section by its stable label (e.g. 'basic.life').

        Returns the section's raw LaTeX, cleaned text, metadata, and eel.is URL.
        """
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps(_NO_DRAFTS)
        row = backend.lookup_section(stable_label, tag)
        if row is None:
            return json.dumps({"error": f"Section [{stable_label}] not found in draft '{tag}'."})
        return json.dumps(_format_with_url(_format_section(row)))

    @mcp.tool()
    def search_standard(
        query: str,
        top_k: int = 10,
        chapter: str | None = None,
        snippet: bool = False,
        draft: str | None = None,
    ) -> str:
        """Full-text search across the C++ standard.

        Returns sections matching the query, ranked by relevance.
        Use chapter (e.g. 'basic.tex') to restrict scope.
        Set snippet=True for abbreviated results (truncated cleaned_text).
        """
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps(_NO_DRAFTS)
        rows = backend.search(query, top_k=top_k, chapter=chapter, draft_tag=tag)
        if snippet:
            results = []
            for r in rows:
                d = _format_section_brief(r)
                text = r.cleaned_text or ""
                if len(text) > SNIPPET_TRUNCATION_CHARS:
                    d["snippet"] = text[:SNIPPET_TRUNCATION_CHARS] + "..."
                else:
                    d["snippet"] = text
                results.append(_format_with_url(d))
            return json.dumps(results)
        return json.dumps([_format_with_url(_format_section(r)) for r in rows])

    @mcp.tool()
    def get_section_with_children(
        stable_label: str, draft: str | None = None
    ) -> str:
        """Get a section and all its sub-sections.

        Useful for retrieving an entire topic area like [basic.life] with
        all its subsections.
        """
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps(_NO_DRAFTS)
        rows = backend.get_section_with_children(stable_label, tag)
        if not rows:
            return json.dumps({"error": f"Section [{stable_label}] not found in draft '{tag}'."})
        return json.dumps([_format_with_url(_format_section(r)) for r in rows])

    @mcp.tool()
    def list_chapters(draft: str | None = None) -> str:
        """List all top-level chapters of the C++ standard."""
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps(_NO_DRAFTS)
        rows = backend.list_chapters(tag)
        return json.dumps([_format_with_url(_format_section_brief(r)) for r in rows])

    @mcp.tool()
    def list_sections(
        chapter: str | None = None,
        depth: int | None = None,
        draft: str | None = None,
    ) -> str:
        """Browse sections of the standard, optionally filtered by chapter and depth.

        Chapter is the .tex filename (e.g. 'basic.tex', 'expressions.tex').
        Depth 0 = chapters, 1 = major sections, 2+ = subsections.
        """
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps(_NO_DRAFTS)
        rows = backend.list_sections(chapter=chapter, depth=depth, draft_tag=tag)
        return json.dumps([_format_with_url(_format_section_brief(r)) for r in rows])

    @mcp.tool()
    def list_drafts() -> str:
        """List all ingested versions of the C++ standard.

        Returns draft tags, standard versions, ingestion dates, section counts,
        and git SHAs. Use a draft tag (or version shorthand like 'C++26') in
        other tools to query a specific version.
        """
        drafts = backend.list_drafts()
        return json.dumps([
            {
                "draft_tag": d.draft_tag,
                "standard_version": d.standard_version,
                "version_note": d.version_note,
                "ingested_at": d.ingested_at,
                "section_count": d.section_count,
                "git_sha": d.git_sha,
            }
            for d in drafts
        ])

    @mcp.tool()
    def diff_section(
        stable_label: str, from_draft: str, to_draft: str
    ) -> str:
        """Compare a section across two draft versions.

        Returns both versions side by side (raw LaTeX and cleaned text).
        Accepts version shorthands (e.g. 'C++23', 'C++26') for either draft.
        """
        from_tag = _resolve_version_shorthand(from_draft)
        to_tag = _resolve_version_shorthand(to_draft)
        left, right = backend.diff_section(stable_label, from_tag, to_tag)
        result: dict = {
            "stable_label": stable_label,
            "from_draft": from_tag,
            "to_draft": to_tag,
        }
        result["from_section"] = _format_with_url(_format_section(left)) if left else None
        result["to_section"] = _format_with_url(_format_section(right)) if right else None
        if left is None and right is None:
            result["error"] = f"Section [{stable_label}] not found in either draft."
        return json.dumps(result)

    # =================================================================
    # New tools
    # =================================================================

    @mcp.tool()
    def verify_mechanism(name: str, draft: str | None = None) -> str:
        """Check if a C++ mechanism (type, function, keyword, concept) exists in the standard.

        Example: verify_mechanism("std::move") or verify_mechanism("constexpr")

        Returns {exists, matches: [{name, category, stable_label, url}], deprecated}.

        When not to use: For section lookup by label, use lookup_section instead.
        """
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps(_NO_DRAFTS)
        rows = backend.verify_mechanism(name, tag)
        matches = [
            _format_with_url({
                "name": r.name,
                "category": r.category,
                "stable_label": r.stable_label,
            })
            for r in rows
        ]
        has_deprecated = any(r.category == "zombie" for r in rows)
        return json.dumps({
            "exists": len(rows) > 0,
            "matches": matches,
            "deprecated": has_deprecated,
        })

    @mcp.tool()
    def search_index(
        term: str,
        category: str | None = None,
        draft: str | None = None,
    ) -> str:
        """Find which sections of the standard are indexed under a term.

        Example: search_index("overload resolution") or search_index("vector", category="library")

        Returns [{stable_label, category, term, url}].

        When not to use: For the normative definition of a term, use lookup_definition.
        """
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps(_NO_DRAFTS)
        rows = backend.search_index(term, category=category, draft_tag=tag)
        return json.dumps([
            _format_with_url({
                "stable_label": r.stable_label,
                "category": r.category,
                "term": r.term,
            })
            for r in rows
        ])

    @mcp.tool()
    def lookup_declaration(pattern: str, draft: str | None = None) -> str:
        """Get library API specifications (preconditions, effects, returns, etc.).

        Example: lookup_declaration("push_back") or lookup_declaration("std::sort")

        Returns [{stable_label, declaration, preconditions, effects, postconditions,
        returns, throws, mandates, constraints, complexity, remarks, url}].

        When not to use: For non-library sections, use lookup_section.
        """
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps(_NO_DRAFTS)
        rows = backend.lookup_declarations(pattern, tag)
        return json.dumps([
            _format_with_url({
                "stable_label": r.stable_label,
                "declaration": r.declaration,
                "preconditions": r.preconditions,
                "effects": r.effects,
                "postconditions": r.postconditions,
                "returns": r.returns,
                "throws": r.throws,
                "mandates": r.mandates,
                "constraints": r.constraints,
                "complexity": r.complexity,
                "remarks": r.remarks,
            })
            for r in rows
        ])

    @mcp.tool()
    def search_grammar(nonterminal: str, draft: str | None = None) -> str:
        """Find a grammar production rule by its nonterminal name.

        Example: search_grammar("expression") or search_grammar("declaration")

        Returns {nonterminal, stable_label, raw_rule, url} or an error.

        When not to use: For index terms related to grammar, use search_index
        with category="grammar".
        """
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps(_NO_DRAFTS)
        row = backend.search_grammar(nonterminal, tag)
        if row is None:
            return json.dumps({"error": f"No grammar rule for '{nonterminal}' in draft '{tag}'."})
        return json.dumps(_format_with_url({
            "nonterminal": row.nonterminal,
            "stable_label": row.stable_label,
            "raw_rule": row.raw_rule,
        }))

    @mcp.tool()
    def get_cross_references(
        stable_label: str,
        direction: str = "both",
        draft: str | None = None,
    ) -> str:
        """Explore relationships between standard sections.

        Example: get_cross_references("basic.life", direction="from")

        direction: "from" (outgoing), "to" (incoming), or "both".
        Returns {stable_label, from: [labels], to: [labels]}.

        When not to use: For fetching section content, use lookup_section.
        """
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps(_NO_DRAFTS)
        allowed = {"from", "to", "both"}
        if direction not in allowed:
            return json.dumps({
                "error": f"direction must be one of {sorted(allowed)}, got '{direction}'."
            })
        result: dict = {"stable_label": stable_label}
        if direction in ("from", "both"):
            result["from"] = backend.get_references_from(stable_label, tag)
        if direction in ("to", "both"):
            result["to"] = backend.get_references_to(stable_label, tag)
        return json.dumps(result)

    @mcp.tool()
    def semantic_search(
        query: str,
        top_k: int = 10,
        chapter: str | None = None,
        draft: str | None = None,
    ) -> str:
        """Find standard sections by meaning, not just keywords.

        Example: semantic_search("when does an object's lifetime begin")

        Returns sections ranked by relevance. Currently uses FTS5 keyword
        search; embedding-based retrieval will be added when the
        infrastructure is ready.

        When not to use: For exact label lookup, use lookup_section.
        """
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps(_NO_DRAFTS)
        rows = backend.search(query, top_k=top_k, chapter=chapter, draft_tag=tag)
        return json.dumps([_format_with_url(_format_section(r)) for r in rows])

    @mcp.tool()
    def lookup_sections(
        stable_labels: list[str], draft: str | None = None
    ) -> str:
        """Fetch multiple standard sections in one call.

        Example: lookup_sections(["basic.life", "basic.stc", "expr.prim"])

        Returns a list of full section objects (same format as lookup_section).

        When not to use: For a single section, use lookup_section.
        """
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps(_NO_DRAFTS)
        rows = backend.lookup_sections(stable_labels, tag)
        return json.dumps([_format_with_url(_format_section(r)) for r in rows])

    @mcp.tool()
    def lookup_definition(term: str, draft: str | None = None) -> str:
        """Get the standard's normative definition of a term.

        Example: lookup_definition("undefined behavior") or
        lookup_definition("lvalue")

        Returns {term, stable_label, definition_text, url} or an error.

        When not to use: For non-definition sections that mention a term,
        use search_standard.
        """
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps(_NO_DRAFTS)
        row = backend.lookup_definition(term, tag)
        if row is None:
            return json.dumps({"error": f"No defined term '{term}' in draft '{tag}'."})
        return json.dumps(_format_with_url({
            "term": row.term,
            "stable_label": row.stable_label,
            "definition_text": row.definition_text,
        }))

    @mcp.tool()
    def lookup_paragraph(
        stable_label: str,
        paragraph: int,
        draft: str | None = None,
    ) -> str:
        """Get a specific paragraph of a standard section.

        Example: lookup_paragraph("basic.life", 1)

        Returns {stable_label, paragraph_number, cleaned_text, raw_latex,
        normative_force, url}.

        When not to use: For the whole section, use lookup_section.
        """
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps(_NO_DRAFTS)
        row = backend.lookup_paragraph(stable_label, paragraph, tag)
        if row is None:
            return json.dumps({
                "error": f"Paragraph {paragraph} of [{stable_label}] not found in draft '{tag}'."
            })
        return json.dumps(_format_with_url({
            "stable_label": row.stable_label,
            "paragraph_number": row.paragraph_number,
            "cleaned_text": row.cleaned_text,
            "raw_latex": row.raw_latex,
            "normative_force": row.normative_force,
        }))

    @mcp.tool()
    def get_ancestors(stable_label: str, draft: str | None = None) -> str:
        """Get the parent chain from a section up to the chapter root.

        Example: get_ancestors("basic.life.init")

        Returns [{stable_label, title, depth, section_number, url}] ordered
        from the chapter root down to the immediate parent.

        When not to use: For the section itself, use lookup_section.
        """
        tag = _resolve_draft(draft)
        if tag is None:
            return json.dumps(_NO_DRAFTS)
        rows = backend.get_ancestors(stable_label, tag)
        return json.dumps([
            _format_with_url({
                "stable_label": r.stable_label,
                "title": r.title,
                "depth": r.depth,
                "section_number": r.section_number,
            })
            for r in rows
        ])

    @mcp.tool()
    def guide_query(question: str) -> str:
        """Not sure which tool to use? Describe what you need and get a recommendation.

        Example: guide_query("does std::move exist in C++11?")

        Returns {recommended_tool, parameters, explanation}.
        Pure logic routing -- no database queries.
        """
        q = question.strip()

        label_match = _GUIDE_LABEL_RE.search(q)
        if label_match:
            label = label_match.group(1)
            return json.dumps({
                "recommended_tool": "lookup_section",
                "parameters": {"stable_label": label},
                "explanation": (
                    f"The question references [{label}]. "
                    "Use lookup_section to fetch it directly."
                ),
            })

        if _GUIDE_EXIST_RE.search(q):
            return json.dumps({
                "recommended_tool": "verify_mechanism",
                "parameters": {},
                "explanation": (
                    "This looks like a mechanism-existence check. "
                    "Extract the mechanism name and pass it to verify_mechanism."
                ),
            })

        if _GUIDE_DEFINE_RE.search(q):
            return json.dumps({
                "recommended_tool": "lookup_definition",
                "parameters": {},
                "explanation": (
                    "This looks like a request for a normative definition. "
                    "Extract the term and pass it to lookup_definition."
                ),
            })

        if _GUIDE_GRAMMAR_RE.search(q):
            return json.dumps({
                "recommended_tool": "search_grammar",
                "parameters": {},
                "explanation": (
                    "This asks about grammar or productions. "
                    "Extract the nonterminal and pass it to search_grammar."
                ),
            })

        if _GUIDE_SPEC_RE.search(q):
            return json.dumps({
                "recommended_tool": "lookup_declaration",
                "parameters": {},
                "explanation": (
                    "This asks about library specification elements. "
                    "Extract the function/type name and pass it to "
                    "lookup_declaration."
                ),
            })

        if _GUIDE_XREF_RE.search(q):
            return json.dumps({
                "recommended_tool": "get_cross_references",
                "parameters": {},
                "explanation": (
                    "This asks about section relationships. "
                    "Identify the section label and pass it to "
                    "get_cross_references."
                ),
            })

        return json.dumps({
            "recommended_tool": "semantic_search",
            "parameters": {},
            "explanation": (
                "No specific pattern matched. Use semantic_search with "
                "a natural-language description of what you are looking for."
            ),
        })

    # =================================================================
    # MCP Prompts
    # =================================================================

    @mcp.prompt()
    def verify_standard_reference(stable_label: str) -> str:
        """Multi-step workflow to verify a C++ standard section reference."""
        return (
            f"Verify the C++ standard reference [{stable_label}].\n\n"
            f"1. Call lookup_section(stable_label=\"{stable_label}\") "
            "to fetch the section.\n"
            "2. If not found, call search_standard with the label as a query "
            "to find similar or renamed sections.\n"
            f"3. Call get_cross_references(stable_label=\"{stable_label}\") "
            "to see what sections reference it and what it references.\n"
            f"4. Call get_ancestors(stable_label=\"{stable_label}\") to "
            "understand its position in the standard's hierarchy.\n"
            "5. Summarize: does the reference exist, what does the section "
            "cover, and what are its key relationships?"
        )

    @mcp.prompt()
    def check_mechanism_exists(name: str) -> str:
        """Multi-step workflow to check whether a C++ mechanism exists."""
        return (
            f"Check whether the C++ mechanism '{name}' exists in the "
            "standard.\n\n"
            f"1. Call verify_mechanism(name=\"{name}\") to check the "
            "mechanisms table.\n"
            f"2. Call search_index(term=\"{name}\") to find index entries.\n"
            f"3. Call search_standard(query=\"{name}\") for broader text "
            "matches.\n"
            "4. If found, call lookup_section on the relevant stable_label "
            "to read the normative text.\n"
            "5. Summarize: does it exist, in which sections, and is any "
            "form deprecated?"
        )

    @mcp.prompt()
    def research_specification_topic(topic: str) -> str:
        """Multi-step workflow to research a specification topic in depth."""
        return (
            f"Research the specification topic: {topic}\n\n"
            f"1. Call semantic_search(query=\"{topic}\") for an initial set "
            "of relevant sections.\n"
            f"2. Call search_index(term=\"{topic}\") to find indexed "
            "sections.\n"
            "3. For each highly relevant section, call lookup_section to "
            "read the full text.\n"
            "4. Call get_cross_references on the most relevant sections to "
            "discover related material.\n"
            "5. If the topic involves library APIs, call lookup_declaration "
            "with relevant function or class names.\n"
            "6. If the topic involves defined terms, call "
            "lookup_definition.\n"
            "7. Synthesize the findings into a structured summary with "
            "section references."
        )

    @mcp.prompt()
    def evaluate_proposed_wording(stable_label: str) -> str:
        """Multi-step workflow to evaluate proposed changes to a standard section."""
        return (
            f"Evaluate proposed wording changes to [{stable_label}].\n\n"
            f"1. Call lookup_section(stable_label=\"{stable_label}\") to "
            "get the current text.\n"
            f"2. Call get_ancestors(stable_label=\"{stable_label}\") to "
            "understand the section's context in the document hierarchy.\n"
            "3. Call get_cross_references("
            f"stable_label=\"{stable_label}\", direction=\"to\") to find "
            "all sections that reference this one (impact analysis).\n"
            "4. Call get_cross_references("
            f"stable_label=\"{stable_label}\", direction=\"from\") to find "
            "all sections this one depends on.\n"
            "5. For each referenced section, assess whether the proposed "
            "changes would create inconsistencies.\n"
            "6. Call list_drafts() to check if the wording has changed "
            "across versions.\n"
            "7. If multiple drafts are available, call diff_section to "
            "compare the section across versions.\n"
            "8. Report: current wording, cross-reference impact, "
            "consistency risks, and version history."
        )

    # =================================================================
    # MCP Resources
    # =================================================================

    @mcp.resource("standard://capabilities")
    def capabilities() -> str:
        """Description of what this server can do."""
        return (
            "C++ Standard MCP Server Capabilities\n"
            "=====================================\n\n"
            "This server provides structured access to the ISO C++ standard "
            "(ISO/IEC 14882) parsed from the cplusplus/draft LaTeX source.\n\n"
            "Data available:\n"
            "- Full section text (raw LaTeX and cleaned plaintext)\n"
            "- Section hierarchy and parent/child relationships\n"
            "- Cross-references between sections\n"
            "- Index terms (text, library, grammar, definition, concept)\n"
            "- Named mechanisms (keywords, library functions, concepts)\n"
            "- Grammar production rules (BNF)\n"
            "- Defined terms with normative definitions\n"
            "- Library declarations with specification elements "
            "(Expects, Effects, Ensures, Returns, Throws, Mandates, "
            "Constraints)\n"
            "- Individual numbered paragraphs with normative force "
            "classification\n"
            "- Multi-version support with cross-version diffing\n\n"
            "All section references include eel.is URLs for "
            "human-readable browsing.\n"
            "Version shorthands like 'C++23' and 'C++26' are accepted "
            "as draft parameters."
        )

    return mcp


def resolve_data_dir(data_dir: str | None = None) -> Path:
    """Resolve the data directory from flag, env var, or default."""
    if data_dir is not None:
        return Path(data_dir)
    env = os.environ.get(DATA_DIR_ENV, "").strip()
    if env:
        return Path(env)
    return DEFAULT_DATA_DIR


def build_default_server(
    data_dir: str | None = None,
    default_draft: str | None = None,
    keys_file: str | None = None,
    no_auth: bool = False,
) -> tuple[FastMCP, SqliteStandardBackend]:
    """Construct a server with the default SQLite backend."""
    resolved_dir = resolve_data_dir(data_dir)
    resolved_dir.mkdir(parents=True, exist_ok=True)
    db_path = resolved_dir / "standard.db"

    backend = SqliteStandardBackend(db_path)
    backend.create_schema()

    if default_draft is None:
        default_draft = os.environ.get(DEFAULT_DRAFT_ENV, "").strip() or None

    if keys_file is None:
        keys_file = os.environ.get(KEYS_FILE_ENV, "").strip() or None

    mcp = create_server(
        backend,
        default_draft=default_draft,
        keys_file=keys_file,
        no_auth=no_auth,
    )
    return mcp, backend
