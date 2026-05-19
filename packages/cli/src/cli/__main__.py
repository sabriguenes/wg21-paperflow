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
import cli.dissect as _dissect_mod
import cli.advocatus as _advocatus_mod
import cli.agora as _agora_mod
import cli.status as _status_mod
from cli.logutil import configure_console_logging
from cli.targets import MONTH_RE
from paperstore import WORKSPACE_ENV_VAR, SqliteBackend

_VERB_NAMES = {
    "mailing", "download", "convert", "dissect", "advocatus", "agora", "status",
}

_VERB_HELP = {
    "mailing":   "Scrape all WG21 mailing indexes (2011-current). Idempotent.",
    "download":  "Download source files for TARGET papers.",
    "convert":   "Convert source files to markdown for TARGET papers. Downloads first if needed.",
    "dissect":   "Extract claims and evidence for TARGET papers. Downloads and converts if needed.",
    "advocatus": "Run adversarial examination for TARGET papers. Runs all prior stages if needed.",
    "agora":     "Plan discussion threads for TARGET papers. Runs all prior stages if needed.",
    "status":    "Show processing status for papers.",
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
    "dissect": (
        "Dissect a paper using the multi-step LLM pipeline. "
        "Requires the paper to be indexed and converted first."
    ),
    "advocatus": (
        "Examine a dissected paper through the two-office tribunal: "
        "Advocatus Diaboli drafts charges, Defensor Causae cross-examines them. "
        "Requires the paper to be dissected first."
    ),
    "agora": (
        "Plan a fake r/wg21 Reddit thread for a dissected paper. "
        "Produces a structural blueprint (anchors, calibration, submission, "
        "every reply slot with a brief) as JSON; reply text and characters "
        "are filled by a later generation phase. Requires the paper to be "
        "dissected first."
    ),
    "status": (
        "Show the current pipeline status for papers. Accepts a paper ID, "
        "year, year-month, or no argument to show all incomplete papers."
    ),
}

_VERB_TARGETS_HELP = {
    "mailing":   "Not used (mailing discovers years automatically).",
    "download":  "Year (2026), paper id(s) (P3642R4 ...), or year-month (2026-01).",
    "convert":   "Year (2026), paper id(s) (P3642R4 ...), or year-month (2026-01).",
    "dissect":   "Paper ID (P4003R2) or year-month (2026-01) for batch dissection.",
    "advocatus": "Paper ID (P4003R2) or year-month (2026-01) for batch examination.",
    "agora":     "Paper ID (P4003R2) or year-month (2026-01) for batch planning.",
    "status":    "Paper ID, year, year-month, or omit for all incomplete papers.",
}

_COMMANDS = {
    "mailing":   _mailing_mod,
    "download":  _download_mod,
    "convert":   _convert_mod,
    "dissect":   _dissect_mod,
    "advocatus": _advocatus_mod,
    "agora":     _agora_mod,
    "status":    _status_mod,
}

_VERB_FLAGS: dict[str, set[str]] = {
    "mailing":   set(),
    "download":  {"force", "concurrency"},
    "convert":   {"force", "concurrency", "check_content", "check_content_json"},
    "dissect":   {"debug", "trace", "step", "chunk", "service", "classifier", "provider", "force"},
    "advocatus": {"debug", "trace", "step", "service", "provider", "force"},
    "agora":     {"debug", "trace", "step", "service", "provider", "force"},
    "status":    set(),
}

_FLAG_DEFS: list[dict] = [
    dict(name="force", flags=["-f", "--force"], action="store_true",
         default=False, help="Redo stage even if already complete."),
    dict(name="concurrency", flags=["--concurrency"], type=int,
         default=None, metavar="N", help="Number of parallel workers."),
    dict(name="check_content", flags=["--check-content"], action="store_true",
         default=False,
         help="Compare source text against converted markdown for content coverage."),
    dict(name="check_content_json", flags=["--check-content-json"],
         type=Path, default=None, metavar="PATH",
         help="Write per-paper content-check metrics as JSON to PATH "
              "(implies --check-content)."),
    dict(name="debug", flags=["--debug"], action="store_true",
         default=False,
         help="Write full LLM transcripts per step to paperstore as a single .debug.md file."),
    dict(name="trace", flags=["--trace"], action="store_true",
         default=False,
         help="Write pipeline state trace to .trace.md."),
    dict(name="step", flags=["--step"], type=int,
         default=None, metavar="N",
         help="Stop after step N (implies --trace)."),
    dict(name="chunk", flags=["--chunk"], type=int,
         default=None, metavar="C",
         help="Run only chunk C in parallel steps (dissect only)."),
    dict(name="service", flags=["--service"], action="append",
         default=None, metavar="NAME",
         help="Override service binding. Use NAME to override all slots, or SLOT=NAME for one slot. Repeatable."),
    dict(name="classifier", flags=["--classifier"], action="append",
         default=None, metavar="NAME",
         help="Override classifier slot binding (e.g. dissect Step 1 Tag Sentences). Use NAME to override all slots, or SLOT=NAME (e.g. selector=zeroshot-base) for one slot. Repeatable."),
    dict(name="provider", flags=["--provider"], default=None, metavar="NAME",
         help="Override the active transformer provider (device/dtype/batch). Defaults to PAPERFLOW_TRANSFORMER_PROVIDER, then [transformer_provider_defaults].default in SERVICES.toml, then 'auto' (host-detected)."),
]

_PAPER_ID_RE = re.compile(r"^[PND]\d{3,5}(R\d+)?$", re.IGNORECASE)

_EPILOG = """\
Examples:
  paperflow mailing                scrape mailing index
  paperflow download P3642R4       download one paper
  paperflow convert 2026-01        convert papers from Jan 2026 onward
  paperflow dissect P4003R2        LLM-driven paper dissection
  paperflow dissect P4003R2 --trace --step 3   stop after step 3 with trace
  paperflow dissect P4003R2 --chunk 0          run only chunk 0
  paperflow dissect P4003R2 --service b200-r1  override all service slots
  paperflow dissect P4003R2 --service fast=b200-r1 --service tool=b200-llama
  paperflow dissect P4003R2 --classifier selector=zeroshot-base  swap Step 1 classifier
  paperflow dissect P4003R2 --provider cuda-b200                 lock provider in cloud
  paperflow advocatus P4003R2      examine a dissected paper
  paperflow agora P4003R2          plan a discussion thread
  paperflow status                 show all incomplete papers
  paperflow status P4003R2         show status of one paper
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
    """Return 'paper', 'year', 'month', or raise ValueError."""
    if _PAPER_ID_RE.match(t):
        return "paper"
    if t.isdigit() and len(t) == 4 and int(t) >= 2011:
        return "year"
    if MONTH_RE.match(t):
        return "month"
    raise ValueError(
        f"Unrecognized target {t!r}. "
        "Expected a paper ID (P4003R2), year (2026), or year-month (2026-01)."
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

    for process_verb in ("dissect", "advocatus", "agora"):
        if verb != process_verb:
            continue
        if "year" in kinds:
            print(
                f"paperflow {verb}: accepts a paper ID or year-month, not bare years.",
                file=sys.stderr,
            )
            sys.exit(1)
        if len(targets) != 1:
            print(
                f"paperflow {verb}: accepts exactly one target, got {len(targets)}.",
                file=sys.stderr,
            )
            sys.exit(1)

    if len(kinds) > 1:
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
        argv = ["agora"] + argv

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
        if verb == "mailing":
            nargs = "*"
            metavar = "YEAR_OR_ALL"
        elif verb == "status":
            nargs = "*"
            metavar = "TARGET"
        else:
            nargs = "+"
            metavar = "TARGET"
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
    configure_console_logging(args.verbose)

    if args.command is None:
        parser.print_help()
        return 0

    _validate_targets(args.command, args.targets)
    _validate_flags(args.command, args)

    backend = _backend_for(args.workspace_dir)
    return args._mod.command(args, backend)


if __name__ == "__main__":
    sys.exit(main())
