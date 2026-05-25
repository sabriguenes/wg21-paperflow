#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Error hierarchy for the tomd converter."""

from __future__ import annotations


class TomdError(Exception):
    """Base class for tomd-raised exceptions."""


class UnsupportedSourceFormatError(TomdError):
    """Raised when a source path has a suffix other than ``.pdf`` / ``.html`` / ``.htm``."""


class CheckContentArgError(TomdError):
    """Raised when :func:`tomd.lib.check_content.check_paper_content` receives
    an unsupported source suffix.
    """
