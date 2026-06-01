"""Importing this package registers the built-in reporters (json, sarif, markdown)."""

from auditor.reporters import (  # noqa: F401
    json_reporter,
    markdown_reporter,
    sarif_reporter,
)
from auditor.reporters.base import Reporter, render

__all__ = ["Reporter", "render"]
