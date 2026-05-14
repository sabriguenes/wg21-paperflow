#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""CLI command module for 'paperflow dissect'."""

from __future__ import annotations

import argparse

from paperstore.backend import StorageBackend


def command(args: argparse.Namespace, backend: StorageBackend) -> int:
    from dissect import dissect_paper, dissect_since
    from cli.llm_command import run_llm_command

    return run_llm_command(
        args, backend,
        paper_fn=dissect_paper,
        batch_fn=dissect_since,
        write_fn=backend.write_dissect_md,
        output_check_fn=lambda pid: backend.get_paper_md_path(pid).with_suffix(".dissect.md"),
        verb="Dissect",
        progress_label="Extracting",
        trace_tool="dissect",
        success_msg="Dissect written to {path}",
    )
