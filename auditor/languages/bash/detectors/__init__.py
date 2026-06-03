"""Importing this package registers every built-in shell detector."""

from auditor.languages.bash.detectors import (
    malware,  # noqa: F401
    secrets,
)
