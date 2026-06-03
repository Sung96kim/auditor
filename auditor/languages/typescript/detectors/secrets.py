"""Committed-secret detection for TypeScript/JS source (shared catalog + sweep)."""

from typing import ClassVar

from auditor.languages.secret_sweeps import SecretSweep


class HardcodedSecret(SecretSweep):
    rule_id: ClassVar[str] = "TS-SECRET-DETECTED"
    language: ClassVar[str] = "typescript"
