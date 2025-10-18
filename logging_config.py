"""
Centralized logging configuration for the project.

Usage:
- Production (default): concise INFO-level logs.
- Testing: set LOG_LEVEL=DEBUG (or call setup_logging(level="DEBUG")) for very detailed logs.

Environment variables:
- LOG_LEVEL: DEBUG|INFO|WARNING|ERROR|CRITICAL
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Literal, Optional

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _level_from_env(default: LogLevel = "INFO") -> int:
    level_str = os.getenv("LOG_LEVEL", default).upper()
    return {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }.get(level_str, logging.INFO)


def setup_logging(level: Optional[LogLevel] = None, mode: Optional[Literal["test", "prod"]] = None) -> None:
    """Configure root logger.

    Args:
        level: Optional string level (e.g., "DEBUG"). If omitted, uses LOG_LEVEL env var or INFO.
        mode: Optional mode hint ("test"|"prod") to tweak formatting; defaults based on level.
    """
    # Determine level
    numeric_level = _level_from_env() if level is None else _level_from_env(level)

    # Avoid duplicate handlers if re-configuring
    root = logging.getLogger()
    if root.handlers:
        for h in list(root.handlers):
            root.removeHandler(h)

    # Choose format: verbose for DEBUG, concise otherwise
    is_debug = numeric_level <= logging.DEBUG
    fmt_verbose = (
        "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(funcName)s | %(message)s"
    )
    fmt_concise = "%(levelname).1s %(message)s"
    fmt = fmt_verbose if (mode == "test" or is_debug) else fmt_concise

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(fmt=fmt, datefmt="%H:%M:%S"))

    root.setLevel(numeric_level)
    root.addHandler(handler)

    # Tame noisy third-party loggers unless in full debug
    logging.getLogger("discord").setLevel(logging.INFO if is_debug else logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.INFO if is_debug else logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper to get a module logger."""
    return logging.getLogger(name)
