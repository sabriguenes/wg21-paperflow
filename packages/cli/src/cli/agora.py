#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""CLI command module for 'paperflow agora'."""

from __future__ import annotations

import argparse

from paperstore.backend import StorageBackend


def command(args: argparse.Namespace, backend: StorageBackend) -> int:
    from agora import agora_paper, agora_since
    from cli.llm_command import run_llm_command

    return run_llm_command(
        args, backend,
        paper_fn=agora_paper,
        batch_fn=agora_since,
        write_fn=None,
        output_check_fn=lambda pid: backend.get_agora_path(pid),
        verb="Agora",
        progress_label="Planning thread",
        trace_tool="agora",
        success_msg="Thread blueprint written to {path}",
    )
