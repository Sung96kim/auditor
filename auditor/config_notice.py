"""The config keys no model declares, reported once per CLI invocation and once per repo root.

Callers record the root they resolved and never format anything: the CLI's root callback and the
MCP server's middleware ask for the lines when the run is over.
"""

from contextlib import suppress
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from auditor.config import ConfigError, unknown_repo_keys
from auditor.user_settings import UserKeyReport, user_key_report

# A layer that cannot be read reports nothing from that layer: it is either already failing the
# run with its own one-line message, or it is a file this command never loads.
_UNREADABLE = (OSError, ConfigError, ValidationError)


class ConfigNotice(BaseModel):
    """One run's resolved root and extra config layers, plus whether the run reports the keys
    itself. Deliberately mutable: it is filled in as the run proceeds and read at the end."""

    model_config = ConfigDict(frozen=False, validate_assignment=True)

    HINT: ClassVar[str] = "unknown keys are ignored; run `auditr config check`"
    MOVED: ClassVar[str] = "config version 2 moved these settings"

    root: Path | None = None
    profile: str | None = None
    overrides: dict[str, object] | None = None
    policy: tuple[str, ...] | None = None
    user: UserKeyReport | None = None
    owned_by_command: bool = False
    reported: set[Path] = Field(default_factory=set)

    def reset(self) -> None:
        """Forget the previous run, so one process invoking the CLI twice reports twice."""
        self.root = None
        self.profile = None
        self.overrides = None
        self.policy = None
        self.user = None
        self.owned_by_command = False
        self.reported = set()

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
        self.policy = None
        self.user = None
        return root

    def record_policy(self, keys: tuple[str, ...]) -> None:
        """Take the unknown keys the loader already found, so the notice merges no config twice."""
        self.policy = keys

    def owned(self) -> None:
        """Mark this run as one whose own output already lists the unknown keys."""
        self.owned_by_command = True

    def keys(self, *, directory: Path | None = None) -> list[str]:
        """Every dotted path neither settings model declares, repo policy first.

        Each source is read under its own guard, so a repo policy that cannot be read still lets
        the user's own file report its keys, and the other way round. Pass ``directory`` when the
        caller already resolved this repo's state dir: deriving it costs a ``git rev-parse``.
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
        found += list(self.user_keys(directory=directory).unknown)
        return found

    def user_keys(self, *, directory: Path | None = None) -> UserKeyReport:
        """What the user's two JSON layers hold that the model does not, read once per run."""
        if self.user is None and self.root is not None:
            with suppress(*_UNREADABLE):
                self.user = user_key_report(self.root, directory=directory)
        return self.user if self.user is not None else UserKeyReport()

    def report(self) -> list[str]:
        """The lines this run should print, and a note that this root has spoken.

        A root is reported once per process: the CLI resets between invocations, so every run
        speaks, while a long-lived MCP server says it once per repo it is asked about. A command
        that lists the unknown keys in its own output still gets the migration line, which no
        payload carries.
        """
        if self.root is None or self.root in self.reported:
            return []
        self.reported.add(self.root)
        moves = self.user_keys().moves()
        lines = (
            [
                f"{self.MOVED}: {', '.join(moves)}; "
                "move them, then run `auditr init --force`"
            ]
            if moves
            else []
        )
        if self.owned_by_command:
            return lines
        keys = self.keys()
        return lines + (
            [f"unknown config key: {key}" for key in keys] + [self.HINT] if keys else []
        )


NOTICE = ConfigNotice()
