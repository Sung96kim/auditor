"""Importing this package registers every built-in Python detector."""

from auditor.languages.python.detectors import (  # noqa: F401
    async_rules,
    config_rules,
    correctness,
    oop,
    security,
    style,
    typing_rules,
    xfile,
)
