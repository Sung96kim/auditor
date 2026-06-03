"""Committed-secret detection for Python source (shared catalog + sweep)."""

from typing import ClassVar

from auditor.languages.secret_sweeps import SecretSweep


class HardcodedSecret(SecretSweep):
    rule_id: ClassVar[str] = "PY-SECRET-DETECTED"
    language: ClassVar[str] = "python"
