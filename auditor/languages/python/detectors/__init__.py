"""Importing this package registers every built-in Python detector."""

from auditor.languages.python.detectors import (
    async_rules,  # noqa: F401
    config_rules,
    correctness,
    malware,
    oop,
    secrets,
    security,
    style,
    suggestions,
    supply_chain,
    typing_rules,
    xfile,
)
