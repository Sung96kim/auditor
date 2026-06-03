"""Committed-secret detection for shell scripts (shared catalog + sweep over each line)."""

from typing import ClassVar

from auditor.languages.secret_sweeps import SecretSweep


class HardcodedSecret(SecretSweep):
    rule_id: ClassVar[str] = "SH-SECRET-DETECTED"
    language: ClassVar[str] = "shell"
