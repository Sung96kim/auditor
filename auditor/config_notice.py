"""The config keys no model declares, reported once per CLI invocation and once per MCP process.

Callers record the root they resolved and never format anything: the CLI's root callback and the
MCP server's middleware ask for the keys when the run is over.
"""

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from auditor.config import unknown_repo_keys
from auditor.user_settings import unknown_user_keys


class ConfigNotice(BaseModel):
    """One run's resolved root and extra config layers, plus whether the run reports the keys
    itself. Deliberately mutable: it is filled in as the run proceeds and read at the end."""

    model_config = ConfigDict(frozen=False)

    HINT: ClassVar[str] = "unknown keys are ignored; run `auditr config check`"

    root: Path | None = None
    profile: str | None = None
    overrides: dict[str, object] | None = None
    owned_by_command: bool = False

    def reset(self) -> None:
        """Forget the previous run, so one process invoking the CLI twice reports twice."""
        self.root = None
        self.profile = None
        self.overrides = None
        self.owned_by_command = False

    def record(
        self,
        root: Path,
        *,
        profile: str | None = None,
        overrides: dict[str, object] | None = None,
    ) -> Path:
        """Remember the root a run resolved and its extra layers, and hand the root straight back."""
        self.root = root
        self.profile = profile
        self.overrides = overrides
        return root

    def owned(self) -> None:
        """Mark this run as one whose own output already lists the unknown keys."""
        self.owned_by_command = True

    def keys(self) -> list[str]:
        """Every dotted path neither settings model declares, repo policy first.

        A config the loader cannot even read reports nothing: the run itself already failed on it
        with a one-line message, and this is called while the context is closing.
        """
        if self.root is None:
            return []
        try:
            return unknown_repo_keys(
                self.root, profile=self.profile, overrides=self.overrides
            ) + unknown_user_keys(self.root)
        except (OSError, ValueError):  # unreadable or malformed layer, already reported
            return []

    def reportable(self) -> list[str]:
        """The keys this run should surface, empty when its own output already lists them."""
        return [] if self.owned_by_command else self.keys()


NOTICE = ConfigNotice()
