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


CONFIG_VERSION = 2

#: how long a rate limit holds when the SDK named no reset instant
DEFAULT_RATELIMIT_MINUTES = 5.0
#: how long an auth refusal holds before the loop re-asks the runner (H-3)
DEFAULT_AUTH_MINUTES = 15.0
#: the quiet window may restart at most this many times, so a flood cannot starve the ladder (H-4)
DEBOUNCE_WINDOW_CAP = 5.0
#: how long a repo waits after a pass raised, and the ceiling its doubling hits
DEFAULT_ERROR_SECONDS = 5.0
MAX_ERROR_SECONDS = 300.0
#: how many events a paused loop holds before the oldest are dropped (H-5)
HELD_EVENT_CAP = 500
#: how many deferred pairs the loop carries between passes (M-4)
DEFERRED_CAP = 200

# Where each pre-2 observer key lives now. `runner` and `tuning` are the two names version 2
# reused for tables, so a file holding the old scalar fails the load rather than passing through
# as an unknown key; the other eighteen are silently dropped without this map.
MOVED_OBSERVER_KEYS: dict[str, str] = {
    "codex_model": "runner.codex_model",
    "codex_prices": "runner.codex_prices",
    "debounce_seconds": "scheduling.debounce_seconds",
    "idle_shutdown_minutes": "scheduling.idle_shutdown_minutes",
    "low_budget_fraction": "budget.low_budget_fraction",
    "max_budget_usd_per_run": "budget.max_budget_usd_per_run",
    "max_changes_per_run": "limits.max_changes_per_run",
    "max_cost_usd_per_day": "budget.max_cost_usd_per_day",
    "max_nodes_per_run": "limits.max_nodes_per_run",
    "max_runs_per_day": "budget.max_runs_per_day",
    "max_turns": "limits.max_turns",
    "max_utilization": "budget.max_utilization",
    "min_new_unresolved": "scheduling.min_new_unresolved",
    "min_precision": "tuning.min_precision",
    "model": "runner.model",
    "run_on_stale": "scheduling.run_on_stale",
    "runner": "runner.agent",
    "session_expiry_minutes": "scheduling.session_expiry_minutes",
    "stopwords_max": "tuning.stopwords_max",
    "tuning": "tuning.mode",
}


class CodexPrice(BaseModel):
    """One Codex model's token prices, in USD per million tokens."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    input: float = Field(ge=0.0, description="USD per million input tokens.")
    output: float = Field(ge=0.0, description="USD per million output tokens.")


class BudgetConfig(BaseModel):
    """What a day of refinement runs may cost, per repository (spec 8.4)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    max_cost_usd_per_day: float = Field(
        2.0,
        ge=0.0,
        description="Hard ceiling on refinement spend per day, per repository.",
    )
    max_runs_per_day: int = Field(
        40, ge=0, description="Hard ceiling on runs per day, per repository."
    )
    max_budget_usd_per_run: float = Field(
        0.25, ge=0.0, description="Ceiling handed to one run."
    )
    max_budget_usd_per_eval: float = Field(
        12.0,
        ge=0.0,
        description="Ceiling on one `auditr graph eval` invocation, across every suite.",
    )
    low_budget_fraction: float = Field(
        0.25,
        ge=0.0,
        le=1.0,
        description=(
            "Remaining daily budget below which only high-value runs proceed. "
            "0 opts out: the low budget rule never fires."
        ),
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
    max_open_runs: int = Field(
        8, ge=1, description="Runs one process may hold staged at once."
    )
    stranded_run_seconds: int = Field(
        3600,
        ge=1,
        description="Seconds before a run still open is presumed dead and finished as skipped.",
    )
    max_held_events: int = Field(
        HELD_EVENT_CAP,
        ge=1,
        description="Events a paused loop holds before the oldest are dropped.",
    )
    max_deferred_pairs: int = Field(
        DEFERRED_CAP,
        ge=1,
        description="Deferred pairs a loop carries from one edit batch to the next drain.",
    )
    max_paths_per_batch: int = Field(
        200,
        ge=1,
        description="Edited paths one batch extracts; a larger batch is truncated and says so.",
    )
    max_queue_rows_per_pass: int = Field(
        500,
        ge=1,
        description="Queue rows one suspect drain reads; the rest wait for the next pass.",
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
    idle_shutdown_minutes: float = Field(
        30.0, ge=0.0, description="Idle minutes before the daemon exits; 0 never exits."
    )
    tick_seconds: float = Field(
        1.0,
        gt=0.0,
        description="Seconds the daemon blocks on its queue before looking at the clock.",
    )
    start_timeout_seconds: float = Field(
        10.0,
        gt=0.0,
        description="Seconds `observer start` waits for the daemon to publish itself.",
    )
    stop_timeout_seconds: float = Field(
        10.0, gt=0.0, description="Seconds `observer stop` waits for the daemon to go."
    )
    cooldown_minutes: int = Field(
        60,
        ge=0,
        description=(
            "Minutes a pair a run already looked at is skipped by the suspect drain. "
            "0 opts out: every pair is drainable on every pass."
        ),
    )
    run_on_stale: bool = Field(
        True, description="Re-run when an edit stales an existing refinement."
    )
    min_new_unresolved: int = Field(
        1,
        ge=1,
        description=(
            "New unresolved callees an edit batch needs to earn a run. At least one: a gate "
            "that fires on nothing opens a model-calling run for every rebuild."
        ),
    )
    verify_cooldown_minutes: int = Field(
        60,
        ge=0,
        description=(
            "Minutes between verify runs, so one unsettled refinement is re-asked at most "
            "once per window. 0 opts out: every tick with a pending row may verify."
        ),
    )
    ratelimit_pause_minutes: float = Field(
        DEFAULT_RATELIMIT_MINUTES,
        ge=0.0,
        description="Minutes a rate limit holds the loop when the runner named no reset instant.",
    )
    auth_pause_minutes: float = Field(
        DEFAULT_AUTH_MINUTES,
        ge=0.0,
        description="Minutes an auth refusal holds the loop before it re-asks the runner.",
    )
    debounce_restart_cap: float = Field(
        DEBOUNCE_WINDOW_CAP,
        ge=0.0,
        description="How many times the quiet window may restart before the batch is taken.",
    )
    error_backoff_seconds: float = Field(
        DEFAULT_ERROR_SECONDS,
        gt=0.0,
        description="Seconds a loop waits after a pass raised; it doubles per consecutive failure.",
    )
    max_error_backoff_seconds: float = Field(
        MAX_ERROR_SECONDS,
        gt=0.0,
        description="Ceiling on the doubling wait after repeated failures.",
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
        lt=1.0,
        description="Measured precision a kind needs before going active; 1.0 is unreachable.",
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
        CONFIG_VERSION,
        ge=1,
        description="Schema version of the settings file; 2 grouped the observer knobs.",
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


def home_json_layers() -> dict[str, object]:
    """The global settings file alone, before env: what a process serving many repos reads."""
    return _read_layer(user_config_path())


def user_json_layers(root: Path, *, directory: Path | None = None) -> dict[str, object]:
    """The global settings file deep-merged with this repo's overlay (repo wins), before env.

    Pass ``directory`` when the caller already resolved the repo's state dir: deriving it costs a
    ``git rev-parse``.
    """
    overlay = (directory if directory is not None else repo_dir(root)) / "config.json"
    return deep_merge(home_json_layers(), _read_layer(overlay))


def _resolved(layers: dict[str, object]) -> UserSettings:
    """``AUDITOR_USER_*`` over the JSON layers, which is the one thing both entry points share.

    Hand-built because the settings-source pipeline puts env below init values, and env has to
    win here.
    """
    return UserSettings.model_validate(
        deep_merge(layers, EnvSettingsSource(UserSettings)())
    )


def load_user_settings(root: Path, *, directory: Path | None = None) -> UserSettings:
    """The user's settings for one repo: defaults, the global file, the repo file, then env.

    Pass ``directory`` when the caller already resolved the repo's state dir, as
    :func:`user_json_layers` describes: deriving it costs a ``git rev-parse``.
    """
    return _resolved(user_json_layers(root, directory=directory))


def load_home_settings() -> UserSettings:
    """The user's settings with no repo overlay: the global file, then ``AUDITOR_USER_*``.

    What a process serving many repos at once reads for its own lifecycle, where there is no one
    repo to overlay; a per-repo answer goes through :func:`load_user_settings`.
    """
    return _resolved(home_json_layers())


class UserKeyReport(BaseModel):
    """What the two JSON layers hold that the model does not: typos, and settings that moved."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    unknown: tuple[str, ...] = ()
    moved: tuple[tuple[str, str], ...] = ()

    @classmethod
    def of(cls, layers: dict[str, object]) -> "UserKeyReport":
        """What a version-2 model makes of one merged settings dict.

        A key the version bump moved is reported as a move rather than a typo: calling it unknown
        tells the user they mistyped something they got right in an older release.
        """
        observer = layers.get("observer")
        table = observer if isinstance(observer, dict) else {}
        moved = tuple(
            (f"observer.{old}", f"observer.{new}")
            for old, new in MOVED_OBSERVER_KEYS.items()
            # `runner` and `tuning` name tables now, so each counts as moved only while it still
            # holds the scalar version 1 put there.
            if old in table
            and not (
                old in ObserverConfig.model_fields and isinstance(table[old], dict)
            )
        )
        renamed = {old for old, _ in moved}
        return cls(
            unknown=tuple(
                key
                for key in unknown_config_keys(layers, UserSettings)
                if key not in renamed
            ),
            moved=moved,
        )

    def moves(self) -> list[str]:
        """Each moved key as ``old -> new``, for a message or a payload."""
        return [f"{old} -> {new}" for old, new in self.moved]


def user_key_report(root: Path, *, directory: Path | None = None) -> UserKeyReport:
    """Read this repo's two JSON settings layers and report on them, in one pass.

    The user-settings counterpart of :func:`load_user_settings`: same layers, but what the model
    rejects rather than what it accepts.
    """
    return UserKeyReport.of(user_json_layers(root, directory=directory))
