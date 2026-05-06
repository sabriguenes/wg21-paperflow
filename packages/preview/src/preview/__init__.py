#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Side-by-side preview server for WG21 paper sources and converted markdown."""

from preview.app import create_app
from preview.render import render_markdown
from preview.watcher import MarkdownWatcher

__all__ = ["create_app", "render_markdown", "MarkdownWatcher"]
__version__ = "0.3.0"
