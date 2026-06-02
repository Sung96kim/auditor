"""Importing this package registers the built-in reporters (json, sarif, markdown, html)."""

from auditor.reporters import (
    html_reporter,
    json_reporter,  # noqa: F401
    markdown_reporter,
    sarif_reporter,
)
from auditor.reporters.base import Reporter, render

__all__ = ["Reporter", "render"]
