"""Config-file audit context, the ``ConfigDetector`` base, and a raw-line literal provider.

A config or data file (``.env``, ``.yaml``, ``.json``, ``.toml``, ``.ini``, …) has no code to
parse, so detectors read its raw text. The shared secret sweep consumes the lines through the
registered ``config`` literal provider below.
"""

from typing import TYPE_CHECKING, ClassVar

from auditor.languages.base import Detector, LineIndexed
from auditor.languages.sweep import register_literal_provider
from auditor.models import FileRole, Finding

if TYPE_CHECKING:
    from auditor.config import ResolvedConfig


class ConfigContext(LineIndexed):
    """Everything a config-file detector needs for one file: its path and raw lines."""

    __slots__ = ("file_path", "source", "lines", "role", "config")

    def __init__(
        self,
        *,
        file_path: str,
        source: str,
        role: FileRole,
        config: "ResolvedConfig",
    ) -> None:
        self.file_path = file_path
        self.source = source
        self.lines = source.splitlines()
        self.role = role
        self.config = config


class ConfigDetector(Detector):
    """Base for config/data-file rules. Subclass, set the ClassVars, implement ``run``."""

    abstract: ClassVar[bool] = True
    language: ClassVar[str] = "config"

    def run(self, ctx: "ConfigContext") -> list[Finding]:  # type: ignore[override]
        raise NotImplementedError


def _config_lines(ctx: object) -> list[tuple[int, str]]:
    """Every ``(1-indexed line, text)`` for a config file, so the shared secret sweep can inspect
    values in plain data files rather than only code string literals."""
    lines: list[str] = ctx.lines  # type: ignore[attr-defined]
    return [(i, line) for i, line in enumerate(lines, 1) if line.strip()]


register_literal_provider("config", _config_lines)
