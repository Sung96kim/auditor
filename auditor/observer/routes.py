"""Spec 12.1's handlers: one route, one payload, one `Reply` (spec 8.1)."""

import asyncio
import hashlib
import logging
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypeVar
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, ConfigDict, ValidationError

from auditor.config import AuditorSettings, load_config
from auditor.database import IndexStore, open_repo_index, open_shared_index
from auditor.discovery import FileDiscovery
from auditor.graph.flow import (
    DEFAULT_FLOW_DEPTH,
    DEFAULT_FLOW_LIMIT,
    FlowDirection,
    FlowOptions,
)
from auditor.graph.model import LOG_ROW_LIMIT, enum_value
from auditor.graph.payloads import (
    LogFilter,
    LogView,
    RefinementRowPayload,
    RunRowPayload,
)
from auditor.graph.query import GraphQuery, LogQuery
from auditor.graph.refine.models import MODEL_RUNNERS, RunnerKind
from auditor.graph.refine.tiers import TierPolicy
from auditor.graph.viz import build_payload
from auditor.observer.events import Event, EventQueue, EventRequest
from auditor.observer.payloads import (
    ROUTES,
    AttachOutcome,
    EvalStratumPayload,
    EvalsView,
    EventAck,
    FlowView,
    GraphView,
    HealthPayload,
    Metered,
    RefinementsView,
    RepoPayload,
    ReposPayload,
    RestartAck,
    RestartRequest,
    RunDetailView,
    RunnerEvalPayload,
    RunsView,
    SessionAck,
    SessionPayload,
    StatusPayload,
)
from auditor.observer.server import Reply
from auditor.observer.sessions import AttachRequest, Session, SessionBook, SessionRef
from auditor.paths import identity_key, repo_identity
from auditor.payload import WirePayload
from auditor.user_settings import UserSettings, load_user_settings

Handler = Callable[["Router", str, Mapping[str, str], bytes], Reply]
Tag = Callable[["Router", Mapping[str, str]], str]
Meters = Callable[[str], Metered]
Read = Callable[[Path], WirePayload]

_LOG = logging.getLogger("auditor.observer")
#: what a repo-scoped route answers when the query named no repo, or named a path that is not one
_NO_REPO = "a repo=<absolute path> naming a directory is required"
_K = TypeVar("_K")
_V = TypeVar("_V")


def _no_drop(made: object) -> None:
    """What `Readers._cached` does with a build that lost the race and owns nothing to release."""


def _no_meters(key: str) -> Metered:
    """A router with no daemon behind it meters nothing; `serve` passes the daemon's own reader."""
    return Metered()


def _no_loop_state(key: str) -> str:
    """A router with no daemon behind it runs no loop, so every repo's state is empty."""
    return ""


def _no_drained() -> int:
    """A router with no daemon behind it has drained nothing; `serve` passes the real counter."""
    return 0


def repo_root(repo: str | None) -> Path | None:
    """The absolute directory this name is, or None when it is anything else.

    No fallback and no relative name: both resolve against the daemon's inherited cwd, which is a
    repo the caller never asked about, and answering from it is a silent substitution.
    """
    root = Path(repo) if repo else None
    if root is None or not root.is_absolute() or not root.is_dir():
        return None
    return root


def route_pattern(path: str) -> str:
    """The one route with a variable segment, matched by shape rather than by regex.

    ``/api/runs/`` with an empty id matches too, and its handler answers the same 404 an
    unknown id gets.
    """
    if path.startswith("/api/runs/") and path.count("/") == 3:
        return "/api/runs/<id>"
    return path


_TRUE = frozenset({"1", "true", "yes", "on"})


def _named(query: Mapping[str, str], name: str) -> str | None:
    """One control's raw value, or None when the query did not name it at all.

    A control named with an empty value is a typo rather than a default: `enum_value` says so for
    the enums, `parse_since` says so for the window, and the other three read the same way here.

    Raises:
        ValueError: when the control is present and blank.
    """
    raw = query.get(name)
    if raw is None:
        return None
    if not raw.strip():
        raise ValueError(f"{name} was named with no value")
    return raw


def _flag(raw: str | None) -> bool:
    """A query flag. `parse_qs` drops a bare `?skipped`, so the page sends `skipped=1`."""
    return raw is not None and raw.strip().lower() in _TRUE


def _int(query: Mapping[str, str], name: str, default: int) -> int:
    """One integer control, refused by name. `int()`'s own message names no field."""
    raw = _named(query, name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, not {raw!r}") from None


def runs_filter(query: Mapping[str, str]) -> LogFilter:
    """Spec 12.1's collapsed `skipped` rows and the stream's window, through `graph log`'s parser.

    Raises:
        ValueError: for any value the CLI would also refuse, which the handler answers 400 with.
    """
    status = _named(query, "status")
    return LogFilter.of(
        view=LogView.RUNS,  # never the caller's: one route, one view (P16)
        status=status.split(",") if status else None,
        since=_named(query, "since"),
        skipped=_flag(_named(query, "skipped")),
        limit=_int(query, "limit", LOG_ROW_LIMIT),
    )


def filter_key(chosen: LogFilter, *, since: str | None = None) -> str:
    """A filter's fingerprint, so two windows over one ledger cannot share an ETag.

    ``since`` is fingerprinted as the raw query value, never as the epoch `parse_since` resolved
    against the clock, or a window would mint a new tag every request and could never 304 (P23).
    """
    body = chosen.model_dump_json(exclude={"since"})
    return hashlib.sha256(f"{body}|{since or ''}".encode()).hexdigest()[:12]


def flow_options(query: Mapping[str, str], *, hub_fan_in: int) -> FlowOptions:
    """Spec 12.1's direction toggle, depth slider and hub disclosure. `FlowOptions.of` clamps.

    ``expand_hubs`` is the whole walk rather than one node: `limit` already bounds it, so the
    reader's first hub click costs one round trip and every later one costs none.

    Raises:
        ValueError: when `depth` or `limit` is not an integer, or `direction` is not a direction.
    """
    named = _named(query, "direction")
    return FlowOptions.of(
        hub_fan_in=hub_fan_in,
        direction=(
            FlowDirection(enum_value(named, FlowDirection, "direction"))
            if named is not None
            else FlowDirection.OUT
        ),
        depth=_int(query, "depth", DEFAULT_FLOW_DEPTH),
        limit=_int(query, "limit", DEFAULT_FLOW_LIMIT),
        expand_hubs=_flag(_named(query, "expand_hubs")),
    )


class DaemonIdentity(BaseModel):
    """What `/health` answers and `ensure` compares: one object, not four parameters (P31)."""

    model_config = ConfigDict(frozen=True)

    home: Path
    db_path: Path
    version: str
    compat: int


class Readers:
    """The store reads the API routes make, injected so `test_server.py` needs no database.

    One `IndexStore` per identity for the daemon's lifetime: `SqliteWorker` owns its own thread and
    wraps a `concurrent.futures.Future` per call, so a handle is neither loop bound nor thread
    bound and the 3 s poll pays no connect (P15).
    """

    def __init__(self, *, settings: UserSettings) -> None:
        #: `evals` needs `runner.model` and `tuning.min_precision`; nothing else reads it
        self.settings = settings
        self._handles: dict[str, IndexStore] = {}
        self._identities: dict[Path, str] = {}
        self._configs: dict[Path, AuditorSettings] = {}
        self._users: dict[Path, UserSettings] = {}
        self._lock = threading.Lock()

    def _cached(
        self,
        store: dict[_K, _V],
        key: _K,
        make: Callable[[], _V],
        drop: Callable[[_V], None] = _no_drop,
    ) -> _V:
        """The one cache idiom: read under the lock, build outside it, keep the first to land.

        Building outside the lock keeps one slow open from serialising every other reader; ``drop``
        disposes of a build that lost the race, which only the index handle has to close.
        """
        with self._lock:
            known = store.get(key)
        if known is not None:
            return known
        made = make()
        with self._lock:
            kept = store.setdefault(key, made)
        if kept is not made:
            drop(made)
        return kept

    def identity(self, root: Path, *, identity: str | None = None) -> str:
        """This repo's checkout identity, resolved once and kept (P15, M-10).

        `repo_identity` is an uncached git subprocess of about 1.4 ms, and one 3 s poll would
        otherwise pay it once per reader it touches.
        """
        if identity is not None:
            return identity
        return self._cached(self._identities, root, lambda: repo_identity(root))

    def index(self, root: Path, *, identity: str | None = None) -> IndexStore:
        """This repo's handle, opened once and kept. Bound to the identity, never the repo key."""
        return self._cached(
            self._handles,
            self.identity(root, identity=identity),
            lambda: asyncio.run(open_repo_index(root)),
            lambda handle: asyncio.run(handle.aclose()),
        )

    def config(self, root: Path) -> AuditorSettings:
        """This repo's own `AuditorSettings`, loaded once and kept.

        Named `config` rather than `settings` because :attr:`settings` is the user's own layer,
        which P31 put on this object first. Two callers read it: the `RepoLoop` the daemon
        builds, and `Router.api_flow`, which needs `graph.flow_hub_fan_in` to parse a walk.
        """
        return self._cached(self._configs, root, lambda: load_config(root))

    def user(self, root: Path) -> UserSettings:
        """This repo's own `UserSettings`, loaded once and kept.

        The daemon serves many repos and :attr:`settings` is its home-level layer, so a per-repo
        answer resolves the overlay here; a repo that will not load falls back to it uncached.
        """
        try:
            return self._cached(self._users, root, lambda: load_user_settings(root))
        # not cached, so one torn write is retried on the next poll rather than kept forever
        except (OSError, ValidationError):
            _LOG.exception(
                "could not read %s's user settings; using the daemon's", root
            )
            return self.settings

    def close(self) -> None:
        """Release every handle on shutdown, so the worker threads end with the process."""
        with self._lock:
            for handle in self._handles.values():
                asyncio.run(handle.aclose())
            self._handles.clear()

    def runs(
        self,
        root: Path,
        *,
        identity: str | None = None,
        chosen: LogFilter | None = None,
    ) -> RunsView:
        identity = self.identity(root, identity=identity)
        report = asyncio.run(
            LogQuery(self.index(root, identity=identity)).page(chosen or LogFilter())
        )
        return RunsView(repo=str(root), identity=identity, log=report)

    async def _ledger(self, index: IndexStore) -> tuple[int, float, float]:
        """The run count, the newest start and the last change, on one event loop not three."""
        count = await index.runs.count()
        newest = await index.runs.runs(limit=1)
        changed = await index.runs.last_change()
        return count, newest[0].started_at if newest else 0.0, changed

    def runs_tag(
        self,
        root: Path,
        *,
        identity: str | None = None,
        chosen: LogFilter | None = None,
        since: str | None = None,
    ) -> str:
        """`(repo, count, newest start, last change, filter)`: three aggregates, no decoded page.

        The repo is in the tag because the page has a switcher: two repos whose counts coincide
        would otherwise share a tag and the second would 304 on the first one's rows (P14). The
        filter is in it because one ledger answers two bodies once `skipped=1` exists, and the
        window rides as the caller's raw string rather than as its resolved epoch (P23). The last
        change is in it because a run is inserted once and then mutated in place, and neither the
        count nor the newest start moves when one finishes.
        """
        identity = self.identity(root, identity=identity)
        count, started, changed = asyncio.run(
            self._ledger(self.index(root, identity=identity))
        )
        key = filter_key(chosen or LogFilter(), since=since)
        return f'W/"{identity_key(identity)}-{count}-{started}-{changed}-{key}"'

    def graph(self, root: Path, *, identity: str | None = None) -> GraphView:
        identity = self.identity(root, identity=identity)
        document = asyncio.run(build_payload(self.index(root, identity=identity)))
        return GraphView(repo=str(root), identity=identity, graph=document)

    def refinements(
        self, root: Path, *, identity: str | None = None
    ) -> RefinementsView:
        identity = self.identity(root, identity=identity)
        report = asyncio.run(
            LogQuery(self.index(root, identity=identity)).refinements()
        )
        return RefinementsView(repo=str(root), identity=identity, refinements=report)

    def flow(
        self,
        root: Path,
        symbol: str,
        *,
        identity: str | None = None,
        options: FlowOptions | None = None,
    ) -> FlowView:
        identity = self.identity(root, identity=identity)
        chosen = options or FlowOptions(
            hub_fan_in=self.config(root).graph.flow_hub_fan_in
        )
        walk = asyncio.run(
            GraphQuery(self.index(root, identity=identity)).flow(symbol, chosen)
        )
        return FlowView(repo=str(root), identity=identity, symbol=symbol, flow=walk)

    async def _detail(
        self, index: IndexStore, root: Path, identity: str, run_id: str
    ) -> RunDetailView | None:
        """One run's four reads on one event loop, rather than one loop apiece."""
        row = await index.runs.run(run_id)
        if row is None:
            return None
        rows = await index.refinements.of_run(run_id)
        anchors = await index.refinements.anchors([r.refinement_id for r in rows])
        trials = [t for t in await index.tuning.tuning() if t.run_id == run_id]
        return RunDetailView(
            repo=str(root),
            identity=identity,
            run=RunRowPayload.of(row),
            prompt=row.prompt or "",
            tool_trace=row.tool_trace,
            assessment=row.trigger_detail.assessment,
            refinements=tuple(
                RefinementRowPayload.of(r, anchors.get(r.refinement_id, ()))
                for r in rows
            ),
            trials=tuple(trials),
        )

    def run(
        self, root: Path, run_id: str, *, identity: str | None = None
    ) -> RunDetailView | None:
        """One run in full, or None for an id this repo's ledger does not hold (L-7)."""
        identity = self.identity(root, identity=identity)
        index = self.index(root, identity=identity)
        return asyncio.run(self._detail(index, root, identity, run_id))

    def _model_for(self, runner: RunnerKind, settings: UserSettings) -> str:
        """The model this runner is pinned to, which `EvalsDB.latest` needs beside the runner.

        A runner's own pin only: a Codex with no `codex_model` has no model, and lending it
        Claude's would draw Claude's eval numbers under the Codex mark.
        """
        pinned = settings.observer.runner
        if runner is RunnerKind.CODEX:
            return pinned.codex_model
        return pinned.model

    async def _runner_evals(
        self, index: IndexStore, settings: UserSettings
    ) -> tuple[RunnerEvalPayload, ...]:
        """Every runner's latest eval on one event loop, with the tier policy behind each."""
        minimum = settings.observer.tuning.min_precision
        runners: list[RunnerEvalPayload] = []
        for runner in MODEL_RUNNERS:
            model = self._model_for(runner, settings)
            rows = await index.evals.latest(runner, model)
            if not rows:
                # a runner with no eval is a row, not an omission: the page says "no eval yet"
                runners.append(RunnerEvalPayload(runner=runner, model=model))
                continue
            policy = TierPolicy.of(
                rows, min_precision=minimum, runner=runner, model=model
            )
            runners.append(
                RunnerEvalPayload(
                    runner=runner,
                    model=model,
                    measured=len(policy.measured),
                    proven=len(policy.proven),
                    strata=tuple(
                        EvalStratumPayload(
                            suite=row.suite,
                            stratum=row.stratum,
                            n=row.metrics.n,
                            precision=row.metrics.precision,
                            lower_bound_95=row.metrics.lower_bound_95,
                            proven=(row.suite, row.stratum) in policy.proven,
                        )
                        for row in rows
                    ),
                )
            )
        return tuple(runners)

    def evals(self, root: Path, *, identity: str | None = None) -> EvalsView:
        """The latest eval row per runner, with the tier policy that says which strata are proven.

        The runner and its model come from this repo's own settings: `/api/evals` is a per-repo
        answer, and the daemon's home-level layer is only the fallback.
        """
        identity = self.identity(root, identity=identity)
        index = self.index(root, identity=identity)
        runners = asyncio.run(self._runner_evals(index, self.user(root)))
        return EvalsView(repo=str(root), identity=identity, runners=runners)

    def roster(self) -> tuple[RunnerEvalPayload, ...]:
        """Which runners exist and the model each is pinned to, daemon-wide and unmeasured.

        Daemon-wide by construction: this reads :attr:`settings`, the home layer, while
        `/api/evals` resolves the per-repo overlay through :meth:`user`, so a repo that overrides
        `observer.runner.model` shows the overridden model in its numbers and this one in the
        roster. Both layers resolve the model the same way, so the eval block cannot draw one
        runner's name beside another runner's numbers.
        """
        return tuple(
            RunnerEvalPayload(
                runner=runner, model=self._model_for(runner, self.settings)
            )
            for runner in MODEL_RUNNERS
        )

    async def _repo_paths(self) -> tuple[str, ...]:
        """Every repo path the shared index holds; the one place its own row format is read."""
        index = await open_shared_index()
        try:
            return tuple(str(row["repo"]) for row in await index.repos.list())
        finally:
            await index.aclose()

    def repos(self) -> ReposPayload:
        """Every repo the shared index knows, which is the switcher's list (P32)."""
        return ReposPayload(
            repos=tuple(
                RepoPayload(
                    repo=repo,
                    identity=identity,
                    repo_dir_key=identity_key(identity),
                )
                for repo, identity in (
                    (repo, self.identity(Path(repo)))
                    for repo in asyncio.run(self._repo_paths())
                )
            )
        )


class RouterDeps(BaseModel):
    """Everything one `Router` is built from, in groups (item 3a).

    A model rather than eleven keywords: a new seam is a field here and a line in `serve`,
    not a new positional threaded through a procedural assembly.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    #: identity: what `/health` answers and `ensure` compares
    identity: DaemonIdentity
    #: state the daemon and the handlers share
    queue: EventQueue
    sessions: SessionBook
    #: reads: the store surface and the page document
    readers: Readers
    page: Callable[[str | None], str]
    #: policy: spec 8.2's attach gate, and how a browser is opened on the page
    gate: Callable[[AttachRequest], str]
    open_page: Callable[[str], object]
    #: what one repo's `RepoLoop` is doing, keyed by its spool key
    loop_state: Callable[[str], str] = _no_loop_state
    #: one repo's budget and rate limit meters, which are per repo and never daemon-wide (H-9)
    meters: Meters = _no_meters
    #: how many events have been drained: the daemon counts, the router is what puts it on the wire
    drained: Callable[[], int] = _no_drained


#: a read is never activity, so no `GET` pushes spec 8.1's idle deadline out: `daemon.py:743-744`
#: feeds `Router.last_request` straight into the `IdleTimer`, and every page fetch is a read
READ_METHODS = frozenset({"GET", "HEAD"})


class Router:
    """Turns one method and path into a `Reply`. Holds no socket and no thread."""

    def __init__(
        self, deps: RouterDeps, *, url: str = "", started_at: float = 0.0
    ) -> None:
        self.deps = deps
        #: late-bound: the URL is only known once the server has a port
        self.url = url
        self.started_at = started_at or time.time()
        self.revision = 0
        self.restarting = False
        self.opened_page = False
        self.last_request = 0.0
        #: the gap before the request being served, which is the badge's "how quiet has it been"
        self.idle_seconds = 0.0

    def bump(self) -> int:
        """One state change on the status payload, which is what its ETag counts.

        Moved by every write route through :meth:`dispatch`, and by the daemon: a loop state, a
        meter, a drained batch and an expired session all reach `on_change`, wired to this.
        """
        self.revision += 1
        return self.revision

    def dispatch(
        self, method: str, target: str, headers: Mapping[str, str], body: bytes
    ) -> Reply:
        """One request. The tag is computed before the handler, so a 304 costs no query (P14)."""
        parsed = urlparse(target)
        # blanks are kept, so `?depth=` reaches the parser that refuses it rather than vanishing
        query = {
            k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()
        }
        now = time.time()
        self.idle_seconds = now - (self.last_request or self.started_at)
        if method not in READ_METHODS:
            self.last_request = now
        # the two read methods reach the page; it is answered before the table, naming no payload
        if method in {"GET", "HEAD"} and parsed.path == "/":
            return Reply.html(self.deps.page(query.get("repo")))

        key = (method, route_pattern(parsed.path))
        handler = HANDLERS.get(key)
        if handler is None:
            return Reply.error(404, f"no route for {method} {parsed.path}")
        tag = ""
        if ROUTES[key].etag:
            tag = TAGS[key](self, query)
            if tag and headers.get("If-None-Match") == tag:
                return Reply(status=304, etag=tag)
        reply = handler(self, parsed.path, query, body)
        return reply.model_copy(update={"etag": tag}) if tag else reply

    def _root(self, query: Mapping[str, str]) -> Path | None:
        """The absolute directory this query names, or None when it named anything else."""
        return repo_root(query.get("repo"))

    def _scoped(self, query: Mapping[str, str], read: Read) -> Reply:
        """One repo-scoped answer, or the 400 a query naming no usable repo earns."""
        root = self._root(query)
        if root is None:
            return Reply.error(400, _NO_REPO)
        return Reply.json(read(root))

    @property
    def state(self) -> str:
        """The daemon's own word, never a loop's: the badge reads `repos[i].state` for a repo."""
        return "restarting" if self.restarting else "running"

    def status_tag(self, query: Mapping[str, str]) -> str:
        """Restart-unique and change-unique: a 304 here means nothing on the payload moved.

        Milliseconds rather than whole seconds, because two daemons whose starts round to the
        same second would both mint `W/"<sec>-0"` and a page holding the dead one's tag would
        304 over the new one's state.
        """
        return f'W/"{self.started_at:.3f}-{self.revision}"'

    def runs_tag(self, query: Mapping[str, str]) -> str:
        """Empty for a query the handler will refuse, so a 400 is never short-circuited to 304."""
        root = self._root(query)
        if root is None:
            return ""
        try:
            chosen = runs_filter(query)
        except ValueError:
            return ""
        return self.deps.readers.runs_tag(root, chosen=chosen, since=query.get("since"))

    def health(self, path: str, query: Mapping[str, str], body: bytes) -> Reply:
        return Reply.json(
            HealthPayload(
                home=str(self.deps.identity.home),
                db_path=str(self.deps.identity.db_path),
                version=self.deps.identity.version,
                compat=self.deps.identity.compat,
            )
        )

    def _metered(self, row: RepoPayload) -> RepoPayload:
        """One switcher row with the three columns only a daemon behind the router can fill.

        The two meters are assigned as the models they are declared as: ``model_copy`` does not
        validate, so splatting a dump would leave the frozen payload holding raw dicts (M1).
        """
        drawn = self.deps.meters(row.repo_dir_key)
        return row.model_copy(
            update={
                "state": self.deps.loop_state(row.repo_dir_key),
                "budget": drawn.budget,
                "limits": drawn.limits,
            }
        )

    def api_status(self, path: str, query: Mapping[str, str], body: bytes) -> Reply:
        payload = StatusPayload(
            home=str(self.deps.identity.home),
            version=self.deps.identity.version,
            compat=self.deps.identity.compat,
            state=self.state,
            started_at=self.started_at,
            idle_seconds=self.idle_seconds,
            uptime_seconds=time.time() - self.started_at,
            queued_repos=self.deps.queue.pending_keys,
            drained_events=self.deps.drained(),
            evals=self.deps.readers.roster(),
            repos=tuple(
                self._metered(repo) for repo in self.deps.readers.repos().repos
            ),
            sessions=tuple(
                SessionPayload(
                    session_id=s.session_id,
                    repo=s.repo,
                    client=s.client,
                    started_at=s.started_at,
                    last_seen=s.last_seen,
                )
                for s in self.deps.sessions.live(now=time.time())
            ),
        )
        return Reply.json(
            payload
        )  # the tag was already computed and is attached by `dispatch`

    def api_repos(self, path: str, query: Mapping[str, str], body: bytes) -> Reply:
        """The switcher's list, with the session, queue and meter columns the router fills."""
        live = self.deps.sessions.live(now=time.time())
        pending = set(self.deps.queue.keys())
        return Reply.json(
            ReposPayload(
                repos=tuple(
                    self._metered(row).model_copy(
                        update={
                            "attached": any(s.repo == row.repo for s in live),
                            "sessions": sum(1 for s in live if s.repo == row.repo),
                            "queued": row.repo_dir_key in pending,
                        }
                    )
                    for row in self.deps.readers.repos().repos
                )
            )
        )

    def api_graph(self, path: str, query: Mapping[str, str], body: bytes) -> Reply:
        return self._scoped(query, self.deps.readers.graph)

    def api_runs(self, path: str, query: Mapping[str, str], body: bytes) -> Reply:
        try:
            chosen = runs_filter(query)
        except ValueError as invalid:
            return Reply.error(400, str(invalid))
        return self._scoped(
            query, lambda root: self.deps.readers.runs(root, chosen=chosen)
        )

    def api_runs_detail(
        self, path: str, query: Mapping[str, str], body: bytes
    ) -> Reply:
        root = self._root(query)
        if root is None:
            return Reply.error(400, _NO_REPO)
        run_id = path.rsplit("/", 1)[-1]
        view = self.deps.readers.run(root, run_id) if run_id else None
        if view is None:
            return Reply.error(404, f"no run {run_id} in this repo's ledger")
        return Reply.json(view)

    def api_refinements(
        self, path: str, query: Mapping[str, str], body: bytes
    ) -> Reply:
        return self._scoped(query, self.deps.readers.refinements)

    def api_evals(self, path: str, query: Mapping[str, str], body: bytes) -> Reply:
        return self._scoped(query, self.deps.readers.evals)

    def api_flow(self, path: str, query: Mapping[str, str], body: bytes) -> Reply:
        symbol = query.get("symbol", "")
        root = self._root(query)
        if root is None:
            return Reply.error(400, _NO_REPO)
        try:
            options = flow_options(
                query, hub_fan_in=self.deps.readers.config(root).graph.flow_hub_fan_in
            )
        except ValueError as invalid:
            return Reply.error(400, str(invalid))
        return Reply.json(self.deps.readers.flow(root, symbol, options=options))

    def events(self, path: str, query: Mapping[str, str], body: bytes) -> Reply:
        """Stage 0 through the shape predicate, then spool, then 202. No lock is taken here.

        `FileDiscovery(root)` is built with the default excludes, so a repo's configured `exclude`
        globs do not reach Stage 0 here and their edits still spool. The hook runs a narrower,
        config-free Stage 0 of its own (spec 8.2), which only ever drops what this would drop too,
        so this predicate stays the authoritative one.
        """
        try:
            request = EventRequest.model_validate_json(body or b"{}")
        except ValidationError as invalid:
            return Reply.error(
                400, f"unusable event body: {invalid.error_count()} problems"
            )
        root = Path(request.repo)
        finder = FileDiscovery(root)
        kept = tuple(p for p in request.paths if finder.auditable_shape(p))
        if kept:
            self.deps.queue.put(
                request.key,
                Event(
                    repo=str(root),
                    paths=kept,
                    kind=request.kind,
                    client=request.client,
                    session_id=request.session_id,
                    batch=request.batch,
                    at=time.time(),
                ),
            )
            self.bump()  # the queue filling is one of the counters the page polls (P14)
        return Reply.json(
            EventAck(
                accepted=len(kept),
                dropped=len(request.paths) - len(kept),
                queued_repos=self.deps.queue.pending_keys,
            ),
            status=202,
        )

    def sessions_attach(
        self, path: str, query: Mapping[str, str], body: bytes
    ) -> Reply:
        try:
            request = AttachRequest.model_validate_json(body or b"{}")
        except ValidationError as invalid:
            return Reply.error(
                400, f"unusable attach body: {invalid.error_count()} problems"
            )
        if self.restarting:
            return Reply.json(
                AttachOutcome(attached=False, reason="the daemon is restarting")
            )
        reason = self.deps.gate(request)
        if reason:
            return Reply.json(AttachOutcome(attached=False, reason=reason))
        now = time.time()
        earlier = self.deps.sessions.get(request.session_id, now=now)
        self.deps.sessions.attach(
            Session(
                session_id=request.session_id,
                repo=request.repo,
                identity=self.deps.readers.identity(Path(request.repo)),
                client=request.client,
                #: a re-attach is the same session, so its age is not reset
                started_at=earlier.started_at if earlier else now,
                last_seen=now,
            )
        )
        if not self.opened_page:  # spec 12.1: once per daemon lifetime, on first attach
            self.opened_page = True
            self.deps.open_page(self.url)
        self.bump()
        return Reply.json(AttachOutcome(attached=True, page_url=self.url))

    def _session_ref(self, body: bytes) -> SessionRef | Reply:
        """One id, or the 400 a body that is not one earns."""
        try:
            return SessionRef.model_validate_json(body or b"{}")
        except ValidationError as invalid:
            return Reply.error(
                400, f"unusable session body: {invalid.error_count()} problems"
            )

    def sessions_heartbeat(
        self, path: str, query: Mapping[str, str], body: bytes
    ) -> Reply:
        ref = self._session_ref(body)
        if isinstance(ref, Reply):
            return ref
        known = self.deps.sessions.heartbeat(ref.session_id, now=time.time())
        # `sessions[i].last_seen` is on the page, so its tag has to move with the heartbeat
        if known:
            self.bump()
        return Reply.json(
            SessionAck(ok=known, reason="" if known else "no such session")
        )

    def sessions_detach(
        self, path: str, query: Mapping[str, str], body: bytes
    ) -> Reply:
        ref = self._session_ref(body)
        if isinstance(ref, Reply):
            return ref
        known = self.deps.sessions.detach(ref.session_id)
        if known:  # a session leaving is what the page's badge shows; an unknown id is not (P14)
            self.bump()
        return Reply.json(
            SessionAck(ok=known, reason="" if known else "no such session")
        )

    def admin_restart(self, path: str, query: Mapping[str, str], body: bytes) -> Reply:
        """Spec 8.1's re-exec, asked for by a caller whose wire version this daemon does not speak."""
        try:
            request = RestartRequest.model_validate_json(body or b"{}")
        except ValidationError as invalid:
            return Reply.error(
                400, f"unusable restart body: {invalid.error_count()} problems"
            )
        if request.compat == self.deps.identity.compat:
            return Reply.json(
                RestartAck(restarting=False, reason="the wire is already compatible")
            )
        self.restarting = True
        self.bump()
        return Reply.json(RestartAck(restarting=True, reason=request.reason))


#: the one dispatch table: a route with no handler, or a handler no route reaches, fails a test
HANDLERS: Mapping[tuple[str, str], Handler] = {
    ("GET", "/health"): Router.health,
    ("GET", "/api/status"): Router.api_status,
    ("GET", "/api/repos"): Router.api_repos,
    ("GET", "/api/graph"): Router.api_graph,
    ("GET", "/api/runs"): Router.api_runs,
    ("GET", "/api/runs/<id>"): Router.api_runs_detail,
    ("GET", "/api/refinements"): Router.api_refinements,
    ("GET", "/api/evals"): Router.api_evals,
    ("GET", "/api/flow"): Router.api_flow,
    ("POST", "/events"): Router.events,
    ("POST", "/sessions/attach"): Router.sessions_attach,
    ("POST", "/sessions/heartbeat"): Router.sessions_heartbeat,
    ("POST", "/sessions/detach"): Router.sessions_detach,
    ("POST", "/admin/restart"): Router.admin_restart,
}

#: the tag for each conditionally polled route, computed before its handler runs (P14)
TAGS: Mapping[tuple[str, str], Tag] = {
    ("GET", "/api/status"): Router.status_tag,
    ("GET", "/api/runs"): Router.runs_tag,
}
