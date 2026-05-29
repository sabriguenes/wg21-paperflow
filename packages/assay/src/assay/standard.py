#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Async client for the C++ standard MCP server (cpp-mcp).

Wraps the FastMCP client to provide typed data access and pydantic-ai
tool callables for use in assay pipeline steps.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

log = logging.getLogger(__name__)

DEFAULT_CPP_MCP_URL = "https://mcpserver1.cpp.al/mcp"
CPP_MCP_URL_ENV = "CPP_MCP_URL"
CPP_MCP_API_KEY_ENV = "CPP_MCP_API_KEY"

_STABLE_LABEL_RE = re.compile(r"\[([a-z][a-z0-9.]+)\]")
_PARAGRAPH_REF_RE = re.compile(r"\[([a-z][a-z0-9.]+)\]\s*(?:paragraph|p)\s*(\d+)", re.IGNORECASE)
_BACKTICK_NAME_RE = re.compile(r"`([a-zA-Z_][a-zA-Z0-9_:.<>]*)`")


class StandardClient:
    """Async client for the C++ standard MCP server.

    Use as an async context manager or call ``connect()`` / ``close()``
    manually. All data methods return parsed Python objects. All tool
    methods (``*_tool``) return ``str`` for pydantic-ai ``tool_plain``.
    """

    def __init__(self, url: str, api_key: str) -> None:
        self._url = url
        self._transport = StreamableHttpTransport(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self._client: Client | None = None

    async def connect(self) -> None:
        """Open the MCP session and validate authentication."""
        self._client = Client(transport=self._transport)
        await self._client.__aenter__()
        try:
            await self.list_drafts()
        except Exception as exc:
            await self.close()
            raise ValueError(
                f"cpp-mcp connection failed at {self._url}: {exc}\n"
                f"Check CPP_MCP_API_KEY or pass --no-cpp-mcp."
            ) from exc
        log.info("Connected to C++ standard server at %s", self._url)

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception as exc:
                log.debug("Ignoring error during MCP client close: %s", exc)
            self._client = None

    async def __aenter__(self) -> StandardClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _call(self, tool: str, arguments: dict[str, Any]) -> Any:
        """Call an MCP tool and return the parsed JSON result."""
        if self._client is None:
            raise RuntimeError("StandardClient is not connected")
        result = await self._client.call_tool(tool, arguments)
        if not result.content or not hasattr(result.content[0], "text"):
            raise ValueError(f"MCP tool '{tool}' returned no text content")
        return json.loads(result.content[0].text)

    # ------------------------------------------------------------------
    # Data methods (return parsed Python objects)
    # ------------------------------------------------------------------

    async def list_drafts(self) -> list[dict]:
        return await self._call("list_drafts", {})

    async def verify_mechanism(self, name: str, draft: str | None = None) -> dict:
        args: dict[str, Any] = {"name": name}
        if draft:
            args["draft"] = draft
        return await self._call("verify_mechanism", args)

    async def lookup_section(self, stable_label: str, draft: str | None = None) -> dict | None:
        args: dict[str, Any] = {"stable_label": stable_label}
        if draft:
            args["draft"] = draft
        result = await self._call("lookup_section", args)
        return None if "error" in result else result

    async def lookup_sections(self, labels: list[str], draft: str | None = None) -> list[dict]:
        args: dict[str, Any] = {"stable_labels": labels}
        if draft:
            args["draft"] = draft
        return await self._call("lookup_sections", args)

    async def lookup_paragraph(
        self, stable_label: str, paragraph: int, draft: str | None = None
    ) -> dict | None:
        args: dict[str, Any] = {"stable_label": stable_label, "paragraph": paragraph}
        if draft:
            args["draft"] = draft
        result = await self._call("lookup_paragraph", args)
        return None if "error" in result else result

    async def lookup_definition(self, term: str, draft: str | None = None) -> list[dict]:
        args: dict[str, Any] = {"term": term}
        if draft:
            args["draft"] = draft
        result = await self._call("lookup_definition", args)
        if isinstance(result, dict) and "error" in result:
            return []
        return result

    async def lookup_declaration(self, pattern: str, draft: str | None = None) -> list[dict]:
        args: dict[str, Any] = {"pattern": pattern}
        if draft:
            args["draft"] = draft
        return await self._call("lookup_declaration", args)

    async def search_standard(self, query: str, top_k: int = 5, draft: str | None = None) -> list[dict]:
        args: dict[str, Any] = {"query": query, "top_k": top_k}
        if draft:
            args["draft"] = draft
        return await self._call("search_standard", args)

    async def search_index(self, term: str, category: str | None = None, draft: str | None = None) -> list[dict]:
        args: dict[str, Any] = {"term": term}
        if category:
            args["category"] = category
        if draft:
            args["draft"] = draft
        return await self._call("search_index", args)

    async def search_grammar(self, nonterminal: str, draft: str | None = None) -> dict | None:
        args: dict[str, Any] = {"nonterminal": nonterminal}
        if draft:
            args["draft"] = draft
        result = await self._call("search_grammar", args)
        return None if "error" in result else result

    async def get_cross_references(
        self, stable_label: str, direction: str = "both", draft: str | None = None
    ) -> dict:
        args: dict[str, Any] = {"stable_label": stable_label, "direction": direction}
        if draft:
            args["draft"] = draft
        return await self._call("get_cross_references", args)

    async def get_ancestors(self, stable_label: str, draft: str | None = None) -> list[dict]:
        args: dict[str, Any] = {"stable_label": stable_label}
        if draft:
            args["draft"] = draft
        return await self._call("get_ancestors", args)

    async def guide_query(self, question: str) -> dict:
        return await self._call("guide_query", {"question": question})

    # ------------------------------------------------------------------
    # Tool callables for pydantic-ai tool_plain registration
    # Each returns str (JSON), takes simple arguments.
    # ------------------------------------------------------------------

    async def verify_mechanism_tool(self, name: str) -> str:
        """Check if a C++ mechanism (type, function, keyword, concept) exists in the standard."""
        return json.dumps(await self.verify_mechanism(name))

    async def lookup_section_tool(self, stable_label: str) -> str:
        """Look up a C++ standard section by stable label (e.g. 'basic.life')."""
        result = await self.lookup_section(stable_label)
        if result is None:
            return json.dumps({"error": f"Section [{stable_label}] not found."})
        return json.dumps(result)

    async def lookup_declaration_tool(self, pattern: str) -> str:
        """Get library API specifications matching a declaration pattern."""
        return json.dumps(await self.lookup_declaration(pattern))

    async def lookup_definition_tool(self, term: str) -> str:
        """Get the standard's normative definition of a term."""
        result = await self.lookup_definition(term)
        if not result:
            return json.dumps({"error": f"Term '{term}' not found."})
        return json.dumps(result)

    async def search_standard_tool(self, query: str) -> str:
        """Search the C++ standard for sections matching a query."""
        return json.dumps(await self.search_standard(query))

    async def search_index_tool(self, term: str) -> str:
        """Find which standard sections are indexed under a term."""
        return json.dumps(await self.search_index(term))

    async def search_grammar_tool(self, nonterminal: str) -> str:
        """Find a grammar production rule by nonterminal name."""
        result = await self.search_grammar(nonterminal)
        if result is None:
            return json.dumps({"error": f"Grammar rule '{nonterminal}' not found."})
        return json.dumps(result)

    async def get_cross_references_tool(self, stable_label: str) -> str:
        """Get cross-references from/to a standard section."""
        return json.dumps(await self.get_cross_references(stable_label))

    async def get_ancestors_tool(self, stable_label: str) -> str:
        """Get the parent chain from a section to the chapter root."""
        return json.dumps(await self.get_ancestors(stable_label))

    async def guide_query_tool(self, question: str) -> str:
        """Describe what you need and get a recommended tool and parameters."""
        return json.dumps(await self.guide_query(question))

    # ------------------------------------------------------------------
    # Extraction helpers for pre-fetch
    # ------------------------------------------------------------------

    @staticmethod
    def extract_stable_labels(text: str) -> list[str]:
        """Extract [stable.label] patterns from text."""
        return list(dict.fromkeys(_STABLE_LABEL_RE.findall(text)))

    @staticmethod
    def extract_paragraph_refs(text: str) -> list[tuple[str, int]]:
        """Extract '[label] paragraph N' patterns from text."""
        return [(m.group(1), int(m.group(2))) for m in _PARAGRAPH_REF_RE.finditer(text)]

    @staticmethod
    def extract_mechanism_names(text: str) -> list[str]:
        """Extract backtick-quoted mechanism names from text."""
        return list(dict.fromkeys(_BACKTICK_NAME_RE.findall(text)))

    async def prefetch_standard_context(self, text: str) -> str:
        """Extract stable labels and mechanisms from text, fetch from server.

        Returns a formatted block suitable for injection into an LLM prompt,
        or empty string if nothing was found.
        """
        parts: list[str] = []

        labels = self.extract_stable_labels(text)
        if labels:
            try:
                sections = await self.lookup_sections(labels[:10])
            except Exception as exc:
                log.debug("lookup_sections failed: %s", exc)
                sections = []
            if sections:
                parts.append("### Referenced standard sections\n")
                for s in sections:
                    cleaned = s.get("cleaned_text", "")
                    preview = cleaned[:300] + "..." if len(cleaned) > 300 else cleaned
                    parts.append(
                        f"**[{s['stable_label']}]** {s.get('title', '')}\n"
                        f"{preview}\n"
                    )

        para_refs = self.extract_paragraph_refs(text)
        for label, num in para_refs[:5]:
            try:
                para = await self.lookup_paragraph(label, num)
            except Exception as exc:
                log.debug("lookup_paragraph(%s, %d) failed: %s", label, num, exc)
                continue
            if para:
                parts.append(
                    f"**[{label}] paragraph {num}** "
                    f"(normative force: {para.get('normative_force', '?')})\n"
                    f"{para.get('cleaned_text', '')[:300]}\n"
                )

        if not parts:
            return ""

        return "## Standard context (verified)\n\n" + "\n".join(parts)

    async def prefetch_mechanism_verification(self, text: str) -> str:
        """Extract mechanism names from text and verify each against the standard.

        Returns a formatted verification block for prompt injection.
        Individual verification failures are logged and skipped.
        """
        import asyncio as _asyncio

        names = self.extract_mechanism_names(text)
        if not names:
            return ""

        names = names[:20]

        async def _verify_one(name: str) -> str:
            try:
                result = await self.verify_mechanism(name)
            except Exception as exc:
                log.debug("verify_mechanism(%s) failed: %s", name, exc)
                return f"- `{name}`: LOOKUP FAILED"
            if result.get("exists"):
                matches = result.get("matches", [])
                cats = ", ".join(sorted({m["category"] for m in matches[:3]}))
                labels = ", ".join(f"[{m['stable_label']}]" for m in matches[:3])
                return f"- `{name}`: EXISTS ({cats}) in {labels}"
            return f"- `{name}`: NOT FOUND in the standard"

        results = await _asyncio.gather(*[_verify_one(n) for n in names])
        parts = ["## Standard verification (pre-checked)\n"] + list(results)
        return "\n".join(parts) + "\n"


def from_env(
    *,
    url: str | None = None,
    no_cpp_mcp: bool = False,
) -> StandardClient | None:
    """Build a StandardClient from env vars and defaults.

    Resolution order for URL: explicit *url* arg > $CPP_MCP_URL > default.
    API key: $CPP_MCP_API_KEY (required unless *no_cpp_mcp*).
    """
    if no_cpp_mcp:
        return None

    resolved_url = url or os.environ.get(CPP_MCP_URL_ENV, "").strip() or DEFAULT_CPP_MCP_URL
    key = os.environ.get(CPP_MCP_API_KEY_ENV, "").strip()
    if not key:
        raise ValueError(
            f"CPP_MCP_API_KEY is not set. The C++ standard server at "
            f"{resolved_url} requires authentication.\n"
            f"  Set ${CPP_MCP_API_KEY_ENV} in your environment, or\n"
            f"  pass --no-cpp-mcp to run without the C++ standard MCP server."
        )
    return StandardClient(resolved_url, key)
