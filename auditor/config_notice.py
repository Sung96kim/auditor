"""The config keys no model declares, reported once per CLI invocation and once per repo root.

Callers record the root they resolved and never format anything: the CLI's root callback and the
MCP server's middleware ask for the lines when the run is over.
"""

from contextlib import suppress
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from auditor.config import ConfigError, unknown_repo_keys
from auditor.user_settings import unknown_user_keys

# A layer that cannot be read reports nothing from that layer: it is either already failing the
# run with its own one-line message, or it is a file this command never loads.
_UNREADABLE = (OSError, ConfigError, ValidationError)


class ConfigNotice(BaseModel):
    """One run's resolved root and extra config layers, plus whether the run reports the keys
    itself. Deliberately mutable: it is filled in as the run proceeds and read at the end."""

    model_config = ConfigDict(frozen=False, validate_assignment=True)

    HINT: ClassVar[str] = "unknown keys are ignored; run `auditr config check`"

    root: Path | None = None
    profile: str | None = None
    overrides: dict[str, object] | None = None
    directory: Path | None = None
    policy: tuple[str, ...] | None = None
    owned_by_command: bool = False
    reported: set[Path] = Field(default_factory=set)

    def reset(self) -> None:
        """Forget the previous run, so one process invoking the CLI twice reports twice."""
        self.root = None
        self.profile = None
        self.overrides = None
        self.directory = None
        self.policy = None
        self.owned_by_command = False
        self.reported = set()

    def record(
        self,
        root: Path,
        *,
        profile: str | None = None,
        overrides: dict[str, object] | None = None,
        directory: Path | None = None,
    ) -> Path:
        """Remember the root a run resolved and its extra layers, and hand the root straight back.

        ``directory`` is this repo's state dir when the caller already resolved it: deriving it
        costs a ``git rev-parse``.
        """
        self.root = root
        self.profile = profile
        self.overrides = overrides
        self.directory = directory
        self.policy = None
        return root

    def record_policy(self, keys: tuple[str, ...]) -> None:
        """Take the unknown keys the loader already found, so the notice merges no config twice."""
        self.policy = keys

    def owned(self) -> None:
        """Mark this run as one whose own output already lists the unknown keys."""
        self.owned_by_command = True

    def keys(self) -> list[str]:
        """Every dotted path neither settings model declares, repo policy first.

        Each source is read under its own guard, so a repo policy that cannot be read still lets
        the user's own file report its keys, and the other way round.
        """
        if self.root is None:
            return []
        found: list[str] = []
        with suppress(*_UNREADABLE):
            found += (
                list(self.policy)
                if self.policy is not None
                else unknown_repo_keys(
                    self.root, profile=self.profile, overrides=self.overrides
                )
            )
        with suppress(*_UNREADABLE):
            found += unknown_user_keys(self.root, directory=self.directory)
        return found

    def report(self) -> list[str]:
        """The lines this run should print, hint last, and a note that this root has spoken.

        Empty when the command lists the keys in its own output, when there is nothing to say, or
        when this process already reported this root: the CLI resets between invocations, so every
        run speaks, while a long-lived MCP server says it once per repo it is asked about.
        """
        if self.root is None or self.owned_by_command or self.root in self.reported:
            return []
        self.reported.add(self.root)
        keys = self.keys()
        return (
            [f"unknown config key: {key}" for key in keys] + [self.HINT] if keys else []
        )


NOTICE = ConfigNotice()
