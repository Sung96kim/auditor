"""Verbosity-driven logging via loguru — the classic ``-v`` / ``-vv`` / ``-vvv`` ladder.

Following loguru's library convention, ``auditor`` logging is **disabled on import** (see
``auditor/__init__.py``) so embedding the package or running the MCP server stays silent. The
CLI calls :func:`configure` to enable it and attach a single **stderr** sink — stdout is left
clean for the JSON/SARIF an agent parses. loguru ships a ``TRACE`` level, so the ladder is a
direct level map.
"""

import sys

from loguru import logger

_LEVELS = {0: "WARNING", 1: "INFO", 2: "DEBUG", 3: "TRACE"}


def configure(verbosity: int) -> None:
    """Enable auditor logging at the level implied by the ``-v`` count and route it to stderr."""
    logger.enable("auditor")
    logger.remove()
    logger.add(
        sys.stderr,
        level=_LEVELS.get(verbosity, "TRACE"),
        format="{message}",  # the engine colorizes inline via logger.opt(colors=True)
    )
