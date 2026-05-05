#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Paperflow CLI - unified entry point.

All subparser definitions, target classification, flag validation,
and dispatch live in this file. Command modules keep only their
``command(args, backend)`` functions.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import cli.mailing as _mailing_mod
import cli.download as _download_mod
import cli.convert as _convert_mod
import cli.full as _full_mod
import cli.review as _review_mod
from cli.logutil import configure_paperlint_console_logging
from paperstore import WORKSPACE_ENV_VAR, SqliteBackend

_VERB_NAMES = {"mailing", "download", "convert", "full", "review"}

_VERB_HELP = {
    "mailing":  "Scrape mailing indexes from open-std.org (no downloads)",
    "download": "Download paper source files (PDF/HTML)",
    "convert":  "Convert staged source files to markdown (no LLM)",
    "full":     "Run all three stages: mailing + download + convert",
    "review":   "Run an LLM-driven review of a WG21 paper",
}

_VERB_DESCRIPTION = {
    "mailing": (
        "Scrape WG21 mailing indexes from open-std.org and persist locally. "
        "Does not download any paper source files. Idempotent for past years."
    ),
    "download": (
        "Download source files for papers. Reads URLs from the local index. "
        "Idempotent: skips papers already staged unless --force is given."
    ),
    "convert": (
        "Convert staged PDF/HTML sources to markdown using tomd. "
        "Hard-fails if the source is not yet staged."
    ),
    "full": (
        "Full pipeline: scrape mailing index, download sources, and convert to "
        "markdown. Each stage is idempotent; already-done work is skipped."
    ),
    "review": (
        "Review a paper using the multi-step LLM pipeline. "
        "Requires the paper to be indexed and converted first."
    ),
}

_VERB_TARGETS_HELP = {
    "mailing":  'Year(s) to scrape (e.g. 2026 2025), or "all".',
    "download": 'Year (2026), paper id(s) (P3642R4 ...), or "all".',
    "convert":  'Year (2026), paper id(s) (P3642R4 ...), or "all".',
    "full":     'Year (2026), paper id(s) (P3642R4 ...), or "all".',
    "review":   "Paper ID to review (e.g. P4003R2, p4003r2).",
}

_COMMANDS = {
    "mailing":  _mailing_mod,
    "download": _download_mod,
    "convert":  _convert_mod,
    "full":     _full_mod,
    "review":   _review_mod,
}

_VERB_FLAGS: dict[str, set[str]] = {
    "mailing":  {"force"},
    "download": {"force", "verify", "concurrency"},
    "convert":  {"force", "concurrency", "no_prompts", "qa", "qa_json",
                 "workers", "timeout"},
    "full":     {"force", "verify", "concurrency"},
    "review":   {"stop_after", "dump_steps"},
}

_FLAG_DEFS: list[dict] = [
    dict(name="force", flags=["-f", "--force"], action="store_true",
         default=False, help="Redo stage even if already complete."),
    dict(name="verify", flags=["--verify"], action="store_true",
         default=False,
         help="HEAD-check staged files against Content-Length; re-download on mismatch."),
    dict(name="concurrency", flags=["--concurrency"], type=int,
         default=None, metavar="N", help="Number of parallel workers."),
    dict(name="no_prompts", flags=["--no-prompts"], action="store_true",
         default=False, help="Skip writing the .prompts.json intermediate."),
    dict(name="qa", flags=["--qa"], action="store_true", default=False,
         help="Score existing markdown quality instead of converting."),
    dict(name="qa_json", flags=["--qa-json"], type=Path, default=None,
         metavar="PATH",
         help="Write per-paper QA metrics as JSON to PATH (implies --qa)."),
    dict(name="workers", flags=["--workers"], type=int, default=None,
         metavar="N", help="QA parallelism."),
    dict(name="timeout", flags=["--timeout"], type=int, default=None,
         metavar="SEC", help="QA straggler timeout in seconds."),
    dict(name="stop_after", flags=["--stop-after"], type=int, default=None,
         metavar="N",
         help="Run steps 0..N then dump the output and exit (debugging)."),
    dict(name="dump_steps", flags=["--dump-steps"], action="store_true",
         default=False,
         help="Print each step's structured output as JSON after completion."),
]

_PAPER_ID_RE = re.compile(r"^[PND]\d{3,5}(R\d+)?$", re.IGNORECASE)

_EPILOG = """\
Examples:
  paperflow 2026                   full pipeline for 2026
  paperflow mailing 2026           scrape index only
  paperflow download P3642R4       download one paper
  paperflow convert all            convert all staged-but-not-converted
  paperflow full all               full pipeline for all pending work
  paperflow review P4003R2         LLM-driven paper review
"""


def _add_flags(p: argparse.ArgumentParser, verb: str) -> None:
    """Add all flags to a subparser. Non-allowed flags are SUPPRESS'd."""
    allowed = _VERB_FLAGS[verb]
    for fd in _FLAG_DEFS:
        kw = {k: v for k, v in fd.items() if k not in ("name", "flags")}
        if fd["name"] not in allowed:
            kw["help"] = argparse.SUPPRESS
        p.add_argument(*fd["flags"], dest=fd["name"], **kw)


def _classify_target(t: str) -> str:
    """Return 'paper', 'year', 'all', or raise ValueError."""
    if t.lower() == "all":
        return "all"
    if _PAPER_ID_RE.match(t):
        return "paper"
    if t.isdigit() and len(t) == 4 and int(t) >= 2011:
        return "year"
    raise ValueError(
        f"Unrecognized target {t!r}. "
        "Expected a paper ID (P4003R2), year (2026), or 'all'."
    )


def _validate_targets(verb: str, targets: list[str]) -> None:
    """Check targets against verb-specific rules."""
    if not targets:
        return

    kinds = set()
    for t in targets:
        try:
            kinds.add(_classify_target(t))
        except ValueError as exc:
            print(f"paperflow {verb}: {exc}", file=sys.stderr)
            sys.exit(1)

    if verb == "mailing":
        bad = kinds - {"year", "all"}
        if bad:
            print(
                f"paperflow mailing: accepts years or 'all', "
                f"not paper IDs ({', '.join(t for t in targets if _classify_target(t) == 'paper')})",
                file=sys.stderr,
            )
            sys.exit(1)

    if verb == "review":
        if "year" in kinds or "all" in kinds:
            print(
                "paperflow review: accepts exactly one paper ID, not years or 'all'.",
                file=sys.stderr,
            )
            sys.exit(1)
        if len(targets) != 1:
            print(
                f"paperflow review: accepts exactly one paper ID, got {len(targets)}.",
                file=sys.stderr,
            )
            sys.exit(1)

    if len(kinds) > 1 and "all" not in kinds:
        if "paper" in kinds and "year" in kinds:
            print(
                f"paperflow {verb}: cannot mix paper IDs and years in the same invocation.",
                file=sys.stderr,
            )
            sys.exit(1)


def _validate_flags(verb: str, args: argparse.Namespace) -> None:
    """Check that only flags in the verb's allowlist were set."""
    allowed = _VERB_FLAGS[verb]
    for fd in _FLAG_DEFS:
        name = fd["name"]
        if name in allowed:
            continue
        value = getattr(args, name, fd["default"])
        if value != fd["default"]:
            flag_str = fd["flags"][-1]
            print(
                f"paperflow {verb}: {flag_str} is not valid for '{verb}'.",
                file=sys.stderr,
            )
            sys.exit(1)


def _backend_for(workspace_dir: Path | None) -> SqliteBackend:
    if workspace_dir is None:
        return SqliteBackend.from_env()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return SqliteBackend(workspace_dir)


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] not in _VERB_NAMES and not argv[0].startswith("-"):
        argv = ["full"] + argv

    parser = argparse.ArgumentParser(
        prog="paperflow",
        description="WG21 paper ingestion and conversion.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--workspace-dir", "--output-dir",
        dest="workspace_dir", metavar="DIR", default=None, type=Path,
        help=f"Backend root directory (default: ${WORKSPACE_ENV_VAR}).",
    )
    parser.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="Increase log verbosity (-v = INFO, -vv = DEBUG).",
    )

    subparsers = parser.add_subparsers(dest="command")

    for verb, mod in _COMMANDS.items():
        nargs = "*" if verb == "mailing" else "+"
        metavar = "YEAR_OR_ALL" if verb == "mailing" else (
            "PAPER_ID" if verb == "review" else "TARGET"
        )
        p = subparsers.add_parser(
            verb,
            help=_VERB_HELP[verb],
            description=_VERB_DESCRIPTION[verb],
        )
        p.add_argument("targets", nargs=nargs, metavar=metavar,
                        help=_VERB_TARGETS_HELP[verb])
        _add_flags(p, verb)
        p.set_defaults(_mod=mod, _parser=p)

    if not argv:
        parser.print_help()
        return 0

    args = parser.parse_args(argv)
    configure_paperlint_console_logging(args.verbose)

    if args.command is None:
        parser.print_help()
        return 0

    _validate_targets(args.command, args.targets)
    _validate_flags(args.command, args)

    backend = _backend_for(args.workspace_dir)
    return args._mod.command(args, backend)


if __name__ == "__main__":
    sys.exit(main())
