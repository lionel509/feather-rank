"""Shim module for backward compatibility.

This top-level module forwards imports to the packaged implementation in
`feather_rank.mmr`. External code that imports `mmr` will continue to work.
"""

from feather_rank.mmr import *  # noqa: F401,F403
