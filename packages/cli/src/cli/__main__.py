#
# Copyright (c) 2026 Sergio DuBois (sentientsergio@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/paperlint
#

"""Paperflow CLI - WG21 paper ingestion and conversion.

Commands:

    paperflow mailing 2026             # scrape index (no downloads)
    paperflow mailing all              # scrape all years >= 2011
    paperflow download 2026            # download source files
    paperflow download P3642R4         # download a specific paper
    paperflow convert 2026             # convert to markdown
    paperflow convert P3642R4          # convert a specific paper
    paperflow full 2026                # all three stages
    paperflow 2026                     # same as 'full 2026' (no-verb alias)
    paperflow full all                 # full pipeline for everything

Flags:
    --force / -f               Redo stage even if already complete
    --verify                   HEAD-check sources against Content-Length (download/full)
    --concurrency N            Parallel workers (default varies by command)
    --workspace-dir DIR        Backend root (default: $WG21_DATA_DIR)
    -v / -vv                   Increase log verbosity
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cli.mailing as _mailing_cmd
import cli.download as _download_cmd
import cli.convert as _convert_cmd
import cli.full as _full_cmd
from cli.logutil import configure_paperlint_console_logging
from paperstore import WORKSPACE_ENV_VAR, SqliteBackend

_SUBCOMMAND_NAMES = {"mailing", "download", "convert", "full"}

_EPILOG = """
Examples:
  paperflow 2026                   full pipeline for 2026
  paperflow mailing 2026           scrape index only
  paperflow download P3642R4       download one paper
  paperflow convert all            convert all staged-but-not-converted
  paperflow full all               full pipeline for all pending work
"""


def _backend_for(workspace_dir: Path | None) -> SqliteBackend:
    if workspace_dir is None:
        return SqliteBackend.from_env()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return SqliteBackend(workspace_dir)


def main() -> int:
    # No-verb fallback: if first arg is not a subcommand, prepend "full".
    argv = sys.argv[1:]
    if argv and argv[0] not in _SUBCOMMAND_NAMES and not argv[0].startswith("-"):
        argv = ["full"] + argv

    parser = argparse.ArgumentParser(
        prog="paperflow",
        description="WG21 paper ingestion and conversion.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--workspace-dir",
        "--output-dir",
        dest="workspace_dir",
        metavar="DIR",
        default=None,
        type=Path,
        help=f"Backend root directory (default: ${WORKSPACE_ENV_VAR}).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity (-v = INFO, -vv = DEBUG).",
    )

    subparsers = parser.add_subparsers(dest="command")

    for mod in (_mailing_cmd, _download_cmd, _convert_cmd, _full_cmd):
        p = mod.add_parser(subparsers)
        p.set_defaults(_mod=mod, _parser=p)

    if not argv:
        parser.print_help()
        return 0

    args = parser.parse_args(argv)
    configure_paperlint_console_logging(args.verbose)

    if args.command is None:
        parser.print_help()
        return 0

    backend = _backend_for(args.workspace_dir)
    return args._mod.command(args, backend)


if __name__ == "__main__":
    sys.exit(main())
