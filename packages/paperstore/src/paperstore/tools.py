#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Agent-facing tools for browsing the paperstore.

Methods return JSON strings and are designed for registration via
``agent.tool_plain(...)`` in Pydantic AI pipelines.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from paperstore.backend import StorageBackend
from paperstore.errors import MissingMetaError


class PaperstoreTools:
    """Read-only paperstore tools for LLM agents.

    Wrap a :class:`~paperstore.backend.StorageBackend` and expose methods
    suitable for ``agent.tool_plain(...)`` registration::

        tools = PaperstoreTools(backend)
        agent.tool_plain(tools.paper_meta)
        agent.tool_plain(tools.paper_meta_latest)
        agent.tool_plain(tools.read_file)
    """

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    async def paper_meta(self, paper_id: str) -> str:
        """Get metadata for a WG21 paper by exact ID (e.g. 'P4014R0').

        Returns JSON with paper_id, title, authors, year, target_group,
        url, line_count, and markdown_path. Returns an error object if
        the paper is not in the store.
        """
        try:
            row = self._backend.get_meta(paper_id)
        except MissingMetaError:
            return json.dumps(
                {"error": f"Paper '{paper_id.strip().upper()}' not found in paperstore."}
            )
        return json.dumps(row, ensure_ascii=False)

    async def paper_meta_latest(self, paper_number: str) -> str:
        """Get metadata for the latest revision of a paper number (e.g. 'P4014').

        Accepts formats like 'P4014', 'p4014', '4014', or 'P4014R2' (the
        revision suffix is stripped). Finds the highest revision available
        and returns the same fields as paper_meta.
        """
        cleaned = paper_number.strip().upper()
        m = re.match(r"^P?(\d+)(R\d+)?$", cleaned)
        if not m:
            return json.dumps(
                {"error": f"Cannot parse paper number from '{paper_number}'."}
            )
        number = m.group(1)
        pattern = re.compile(rf"^P{number}R(\d+)$")

        all_ids = self._backend.list_all_paper_ids()
        matches: list[tuple[int, str]] = []
        for pid in all_ids:
            pm = pattern.match(pid)
            if pm:
                matches.append((int(pm.group(1)), pid))

        if not matches:
            return json.dumps(
                {"error": f"No revisions found for paper number '{number}'."}
            )

        matches.sort(reverse=True)
        best_id = matches[0][1]
        return await self.paper_meta(best_id)

    async def read_file(self, path: str, offset: int = 1, limit: int = 200) -> str:
        """Read lines from a file in the paperstore workspace.

        Returns JSON with path, total_lines, start_line, end_line, and
        content. Only files under the paperstore workspace directory can
        be read. offset is 1-based; limit is the max number of lines.
        """
        workspace = self._backend.workspace_dir
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError) as exc:
            return json.dumps({"error": f"Invalid path: {exc}"})

        workspace_resolved = Path(workspace).resolve()
        if not str(resolved).startswith(str(workspace_resolved)):
            return json.dumps(
                {"error": "Access denied: path is outside the paperstore workspace."}
            )

        if not resolved.is_file():
            return json.dumps({"error": f"File not found: {path}"})

        text = resolved.read_text(encoding="utf-8")
        lines = text.splitlines()
        total_lines = len(lines)

        start = max(0, offset - 1)
        end = min(total_lines, start + limit)
        selected = lines[start:end]

        return json.dumps(
            {
                "path": str(resolved),
                "total_lines": total_lines,
                "start_line": start + 1,
                "end_line": end,
                "content": "\n".join(selected),
            },
            ensure_ascii=False,
        )
