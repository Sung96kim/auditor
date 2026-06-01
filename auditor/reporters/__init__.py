"""Importing this package registers the built-in reporters (json, sarif, markdown, html)."""

from auditor.reporters import (  # noqa: F401
    html_reporter,
    json_reporter,
    markdown_reporter,
    sarif_reporter,
)
from auditor.reporters.base import Reporter, render

__all__ = ["Reporter", "render"]
