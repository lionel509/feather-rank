"""Shim module for backward compatibility.

This top-level module forwards imports to the packaged implementation in
`feather_rank.rules`. External code that imports `rules` will continue to work.
"""

from feather_rank.rules import *  # noqa: F401,F403
