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
import cli.advocatus as _advocatus_mod
import cli.agora as _agora_mod
import cli.assay as _assay_mod
import cli.status as _status_mod
from cli.logutil import configure_console_logging
from cli.targets import MONTH_RE
from paperstore import WORKSPACE_ENV_VAR, SqliteBackend

_VERB_NAMES = {
    "mailing", "download", "convert", "advocatus", "agora", "assay", "status",
}

_VERB_HELP = {
    "mailing":   "Scrape all WG21 mailing indexes (2011-current). Idempotent.",
    "download":  "Download source files for TARGET papers.",
    "convert":   "Convert source files to markdown for TARGET papers. Downloads first if needed.",
    "advocatus": "Run adversarial examination for TARGET papers. Runs all prior stages if needed.",
    "agora":     "Plan discussion threads for TARGET papers. Runs all prior stages if needed.",
    "assay":     "Structural analysis of TARGET papers (two-pass, six lenses).",
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
    "advocatus": (
        "Examine a paper through the two-office tribunal: "
        "Advocatus Diaboli drafts charges, Defensor Causae cross-examines them."
    ),
    "agora": (
        "Plan a fake r/wg21 Reddit thread for a paper. "
        "Produces a structural blueprint (anchors, calibration, submission, "
        "every reply slot with a brief) as JSON; reply text and characters "
        "are filled by a later generation phase."
    ),
    "assay": (
        "Run the 12-step assay pipeline on a paper. Produces a two-pass "
        "structural analysis report (six lenses, 25 test patterns) and "
        "persists intermediate artifacts for downstream use. Requires the "
        "paper to be indexed and converted first."
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
    "advocatus": "Paper ID (P4003R2) or year-month (2026-01) for batch examination.",
    "agora":     "Paper ID (P4003R2) or year-month (2026-01) for batch planning.",
    "assay":     "Paper ID (P4003R2) or year-month (2026-01) for batch analysis.",
    "status":    "Paper ID, year, year-month, or omit for all incomplete papers.",
}

_COMMANDS = {
    "mailing":   _mailing_mod,
    "download":  _download_mod,
    "convert":   _convert_mod,
    "advocatus": _advocatus_mod,
    "agora":     _agora_mod,
    "assay":     _assay_mod,
    "status":    _status_mod,
}

_VERB_FLAGS: dict[str, set[str]] = {
    "mailing":   set(),
    "download":  {"force", "concurrency"},
    "convert":   {"force", "concurrency", "check_content", "check_content_json", "keep_downstream", "yes"},
    "advocatus": {"debug", "trace", "step", "service", "provider", "force"},
    "agora":     {"debug", "trace", "step", "service", "provider", "force"},
    "assay":     {"debug", "trace", "step", "service", "force", "rerender"},
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
         help="Run only chunk C in parallel steps."),
    dict(name="service", flags=["--service"], action="append",
         default=None, metavar="NAME",
         help="Override service binding. Use NAME to override all slots, or SLOT=NAME for one slot. Repeatable."),
    dict(name="classifier", flags=["--classifier"], action="append",
         default=None, metavar="NAME",
         help="Override classifier slot binding. Use NAME to override all slots, or SLOT=NAME (e.g. selector=zeroshot-base) for one slot. Repeatable."),
    dict(name="provider", flags=["--provider"], default=None, metavar="NAME",
         help="Override the active transformer provider (device/dtype/batch). Defaults to PAPERFLOW_TRANSFORMER_PROVIDER, then [transformer_provider_defaults].default in SERVICES.toml, then 'auto' (host-detected)."),
    dict(name="keep_downstream", flags=["--keep-downstream"], action="store_true",
         default=False,
         help="On convert: don't clear advocatus/agora artifacts even "
              "when the markdown content changed. Stored loc.line offsets may "
              "be stale until you re-run those pipelines."),
    dict(name="yes", flags=["-y", "--yes"], action="store_true",
         default=False,
         help="On convert: skip the batch confirmation prompt that fires "
              "when a multi-paper invocation would invalidate downstream "
              "artifacts."),
    dict(name="rerender", flags=["--rerender"], action="store_true",
         default=False,
         help="Regenerate report from stored data without re-running "
              "the pipeline. Applies the current template to DB artifacts."),
]

_PAPER_ID_RE = re.compile(r"^[PND]\d{3,5}(R\d+)?$", re.IGNORECASE)

_EPILOG = """\
Examples:
  paperflow mailing                scrape mailing index
  paperflow download P3642R4       download one paper
  paperflow convert 2026-01        convert papers from Jan 2026 onward
  paperflow advocatus P4003R2      examine a paper
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
    if t.isdigit() and len(t) == 4:
        if int(t) >= 2011:
            return "year"
        raise ValueError(f"No WG21 mailings before 2011 (got {t}).")
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

    for process_verb in ("advocatus", "agora"):
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
