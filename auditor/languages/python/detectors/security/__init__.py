"""Importing this package registers every built-in security detector."""

from auditor.languages.python.detectors.security import (
    crypto,  # noqa: F401
    deserialize,
    framework,
    injection,
    network,
)
