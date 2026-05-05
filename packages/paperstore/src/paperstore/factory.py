#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Factory functions for constructing storage backends."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from paperstore.backend import StorageBackend
from paperstore.sqlite_backend import SqliteBackend

WORKSPACE_ENV_VAR = "WG21_DATA_DIR"


def default_workspace_dir() -> Path:
    """Resolve the workspace path from ``$WG21_DATA_DIR``.

    Raises :class:`EnvironmentError` when the variable is unset or empty.
    """
    env = os.environ.get(WORKSPACE_ENV_VAR, "").strip()
    if not env:
        raise EnvironmentError(
            f"{WORKSPACE_ENV_VAR} is not set. "
            "Set it to the directory where paperflow stores its data.\n"
            f"  export {WORKSPACE_ENV_VAR}=/path/to/wg21-data"
        )
    return Path(env)


def from_uri(
    uri: str | None = None, *, workspace_dir: Path | str | None = None
) -> StorageBackend:
    """Construct a storage backend from a URI.

    - ``None`` or ``"file://<path>"`` returns a :class:`SqliteBackend`.
    - Any other scheme is reserved for future backends (e.g. ``postgres://``).
    """
    if uri is None or uri == "":
        if workspace_dir is None:
            raise ValueError(
                "paperstore.from_uri requires workspace_dir when uri is None or empty "
                f"(got uri={uri!r}, workspace_dir=None)."
            )
        return SqliteBackend(workspace_dir)
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            raise ValueError(
                "paperstore.from_uri: file:// URIs must have an empty or "
                f"'localhost' authority (uri={uri!r})."
            )
        # url2pathname handles platform-specific decoding: on Windows,
        # "/C:/Users/foo" becomes "C:\\Users\\foo"; on POSIX it is identity
        # for unencoded paths.
        path: Path | str | None = url2pathname(parsed.path) if parsed.path else workspace_dir
        if not path:
            raise ValueError(
                f"paperstore.from_uri: file:// URI has no path and no workspace_dir "
                f"fallback (uri={uri!r})."
            )
        return SqliteBackend(path)
    raise ValueError(
        f"paperstore.from_uri: unsupported URI scheme (uri={uri!r}); "
        "only None, '', and file:// are recognized."
    )
