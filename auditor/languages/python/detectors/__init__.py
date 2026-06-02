"""Importing this package registers every built-in Python detector."""

from auditor.languages.python.detectors import (
    async_rules,  # noqa: F401
    config_rules,
    correctness,
    oop,
    security,
    style,
    suggestions,
    typing_rules,
    xfile,
)
