#
# Copyright (c) 2026 Dmitriy Chukhin (dmitriy@lincolnloop.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Typed manifest for the mailing -> tomd HTML image handoff.

The mailing package fetches images referenced by HTML paper sources and
writes a sidecar JSON file describing what landed where. The tomd HTML
extractor reads that sidecar to assemble :class:`ExtractedImage` records
without doing any network I/O.

Stdlib only, no Pydantic. Matches the existing ``extract_rows`` pattern
for cross-package row types and keeps paperstore's zero-dependency
invariant intact.

Forward-compatibility rules for ``version``:

- ``version <= _MAX_FORWARD_COMPATIBLE_VERSION``: parsed by this reader.
  Extra unknown fields on entries or the envelope are silently ignored,
  so writers can add optional fields and bump ``version`` to a value in
  ``(_SUPPORTED_HTML_MANIFEST_VERSION, _MAX_FORWARD_COMPATIBLE_VERSION]``
  without breaking older readers.
- ``version >  _MAX_FORWARD_COMPATIBLE_VERSION``: explicit
  :class:`HtmlManifestError` with a "regenerate by re-running
  `paperflow mailing`" hint. The writer signals a breaking change by
  bumping past the ceiling; old readers must refuse rather than
  silently mis-parse.

``_SUPPORTED_HTML_MANIFEST_VERSION`` is the version this code produces.
``_MAX_FORWARD_COMPATIBLE_VERSION`` is the ceiling on what this reader
will accept from a newer writer. Bump the latter when the schema gains
additive-only fields; bump it AND introduce a new code path when the
schema gains a breaking change.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

_SUPPORTED_HTML_MANIFEST_VERSION = 1
_MAX_FORWARD_COMPATIBLE_VERSION = 9


class HtmlManifestError(Exception):
    """Raised when an HTML images manifest is malformed or too new to read."""


@dataclass(frozen=True)
class HtmlImageEntry:
    """One image reference resolved from an HTML paper source.

    ``stored_filename`` is the on-disk basename produced by
    :meth:`StorageBackend.write_paper_image` (e.g. ``p3556r0-fig0-1.png``).
    HTML papers use ``page=0`` as the "no page concept" sentinel; the
    ``document_order`` field (1-based) is the position the image
    appeared at in the parsed HTML and drives the emit position in
    :mod:`tomd`.
    """

    original_src: str
    stored_filename: str
    document_order: int
    caption_text: str = ""
    alt_attr: str = ""


@dataclass(frozen=True)
class HtmlImagesManifest:
    """Envelope written by mailing, read by tomd.

    Persisted as ``<pid>.html-images.json`` alongside the paper source.
    """

    pid: str
    entries: list[HtmlImageEntry] = field(default_factory=list)
    version: int = 1

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialize as the canonical on-disk JSON shape."""
        payload = {
            "version": self.version,
            "pid": self.pid,
            "entries": [asdict(e) for e in self.entries],
        }
        return json.dumps(payload, indent=indent, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | bytes) -> "HtmlImagesManifest":
        """Parse a manifest from JSON text. See module docstring for rules."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HtmlManifestError(f"manifest is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise HtmlManifestError(
                f"manifest root must be a JSON object, got {type(data).__name__}"
            )
        version = data.get("version", 1)
        if not isinstance(version, int):
            raise HtmlManifestError(
                f"manifest 'version' must be an integer, got {type(version).__name__}"
            )
        if version > _MAX_FORWARD_COMPATIBLE_VERSION:
            raise HtmlManifestError(
                f"HTML images manifest version {version} is newer than this "
                f"paperstore supports (max v{_MAX_FORWARD_COMPATIBLE_VERSION}). "
                f"Regenerate by re-running `paperflow mailing`."
            )
        pid = data.get("pid", "")
        if not isinstance(pid, str):
            raise HtmlManifestError(
                f"manifest 'pid' must be a string, got {type(pid).__name__}"
            )
        raw_entries = data.get("entries", [])
        if not isinstance(raw_entries, list):
            raise HtmlManifestError(
                f"manifest 'entries' must be a list, got {type(raw_entries).__name__}"
            )
        entries: list[HtmlImageEntry] = []
        for i, raw_entry in enumerate(raw_entries):
            if not isinstance(raw_entry, dict):
                raise HtmlManifestError(
                    f"manifest entry {i} must be an object, "
                    f"got {type(raw_entry).__name__}"
                )
            entries.append(
                HtmlImageEntry(
                    original_src=str(raw_entry.get("original_src", "")),
                    stored_filename=str(raw_entry.get("stored_filename", "")),
                    document_order=int(raw_entry.get("document_order", 0)),
                    caption_text=str(raw_entry.get("caption_text", "")),
                    alt_attr=str(raw_entry.get("alt_attr", "")),
                )
            )
        return cls(version=version, pid=pid, entries=entries)
