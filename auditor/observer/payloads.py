"""Every JSON shape the daemon puts on the wire, and the route each one answers (spec 12.1)."""

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from auditor.graph.flow import FlowPayload
from auditor.graph.payloads import (
    LogReport,
    RefinementRowPayload,
    RefinementsReport,
    RunRowPayload,
    TuningRowPayload,
)
from auditor.graph.refine.models import Assessment, ClientKind, RunnerKind, ToolCall
from auditor.observer.budget import BudgetState
from auditor.payload import WirePayload

# `daemon.py` reaches this module through `routes.py`, so the import is one way
if TYPE_CHECKING:
    from auditor.observer.daemon import DaemonRecord


class HealthPayload(WirePayload):
    """What `ensure` compares before it attaches: this daemon's home, db, version and wire compat."""

    home: str
    db_path: str
    version: str
    compat: int


class BudgetPayload(WirePayload):
    """One day's budget as the page's meter draws it, derived numbers included (recon Q12)."""

    spent_usd: float = 0.0
    runs: int = 0
    max_cost_usd_per_day: float = 0.0
    max_runs_per_day: int = 0
    low_budget_fraction: float = 0.0
    priced: bool = True
    evaluated: bool = False
    remaining_fraction: float = 0.0
    low: bool = False
    exhausted: bool = False

    @classmethod
    def of(cls, state: BudgetState) -> "BudgetPayload":
        """The stored numbers plus the three properties pydantic does not serialize."""
        return cls(
            **state.model_dump(),
            remaining_fraction=state.remaining_fraction,
            low=state.low,
            exhausted=state.exhausted,
        )


class RateLimitPayload(WirePayload):
    """The rate limit meter: the share the observer may take, and whether it is holding."""

    max_utilization: float = 0.0
    paused: bool = False
    resumes_at: float | None = None


class EvalStratumPayload(WirePayload):
    """One `(suite, stratum)` the page shows a lower bound for."""

    suite: str
    stratum: str
    n: int = 0
    precision: float = 0.0
    lower_bound_95: float = 0.0
    proven: bool = False


class RunnerEvalPayload(WirePayload):
    """The latest eval per runner (spec 12.1), with the strata behind it."""

    runner: RunnerKind
    model: str = ""
    measured: int = 0
    proven: int = 0
    strata: tuple[EvalStratumPayload, ...] = ()


class VectorStatusPayload(WirePayload):
    """The opt-in vector layer's state (spec 22); off until S13 and reported so the badge can say so."""

    enabled: bool = False
    model: str = ""
    ready: bool = False


class SessionPayload(WirePayload):
    """One attached session as `/api/status` shows it."""

    session_id: str
    repo: str
    client: ClientKind
    started_at: float = 0.0
    last_seen: float = 0.0


class Metered(WirePayload):
    """The two meters spec 12.1 draws beside the repo switcher, so they are named as a pair.

    Per repo, not per daemon: `max_cost_usd_per_day` is a per-repository ceiling, so one daemon
    serving two repos has two answers and no daemon-wide one (H-9).
    """

    budget: BudgetPayload | None = None
    limits: RateLimitPayload = Field(default_factory=RateLimitPayload)


class RepoPayload(Metered):
    """One repo in the switcher, with the meters that belong to it."""

    repo: str
    identity: str
    repo_dir_key: str
    attached: bool = False
    sessions: int = 0
    #: whether this one repo has an unconsumed spool; the counts of repos live on the two payloads
    #: that really count repos, `StatusPayload` and `EventAck`
    queued: bool = False
    #: spec 8.3's per-repo state machine, empty for a repo whose loop the daemon has not built
    state: str = ""


class ReposPayload(WirePayload):
    """The repo switcher's list."""

    repos: tuple[RepoPayload, ...] = ()


class StatusPayload(WirePayload):
    """The state badge, the meters and the counters the page polls every 3 s."""

    home: str
    version: str
    compat: int
    state: str = "idle"
    started_at: float = 0.0
    uptime_seconds: float = 0.0
    idle_seconds: float = 0.0
    repos: tuple[RepoPayload, ...] = ()
    sessions: tuple[SessionPayload, ...] = ()
    queued_repos: int = 0
    drained_events: int = 0
    evals: tuple[RunnerEvalPayload, ...] = ()
    vectors: VectorStatusPayload = Field(default_factory=VectorStatusPayload)


class RepoScoped(WirePayload):
    """Every per-repo answer names the repo it is for, because the page has a switcher."""

    repo: str
    identity: str


class GraphView(RepoScoped):
    """The visualization document `viz.build_payload` owns, unchanged, plus which repo it is.

    Left an open object on purpose (P12): `auditor/graph/ui/` is written against that contract and
    a second declaration of it here is a second thing to keep in step.
    """

    node_cap: int | None = None
    graph: dict[str, Any] = Field(default_factory=dict)


class RunsView(RepoScoped):
    """One page of the run stream. `LogReport` is `graph log`'s own shape, reused not copied."""

    log: LogReport = Field(default_factory=LogReport)


class RunDetailView(RepoScoped):
    """One run in full: the verbatim prompt, the tool trace, its rows, trials and the assessment."""

    run: RunRowPayload | None = None
    prompt: str = ""
    tool_trace: tuple[ToolCall, ...] = ()
    #: a convenience copy of ``run.trigger_detail.assessment``; presence, not status, discriminates
    assessment: Assessment | None = None
    refinements: tuple[RefinementRowPayload, ...] = ()
    #: spec 12.1's "tuning trials with metric deltas". A payload like every other row on this
    #: view, so the page reads a decoded value and never the repo identity behind it (S11 L8)
    trials: tuple[TuningRowPayload, ...] = ()


class RefinementsView(RepoScoped):
    """The refinement list by status, including `pending`, `redundant` and `drifted`."""

    refinements: RefinementsReport = Field(default_factory=RefinementsReport)


class EvalsView(RepoScoped):
    """The latest eval rows per runner for this repo."""

    runners: tuple[RunnerEvalPayload, ...] = ()


class FlowView(RepoScoped):
    """One flow walk, as `graph flow` answers it."""

    symbol: str = ""
    flow: FlowPayload | None = None


class EventAck(WirePayload):
    """What `POST /events` answers with, alongside its 202."""

    accepted: int = 0
    dropped: int = 0
    #: how many repos have an unconsumed spool, which is what the queue counts (M-3)
    queued_repos: int = 0


class AttachOutcome(WirePayload):
    """Spec 8.2's attach answer. `reason` is empty exactly when `attached` is true."""

    attached: bool = False
    reason: str = ""
    page_url: str = ""


class SessionAck(WirePayload):
    """A heartbeat or a detach: whether the daemon knew the session."""

    ok: bool = False
    reason: str = ""


class RestartRequest(BaseModel):
    """``POST /admin/restart``'s body: the wire version the caller speaks, and why it is asking.

    The caller declares its own ``compat`` so a daemon that already speaks it can decline rather
    than re-exec on any local process's say-so.
    """

    model_config = ConfigDict(frozen=True)

    compat: int
    reason: str = "wire compat mismatch"


class RestartAck(WirePayload):
    """A compat mismatch's answer: the caller returns at once and the next `ensure` attaches."""

    restarting: bool = False
    reason: str = ""


class DaemonStatus(WirePayload):
    """What every `auditr observer` verb answers with: where the daemon is and what just changed.

    One shape for all five verbs, because each of them ends by reporting the same thing;
    ``auditr-observer status --json`` prints the same keys and a test pins the pair (P19).
    """

    running: bool = False
    action: str = ""
    pid: int | None = None
    port: int | None = None
    home: str = ""
    version: str = ""
    compat: int = 0
    page_url: str = ""

    @classmethod
    def of(
        cls, action: str, record: "DaemonRecord | None", *, home: Path
    ) -> "DaemonStatus":
        """What just changed, and where the daemon is. ``home`` answers when nothing is running."""
        if record is None:
            return cls(action=action, home=str(home))
        return cls(
            running=True,
            action=action,
            pid=record.pid,
            port=record.port,
            home=record.home,
            version=record.version,
            compat=record.compat,
            page_url=f"http://127.0.0.1:{record.port}/",
        )


class RouteSpec(BaseModel):
    """One route: the payload model it answers with, and whether the page polls it conditionally."""

    model_config = ConfigDict(frozen=True)

    payload: type[WirePayload]
    #: `routes.TAGS` holds the tag function; `dispatch` calls it before the handler runs (P14)
    etag: bool = False


ROUTES: Mapping[tuple[str, str], RouteSpec] = {
    ("GET", "/health"): RouteSpec(payload=HealthPayload),
    ("GET", "/api/status"): RouteSpec(payload=StatusPayload, etag=True),
    ("GET", "/api/repos"): RouteSpec(payload=ReposPayload),
    ("GET", "/api/graph"): RouteSpec(payload=GraphView),
    ("GET", "/api/runs"): RouteSpec(payload=RunsView, etag=True),
    ("GET", "/api/runs/<id>"): RouteSpec(payload=RunDetailView),
    ("GET", "/api/refinements"): RouteSpec(payload=RefinementsView),
    ("GET", "/api/evals"): RouteSpec(payload=EvalsView),
    ("GET", "/api/flow"): RouteSpec(payload=FlowView),
    ("POST", "/events"): RouteSpec(payload=EventAck),
    ("POST", "/sessions/attach"): RouteSpec(payload=AttachOutcome),
    ("POST", "/sessions/heartbeat"): RouteSpec(payload=SessionAck),
    ("POST", "/sessions/detach"): RouteSpec(payload=SessionAck),
    ("POST", "/admin/restart"): RouteSpec(payload=RestartAck),
}
