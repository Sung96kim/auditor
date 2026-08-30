"""Spec 12.1's handlers: one route, one payload, one `Reply` (spec 8.1)."""

import asyncio
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, ConfigDict, ValidationError

from auditor.config import AuditorSettings, load_config
from auditor.database import IndexStore, open_repo_index, open_shared_index
from auditor.discovery import FileDiscovery
from auditor.graph.payloads import (
    LogFilter,
    RefinementRowPayload,
    RunRowPayload,
)
from auditor.graph.query import GraphQuery, LogQuery
from auditor.graph.refine.models import RunnerKind
from auditor.graph.refine.tiers import TierPolicy
from auditor.graph.viz import build_payload
from auditor.observer.events import Event, EventQueue, EventRequest
from auditor.observer.payloads import (
    ROUTES,
    AttachOutcome,
    BudgetPayload,
    EvalStratumPayload,
    EvalsView,
    EventAck,
    FlowView,
    GraphView,
    HealthPayload,
    RateLimitPayload,
    RefinementsView,
    RepoPayload,
    ReposPayload,
    RestartAck,
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
from auditor.user_settings import UserSettings

Handler = Callable[["Router", str, Mapping[str, str], bytes], Reply]
Tag = Callable[["Router", Mapping[str, str]], str]
Meters = Callable[[], tuple[BudgetPayload | None, RateLimitPayload]]


def _no_meters() -> tuple[BudgetPayload | None, RateLimitPayload]:
    """S8b holds neither number; S8c injects the callable that does (S8c seam 3)."""
    return None, RateLimitPayload()


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
        self._lock = threading.Lock()

    def identity(self, root: Path, *, identity: str | None = None) -> str:
        """This repo's checkout identity, resolved once and kept (P15, M-10).

        `repo_identity` is an uncached git subprocess of about 1.4 ms, and one 3 s poll would
        otherwise pay it once per reader it touches.
        """
        if identity is not None:
            return identity
        with self._lock:
            known = self._identities.get(root)
        if known is None:
            known = repo_identity(root)
            with self._lock:
                self._identities[root] = known
        return known

    def index(self, root: Path, *, identity: str | None = None) -> IndexStore:
        """This repo's handle, opened once and kept. Bound to the identity, never the repo key."""
        identity = self.identity(root, identity=identity)
        with self._lock:
            handle = self._handles.get(identity)
            if handle is None:
                handle = asyncio.run(open_repo_index(root))
                self._handles[identity] = handle
            return handle

    def config(self, root: Path) -> AuditorSettings:
        """This repo's own `AuditorSettings`, loaded once and kept (S8c seam 4).

        Named `config` rather than `settings` because :attr:`settings` is the user's own layer,
        which P31 put on this object first.
        """
        with self._lock:
            known = self._configs.get(root)
        if known is None:
            known = load_config(root)
            with self._lock:
                self._configs[root] = known
        return known

    def close(self) -> None:
        """Release every handle on shutdown, so the worker threads end with the process."""
        with self._lock:
            for handle in self._handles.values():
                asyncio.run(handle.aclose())
            self._handles.clear()

    def runs(self, root: Path, *, identity: str | None = None) -> RunsView:
        identity = self.identity(root, identity=identity)
        report = asyncio.run(
            LogQuery(self.index(root, identity=identity)).page(LogFilter())
        )
        return RunsView(repo=str(root), identity=identity, log=report)

    def runs_tag(self, root: Path, *, identity: str | None = None) -> str:
        """`(count, newest started_at)`: two shipped readers, one decoded row, no new SQL (P14)."""
        index = self.index(root, identity=identity)
        count = asyncio.run(index.runs.count())
        newest = asyncio.run(index.runs.runs(limit=1))
        return f'W/"{count}-{newest[0].started_at if newest else 0}"'

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

    def flow(self, root: Path, symbol: str, *, identity: str | None = None) -> FlowView:
        identity = self.identity(root, identity=identity)
        walk = asyncio.run(GraphQuery(self.index(root, identity=identity)).flow(symbol))
        return FlowView(repo=str(root), identity=identity, symbol=symbol, flow=walk)

    def run(
        self, root: Path, run_id: str, *, identity: str | None = None
    ) -> RunDetailView | None:
        """One run in full, or None for an id this repo's ledger does not hold (L-7)."""
        identity = self.identity(root, identity=identity)
        index = self.index(root, identity=identity)
        row = asyncio.run(index.runs.run(run_id))
        if row is None:
            return None
        rows = asyncio.run(index.refinements.of_run(run_id))
        anchors = asyncio.run(
            index.refinements.anchors([r.refinement_id for r in rows])
        )
        trials = [t for t in asyncio.run(index.tuning.tuning()) if t.run_id == run_id]
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

    def _model_for(self, runner: RunnerKind) -> str:
        """The model this runner is pinned to, which `EvalsDB.latest` needs beside the runner."""
        pinned = self.settings.observer.runner
        if runner is RunnerKind.CODEX and pinned.codex_model:
            return pinned.codex_model
        return pinned.model

    def evals(self, root: Path, *, identity: str | None = None) -> EvalsView:
        """The latest eval row per runner, with the tier policy that says which strata are proven."""
        identity = self.identity(root, identity=identity)
        index = self.index(root, identity=identity)
        minimum = self.settings.observer.tuning.min_precision
        runners: list[RunnerEvalPayload] = []
        for runner in RunnerKind:
            model = self._model_for(runner)
            rows = asyncio.run(index.evals.latest(runner, model))
            if not rows:
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
        return EvalsView(repo=str(root), identity=identity, runners=tuple(runners))

    def repos(self) -> ReposPayload:
        """Every repo the shared index knows, which is the switcher's list (P32)."""
        rows = asyncio.run(self._shared_repos())
        return ReposPayload(
            repos=tuple(
                RepoPayload(
                    repo=str(row["repo"]),
                    identity=identity,
                    repo_dir_key=identity_key(identity),
                )
                for row, identity in (
                    (row, self.identity(Path(str(row["repo"])))) for row in rows
                )
            )
        )

    @staticmethod
    async def _shared_repos() -> list[dict]:
        index = await open_shared_index()
        try:
            return await index.repos.list()
        finally:
            await index.aclose()


class Router:
    """Turns one method and path into a `Reply`. Holds no socket and no thread."""

    def __init__(
        self,
        *,
        identity: DaemonIdentity,
        queue: EventQueue,
        sessions: SessionBook,
        readers: Readers,
        page: Callable[[str | None], str],
        gate: Callable[[AttachRequest], str],
        open_page: Callable[[str], object],
        url: str = "",
        started_at: float = 0.0,
        loop_state: Callable[[str], str] = lambda key: "",
        meters: Meters = _no_meters,
    ) -> None:
        self.identity = identity
        self.queue = queue
        self.sessions = sessions
        self.readers = readers
        self.page = page
        self.gate = gate
        self.open_page = open_page
        self.url = url
        self.started_at = started_at or time.time()
        #: what one repo's `RepoLoop` is doing; S8b has no loop, so it answers "" (S8c seam 2)
        self.loop_state = loop_state
        #: the budget and rate limit meters `/api/status` draws; S8c owns both (S8c seam 3)
        self.meters = meters
        self.revision = 0
        self.restarting = False
        self.opened_page = False
        self.last_request = 0.0

    def bump(self) -> int:
        """One state change. `/api/status`'s tag carries this counter; S8c moves it most."""
        self.revision += 1
        return self.revision

    def dispatch(
        self, method: str, target: str, headers: Mapping[str, str], body: bytes
    ) -> Reply:
        """One request. The tag is computed before the handler, so a 304 costs no query (P14)."""
        parsed = urlparse(target)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.last_request = time.time()
        if (
            parsed.path == "/"
        ):  # answered before the table, because it names no payload (P13)
            return Reply.html(self.page(query.get("repo")))
        key = (method, self._pattern(parsed.path))
        handler = HANDLERS.get(key)
        if handler is None:
            return Reply.error(404, f"no route for {method} {parsed.path}")
        tag = ""
        if ROUTES[key].etag:
            tag = TAGS[key](self, query)
            if headers.get("If-None-Match") == tag:
                return Reply(status=304, etag=tag)
        reply = handler(self, parsed.path, query, body)
        return reply.model_copy(update={"etag": tag}) if tag else reply

    @staticmethod
    def _pattern(path: str) -> str:
        """The one route with a variable segment, matched by shape rather than by regex.

        ``/api/runs/`` with an empty id matches too, and its handler answers the same 404 an
        unknown id gets.
        """
        if path.startswith("/api/runs/") and path.count("/") == 3:
            return "/api/runs/<id>"
        return path

    def _root(self, query: Mapping[str, str]) -> Path:
        return Path(query.get("repo") or ".")

    def status_tag(self, query) -> str:
        """Restart-unique: a page holding a tag from a dead daemon must not get a 304 (P14)."""
        return f'W/"{self.started_at:.0f}-{self.revision}"'

    def runs_tag(self, query) -> str:
        return self.readers.runs_tag(self._root(query))

    def health(self, path, query, body) -> Reply:
        return Reply.json(
            HealthPayload(
                home=str(self.identity.home),
                db_path=str(self.identity.db_path),
                version=self.identity.version,
                compat=self.identity.compat,
            )
        )

    def api_status(self, path, query, body) -> Reply:
        budget, limits = self.meters()
        payload = StatusPayload(
            home=str(self.identity.home),
            version=self.identity.version,
            compat=self.identity.compat,
            started_at=self.started_at,
            uptime_seconds=time.time() - self.started_at,
            queued_repos=self.queue.pending_keys,
            sessions=tuple(
                SessionPayload(
                    session_id=s.session_id,
                    repo=s.repo,
                    client=s.client,
                    started_at=s.started_at,
                    last_seen=s.last_seen,
                )
                for s in self.sessions.live(now=time.time())
            ),
            budget=budget,
            limits=limits,
        )
        return Reply.json(
            payload
        )  # the tag was already computed and is attached by `dispatch`

    def api_repos(self, path, query, body) -> Reply:
        """The switcher's list, with the session and queue columns only the router can fill."""
        live = self.sessions.live(now=time.time())
        pending = set(self.queue.keys())
        return Reply.json(
            ReposPayload(
                repos=tuple(
                    row.model_copy(
                        update={
                            "attached": any(s.repo == row.repo for s in live),
                            "sessions": sum(1 for s in live if s.repo == row.repo),
                            "queued_repos": int(row.repo_dir_key in pending),
                            "state": self.loop_state(row.repo_dir_key),
                        }
                    )
                    for row in self.readers.repos().repos
                )
            )
        )

    def api_graph(self, path, query, body) -> Reply:
        return Reply.json(self.readers.graph(self._root(query)))

    def api_runs(self, path, query, body) -> Reply:
        return Reply.json(self.readers.runs(self._root(query)))

    def api_runs_detail(self, path, query, body) -> Reply:
        run_id = path.rsplit("/", 1)[-1]
        view = self.readers.run(self._root(query), run_id) if run_id else None
        if view is None:
            return Reply.error(404, f"no run {run_id} in this repo's ledger")
        return Reply.json(view)

    def api_refinements(self, path, query, body) -> Reply:
        return Reply.json(self.readers.refinements(self._root(query)))

    def api_evals(self, path, query, body) -> Reply:
        return Reply.json(self.readers.evals(self._root(query)))

    def api_flow(self, path, query, body) -> Reply:
        return Reply.json(self.readers.flow(self._root(query), query.get("symbol", "")))

    def events(self, path, query, body) -> Reply:
        """Stage 0 through the shape predicate, then spool, then 202. No lock is taken here.

        `FileDiscovery(root)` is built with the default excludes, so a repo's configured `exclude`
        globs do not reach Stage 0 here and their edits still spool; spec 8.2 puts that filter in
        the hook, and S9 is where the repo's own globs arrive.
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
            self.queue.put(
                request.key,
                Event(
                    repo=str(root),
                    paths=kept,
                    kind=request.kind,
                    client=request.client,
                    session_id=request.session_id,
                    at=time.time(),
                ),
            )
        return Reply.json(
            EventAck(
                accepted=len(kept),
                dropped=len(request.paths) - len(kept),
                queued_repos=self.queue.pending_keys,
            ),
            status=202,
        )

    def sessions_attach(self, path, query, body) -> Reply:
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
        reason = self.gate(request)
        if reason:
            return Reply.json(AttachOutcome(attached=False, reason=reason))
        now = time.time()
        self.sessions.attach(
            Session(
                session_id=request.session_id,
                repo=request.repo,
                identity=repo_identity(Path(request.repo)),
                client=request.client,
                started_at=now,
                last_seen=now,
            )
        )
        if not self.opened_page:  # spec 12.1: once per daemon lifetime, on first attach
            self.opened_page = True
            self.open_page(self.url)
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

    def sessions_heartbeat(self, path, query, body) -> Reply:
        ref = self._session_ref(body)
        if isinstance(ref, Reply):
            return ref
        known = self.sessions.heartbeat(ref.session_id, now=time.time())
        return Reply.json(
            SessionAck(ok=known, reason="" if known else "no such session")
        )

    def sessions_detach(self, path, query, body) -> Reply:
        ref = self._session_ref(body)
        if isinstance(ref, Reply):
            return ref
        known = self.sessions.detach(ref.session_id)
        self.bump()  # a session leaving is what the page's badge shows (P14)
        return Reply.json(
            SessionAck(ok=known, reason="" if known else "no such session")
        )

    def admin_restart(self, path, query, body) -> Reply:
        self.restarting = True
        self.bump()
        return Reply.json(RestartAck(restarting=True, reason="wire compat mismatch"))


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
