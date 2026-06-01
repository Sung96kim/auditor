"""Importing this package registers every built-in security detector."""

from auditor.languages.python.detectors.security import (  # noqa: F401
    crypto,
    deserialize,
    framework,
    injection,
    network,
)
