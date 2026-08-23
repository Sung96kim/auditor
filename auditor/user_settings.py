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


class ObserverConfig(BaseModel):
    """Personal budget and behavior knobs for the background graph observer."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    enabled: bool = Field(
        True, description="Attach the observer to auditor-configured repos."
    )
    runner: Runner = Field(
        "auto",
        description="Which agent runs refinements: auto picks by what is installed.",
    )
    model: ClaudeModel = Field(
        "haiku", description="Claude model tier for a refinement run."
    )
    codex_model: str = Field(
        "", description="Codex model override; empty uses the user's Codex default."
    )
    min_precision: float = Field(
        0.95,
        ge=0.0,
        le=1.0,
        description="Measured precision a kind needs before going active.",
    )
    max_cost_usd_per_day: float = Field(
        2.0, ge=0.0, description="Hard ceiling on refinement spend per day, all repos."
    )
    max_runs_per_day: int = Field(40, ge=0, description="Hard ceiling on runs per day.")
    max_budget_usd_per_run: float = Field(
        0.25, ge=0.0, description="Ceiling handed to one run."
    )
    max_turns: int = Field(20, ge=1, description="Agent turns before a run is cut off.")
    max_nodes_per_run: int = Field(
        12, ge=1, description="Graph nodes one run may look at."
    )
    max_changes_per_run: int = Field(
        25, ge=1, description="Proposals one run may commit."
    )
    max_utilization: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="Share of the rate-limit window the observer may take; the rest is the human's.",
    )
    min_new_unresolved: int = Field(
        1, ge=0, description="New unresolved callees an edit batch needs to earn a run."
    )
    run_on_stale: bool = Field(
        True, description="Re-run when an edit stales an existing refinement."
    )
    low_budget_fraction: float = Field(
        0.25,
        ge=0.0,
        le=1.0,
        description="Remaining daily budget below which only high-value runs proceed.",
    )
    debounce_seconds: int = Field(
        20, ge=0, description="Quiet period after an edit before assessing it."
    )
    session_expiry_minutes: int = Field(
        45, ge=1, description="Idle minutes before a session is considered gone."
    )
    idle_shutdown_minutes: int = Field(
        30, ge=1, description="Idle minutes before the daemon exits."
    )
    skipped_retention_days: int = Field(
        7,
        ge=0,
        description="Days to keep skipped-run records for `graph log --skipped`.",
    )
    worktrees: Worktrees = Field(
        "main", description="Observe only the main worktree, or every worktree."
    )
    suspects: bool = Field(
        True, description="Queue suspect nodes found during a build."
    )
    tuning: TuningMode = Field(
        "propose", description="Knob tuning: propose changes, or stay off."
    )
    stopwords_max: int = Field(
        20, ge=0, description="Most repo-specific stopwords a tuning proposal may add."
    )
    open_browser: bool = Field(
        True, description="Open the live page when the daemon starts."
    )
    codex_prices: dict[str, CodexPrice] = Field(
        default_factory=dict,
        description="Per-model price overrides; empty uses the shipped table.",
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
    validation. That switch is read straight from the environment by the hooks and the daemon.
    """

    model_config = SettingsConfigDict(
        env_prefix="AUDITOR_USER_", extra="ignore", frozen=True
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


def user_json_layers(root: Path) -> dict[str, object]:
    """The global settings file deep-merged with this repo's overlay (repo wins), before env."""
    return deep_merge(
        _read_layer(user_config_path()), _read_layer(repo_dir(root) / "config.json")
    )


def load_user_settings(root: Path) -> UserSettings:
    """Resolve the user's settings for one repo: defaults, then the global file, then the
    per-repo file, then ``AUDITOR_USER_*`` (later wins). The layering is hand-built because the
    settings-source pipeline puts env below init values, and env has to win here."""
    merged = deep_merge(user_json_layers(root), EnvSettingsSource(UserSettings)())
    return UserSettings.model_validate(merged)


def unknown_user_keys(root: Path) -> list[str]:
    """Dotted paths in the two JSON layers that ``UserSettings`` does not declare."""
    return unknown_config_keys(user_json_layers(root), UserSettings)
