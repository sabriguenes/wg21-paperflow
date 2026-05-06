#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Entry point for ``uv run preview <PID>``.

Validates the paper id, resolves the paperstore backend from
``$WG21_DATA_DIR`` (or ``--workspace-dir``), starts the markdown
watcher, builds the Flask app, optionally opens the browser, and runs
the dev server. One paper per invocation; relaunch for a different
paper.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import threading
import webbrowser
from pathlib import Path

from paperstore import (
    MissingMetaError,
    PaperstoreError,
    SqliteBackend,
    StorageBackend,
    WORKSPACE_ENV_VAR,
)

from preview.app import create_app
from preview.watcher import MarkdownWatcher

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 5050
_DEFAULT_HOST = "127.0.0.1"
# Small delay so the dev server is listening before we open the tab.
_BROWSER_OPEN_DELAY_SECONDS = 0.5
_PAPER_ID_RE = re.compile(r"^[PND]\d{3,5}(R\d+)?$", re.IGNORECASE)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="preview",
        description=(
            "Side-by-side preview of a WG21 paper: original PDF/HTML on the "
            "left, scrivener-rendered markdown on the right, with hot reload "
            "when paperflow convert rewrites the markdown."
        ),
    )
    parser.add_argument(
        "paper_id",
        metavar="PAPER_ID",
        help="Paper id, e.g. P3642R4 (case-insensitive).",
    )
    parser.add_argument(
        "--workspace-dir",
        dest="workspace_dir",
        metavar="DIR",
        type=Path,
        default=None,
        help=f"Backend root directory (default: ${WORKSPACE_ENV_VAR}).",
    )
    parser.add_argument(
        "--host",
        default=_DEFAULT_HOST,
        help=f"Host to bind (default: {_DEFAULT_HOST}).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_DEFAULT_PORT,
        help=f"Port to bind (default: {_DEFAULT_PORT}).",
    )
    parser.add_argument(
        "--no-browser",
        dest="open_browser",
        action="store_false",
        default=True,
        help="Do not open a browser tab automatically.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity (-v = INFO, -vv = DEBUG).",
    )
    return parser


def _configure_logging(verbosity: int) -> None:
    """Wire stderr logging for the preview namespace only.

    We deliberately avoid ``logging.basicConfig`` so ``-vv`` does not
    crank watchdog/werkzeug/etc. up to DEBUG along with us.
    """
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    log = logging.getLogger("preview")
    log.setLevel(level)
    # Idempotent: re-running main() in tests must not stack handlers.
    if not any(isinstance(h, logging.StreamHandler) for h in log.handlers):
        log.addHandler(handler)
    log.propagate = False


def _backend_for(workspace_dir: Path | None) -> SqliteBackend:
    if workspace_dir is None:
        return SqliteBackend.from_env()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return SqliteBackend(workspace_dir)


def _validate_paper_id(raw: str) -> str:
    if not _PAPER_ID_RE.match(raw):
        raise SystemExit(
            f"preview: {raw!r} is not a valid paper id (expected e.g. P3642R4)."
        )
    return raw.upper()


def _ensure_paper_known(backend: StorageBackend, pid: str) -> None:
    """Fail fast if the paper isn't in the local index.

    This avoids launching a server only for the user to discover that
    the paper id is unknown when the iframes load.
    """
    try:
        backend.get_meta(pid)
    except MissingMetaError as exc:
        raise SystemExit(f"preview: {exc}") from exc


def _open_browser_async(url: str) -> None:
    timer = threading.Timer(
        _BROWSER_OPEN_DELAY_SECONDS,
        lambda: webbrowser.open(url),
    )
    timer.daemon = True
    timer.start()


def main() -> int:
    args = _build_parser().parse_args()
    _configure_logging(args.verbose)

    pid = _validate_paper_id(args.paper_id)

    try:
        backend = _backend_for(args.workspace_dir)
    except (EnvironmentError, PaperstoreError) as exc:
        print(f"preview: {exc}", file=sys.stderr)
        return 1

    _ensure_paper_known(backend, pid)

    md_path = backend.get_paper_md_path(pid)

    with MarkdownWatcher(md_path) as watcher:
        app = create_app(backend, pid, watcher)
        url = f"http://{args.host}:{args.port}/"
        print(f"preview: serving {pid} at {url}", file=sys.stderr)
        if args.open_browser:
            _open_browser_async(url)
        # threaded=True so SSE doesn't block other requests; reloader
        # off so the watchdog observer thread stays alive.
        app.run(
            host=args.host,
            port=args.port,
            threaded=True,
            use_reloader=False,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
