#
# Copyright (c) 2026 Sergio DuBois (sentientsergio@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Paperflow CLI -- WG21 C++ standards paper ingestion and conversion."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cli")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
