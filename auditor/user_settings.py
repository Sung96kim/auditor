"""The settings a user owns, kept out of the repo.

Repo policy (rules, thresholds, roles) is shared through git and lives in ``config.py``. These
are personal and per-machine: observer budgets and runner choice, the opt-in vector layer. They
live in ``$AUDITOR_HOME/config.json`` with an optional per-repo overlay, are layered by
:func:`load_user_settings`, and never appear in a repository.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, EnvSettingsSource, SettingsConfigDict

from auditor.config import deep_merge, unknown_config_keys
from auditor.paths import read_json_dict, repo_dir, user_config_path

Runner = Literal["auto", "claude", "codex"]
ClaudeModel = Literal["haiku", "sonnet"]
Worktrees = Literal["main", "all"]
TuningMode = Literal["propose", "off"]


class CodexPrice(BaseModel):
    """One Codex model's token prices, in USD per million tokens."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    input: float = Field(ge=0.0, description="USD per million input tokens.")
    output: float = Field(ge=0.0, description="USD per million output tokens.")


class BudgetConfig(BaseModel):
    """What a day of refinement runs may cost, across every repo."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    max_cost_usd_per_day: float = Field(
        2.0, ge=0.0, description="Hard ceiling on refinement spend per day, all repos."
    )
    max_runs_per_day: int = Field(40, ge=0, description="Hard ceiling on runs per day.")
    max_budget_usd_per_run: float = Field(
        0.25, ge=0.0, description="Ceiling handed to one run."
    )
    low_budget_fraction: float = Field(
        0.25,
        ge=0.0,
        le=1.0,
        description="Remaining daily budget below which only high-value runs proceed.",
    )
    max_utilization: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="Share of the rate-limit window the observer may take; the rest is the human's.",
    )


class LimitsConfig(BaseModel):
    """How large one refinement run may get."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    max_turns: int = Field(20, ge=1, description="Agent turns before a run is cut off.")
    max_nodes_per_run: int = Field(
        12, ge=1, description="Graph nodes one run may look at."
    )
    max_changes_per_run: int = Field(
        25, ge=1, description="Proposals one run may commit."
    )


class SchedulingConfig(BaseModel):
    """When an edit batch earns a run, and how long the daemon waits around."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    debounce_seconds: int = Field(
        20, ge=0, description="Quiet period after an edit before assessing it."
    )
    session_expiry_minutes: int = Field(
        45, ge=1, description="Idle minutes before a session is considered gone."
    )
    idle_shutdown_minutes: int = Field(
        30, ge=1, description="Idle minutes before the daemon exits."
    )
    run_on_stale: bool = Field(
        True, description="Re-run when an edit stales an existing refinement."
    )
    min_new_unresolved: int = Field(
        1, ge=0, description="New unresolved callees an edit batch needs to earn a run."
    )


class RunnerConfig(BaseModel):
    """Which agent runs a refinement, on which model, at which prices."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    agent: Runner = Field(
        "auto",
        description="Which agent runs refinements: auto picks by what is installed.",
    )
    model: ClaudeModel = Field(
        "haiku", description="Claude model tier for a refinement run."
    )
    codex_model: str = Field(
        "", description="Codex model override; empty uses the user's Codex default."
    )
    codex_prices: dict[str, CodexPrice] = Field(
        default_factory=dict,
        description="Per-model price overrides; empty uses the shipped table.",
    )


class TuningConfig(BaseModel):
    """Whether the observer may propose knob changes, and how far it may go."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    mode: TuningMode = Field(
        "propose", description="Knob tuning: propose changes, or stay off."
    )
    stopwords_max: int = Field(
        20, ge=0, description="Most repo-specific stopwords a tuning proposal may add."
    )
    min_precision: float = Field(
        0.95,
        ge=0.0,
        le=1.0,
        description="Measured precision a kind needs before going active.",
    )


class ObserverConfig(BaseModel):
    """Personal budget and behavior knobs for the background graph observer."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    enabled: bool = Field(
        True, description="Attach the observer to auditor-configured repos."
    )
    worktrees: Worktrees = Field(
        "main", description="Observe only the main worktree, or every worktree."
    )
    suspects: bool = Field(
        True, description="Queue suspect nodes found during a build."
    )
    open_browser: bool = Field(
        True, description="Open the live page when the daemon starts."
    )
    skipped_retention_days: int = Field(
        7,
        ge=0,
        description="Days to keep skipped-run records for `graph log --skipped`.",
    )
    budget: BudgetConfig = Field(
        default_factory=BudgetConfig, description="Spend and run ceilings."
    )
    limits: LimitsConfig = Field(
        default_factory=LimitsConfig, description="Per-run size limits."
    )
    scheduling: SchedulingConfig = Field(
        default_factory=SchedulingConfig, description="Trigger and daemon timing."
    )
    runner: RunnerConfig = Field(
        default_factory=RunnerConfig, description="Agent, model and prices."
    )
    tuning: TuningConfig = Field(
        default_factory=TuningConfig, description="Knob tuning policy."
    )


class VectorsConfig(BaseModel):
    """The opt-in vector layer (spec section 22), off until its own eval clears."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    enabled: bool = Field(
        False, description="Enable the sqlite-vec + static-embedding layer."
    )
    model: str = Field(
        "minishlab/potion-base-8M@bf8b056",
        description="Pinned model2vec model and revision used for embeddings.",
    )


class UserSettings(BaseSettings):
    """Personal settings, layered by :func:`load_user_settings`.

    The prefix is ``AUDITOR_USER_`` and never ``AUDITOR_``: on the shared prefix the documented
    ``AUDITOR_OBSERVER=0`` kill switch would be parsed as this model's ``observer`` table and fail
    validation. ``__`` separates nested keys, so one knob is reachable without a JSON blob.
    """

    model_config = SettingsConfigDict(
        env_prefix="AUDITOR_USER_",
        env_nested_delimiter="__",
        extra="ignore",
        frozen=True,
    )

    config_version: int = Field(
        1, ge=1, description="Schema version of the settings file."
    )
    observer: ObserverConfig = Field(
        default_factory=ObserverConfig, description="Graph observer settings."
    )
    vectors: VectorsConfig = Field(
        default_factory=VectorsConfig, description="Optional vector layer settings."
    )


def _read_layer(path: Path) -> dict[str, object]:
    """One JSON settings layer, minus its ``$``-prefixed editor keys."""
    return {
        key: value
        for key, value in read_json_dict(path).items()
        if not key.startswith("$")
    }


def user_json_layers(root: Path, *, directory: Path | None = None) -> dict[str, object]:
    """The global settings file deep-merged with this repo's overlay (repo wins), before env.

    Pass ``directory`` when the caller already resolved the repo's state dir: deriving it costs a
    ``git rev-parse``.
    """
    overlay = (repo_dir(root) if directory is None else directory) / "config.json"
    return deep_merge(_read_layer(user_config_path()), _read_layer(overlay))


def load_user_settings(root: Path) -> UserSettings:
    """Resolve the user's settings for one repo: defaults, then the global file, then the
    per-repo file, then ``AUDITOR_USER_*`` (later wins). The layering is hand-built because the
    settings-source pipeline puts env below init values, and env has to win here."""
    merged = deep_merge(user_json_layers(root), EnvSettingsSource(UserSettings)())
    return UserSettings.model_validate(merged)


def unknown_user_keys(root: Path, *, directory: Path | None = None) -> list[str]:
    """Dotted paths in the two JSON layers that ``UserSettings`` does not declare."""
    return unknown_config_keys(
        user_json_layers(root, directory=directory), UserSettings
    )
