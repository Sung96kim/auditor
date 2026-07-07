"""Importing this package registers every built-in Python detector."""

from auditor.languages.python.detectors import (
    async_rules,  # noqa: F401
    config_rules,
    correctness,
    dry_rules,
    graph_rules,
    malware,
    oop,
    orchestration,
    secrets,
    security,
    sqlalchemy_rules,
    style,
    suggestions,
    supply_chain,
    testing,
    typing_rules,
    xfile,
)
