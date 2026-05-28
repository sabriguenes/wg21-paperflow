#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""CLI entry point for cpp-mcp: ``python -m cpp_mcp`` or ``cpp-mcp``."""

from __future__ import annotations

import argparse
import logging
import sys

log = logging.getLogger("cpp_mcp")


def _cmd_ingest(args: argparse.Namespace) -> int:
    from cpp_mcp.ingest import ingest_from_git
    from cpp_mcp.server import resolve_data_dir
    from cpp_mcp.sqlite_backend import SqliteStandardBackend

    data_dir = resolve_data_dir(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "standard.db"

    with SqliteStandardBackend(db_path) as backend:
        backend.create_schema()
        count = ingest_from_git(backend, args.tag)
    print(f"Ingested {count} sections for draft '{args.tag}' into {db_path}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from cpp_mcp.server import build_default_server

    mcp, backend = build_default_server(
        data_dir=args.data_dir,
        default_draft=args.default_draft,
        keys_file=args.keys_file,
    )
    try:
        if args.transport == "stdio":
            log.info("Starting MCP server (stdio)")
            mcp.run(transport="stdio")
        else:
            log.info("Starting MCP server on http://%s:%d", args.host, args.port)
            mcp.run(transport="http", host=args.host, port=args.port)
    finally:
        backend.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cpp-mcp",
        description="C++ Standard MCP Server: search and browse the ISO C++ standard.",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Data directory for the SQLite database (default: ~/.cpp-mcp or $CPP_MCP_DATA_DIR).",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )

    sub = parser.add_subparsers(dest="command")

    ingest_p = sub.add_parser("ingest", help="Ingest the C++ standard from cplusplus/draft.")
    ingest_p.add_argument(
        "--tag", required=True,
        help="Git tag or branch to ingest (e.g. n5008, n4950, main).",
    )

    serve_p = sub.add_parser("serve", help="Start the MCP server.")
    serve_p.add_argument(
        "--port", type=int, default=8001,
        help="Port for the HTTP server (default: 8001).",
    )
    serve_p.add_argument(
        "--host", default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1).",
    )
    serve_p.add_argument(
        "--transport", choices=["http", "stdio"], default="http",
        help="Transport mode (default: http).",
    )
    serve_p.add_argument(
        "--default-draft", default=None,
        help="Default draft tag for queries that don't specify one.",
    )
    serve_p.add_argument(
        "--keys-file", default=None,
        help="Path to API keys file for bearer token auth.",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.command == "ingest":
        return _cmd_ingest(args)
    elif args.command == "serve":
        return _cmd_serve(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
