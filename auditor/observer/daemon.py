"""Spec 8.1's process: the singleton lock, `daemon.json`, the idle timer and the restart exec."""

import asyncio
import http.client
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Coroutine
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, TypeVar

from loguru import logger
from pydantic import BaseModel, ConfigDict

from auditor import __version__
from auditor.config import AuditorSettings, is_configured, load_config
from auditor.database import IndexStore
from auditor.graph.refine.drive import build_runner, select_runner
from auditor.graph.refine.lock import flock_nb
from auditor.graph.refine.models import RunnerKind
from auditor.graph.refine.service import RefinementService
from auditor.graph.viz import render_app_or_status
from auditor.observer import MINUTE, OBSERVER_API_VERSION
from auditor.observer.events import Event, EventQueue
from auditor.observer.loop import RepoLoop
from auditor.observer.payloads import (
    BudgetPayload,
    HealthPayload,
    RateLimitPayload,
    RestartAck,
    RestartRequest,
    StatusPayload,
)
from auditor.observer.routes import DaemonIdentity, Readers, Router, RouterDeps
from auditor.observer.scheduling import LoopState, QueueFeed, RunSlots
from auditor.observer.server import ObserverServer
from auditor.observer.sessions import AttachRequest, SessionBook, attach_refusal
from auditor.paths import (
    auditor_home,
    daemon_json_path,
    index_db_path,
    is_main_worktree,
    is_repo_dir_key,
    observer_lock_path,
    observer_log_dir,
    observer_port,
    read_json_dict,
    repo_dir_key,
    repo_root_from_key,
    write_json_dict,
)
from auditor.payload import WirePayload
from auditor.serve import open_url
from auditor.user_settings import UserSettings, load_home_settings, load_user_settings

_LOG = logging.getLogger("auditor.observer")
#: Transport fact, not `SchedulingConfig` setting: daemon is answering or gone, no caller waits
_ASK_TIMEOUT = 2.0
#: how long a cancelled loop task gets to unwind before the host thread stops
_CANCEL_GRACE = 0.25
_P = TypeVar("_P", bound=WirePayload)
_T = TypeVar("_T")


class DaemonRecord(BaseModel):
    """`daemon.json`: how a client finds a daemon and decides whether it can talk to it."""

    model_config = ConfigDict(frozen=True)

    pid: int
    port: int
    home: str
    version: str
    compat: int


class DaemonLock:
    """The singleton. Liveness is "can I take this flock", never a pid check (recon Q2).

    The kernel releases the lock when the descriptor closes or the process dies, so the file is
    never stale and is never deleted.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> bool:
        """Take the lock, or return False because another daemon holds it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        if not flock_nb(fd):
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


class IdleTimer:
    """Spec 8.1's idle shutdown, on an injected clock so a test never sleeps 30 minutes."""

    def __init__(self, *, minutes: float, now: float = 0.0) -> None:
        self.seconds = minutes * MINUTE
        self.last = now

    def touch(self, now: float) -> None:
        self.last = now

    def due(self, now: float) -> bool:
        """Whether the daemon has gone the whole window with no request. Zero disables it."""
        return self.seconds > 0 and now - self.last >= self.seconds


def daemon_argv(which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """The install spec the daemon starts and re-execs from, resolved fresh on every call.

    Resolved rather than remembered so `/admin/restart` picks up an upgraded console script; the
    interpreter fallback covers a checkout with no scripts on PATH.
    """
    found = which("auditr")
    if found:
        return [found, "observer", "start", "--foreground"]
    return [sys.executable, "-m", "auditor.cli", "observer", "start", "--foreground"]


def detach(argv: list[str], log_path: Path, spawn=subprocess.Popen) -> int:
    """Start ``argv`` in its own session and return its pid; that child is the daemon.

    No fork: the child is launched from the resolved entry point the restart exec needs anyway, and
    its stderr goes to the daemon log rather than to `/dev/null` (P2, P30).
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as stream:
        child = spawn(
            argv,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stream,
        )
    return int(child.pid)


def wait_for(check: Callable[[], bool], *, timeout: float, poll: float = 0.02) -> bool:
    """Poll ``check`` until it is true or the deadline passes: `start` must not race `ensure`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(poll)
    return check()


def observer_log_handler(path: Path) -> RotatingFileHandler:
    """The daemon's stdlib log sink; the loguru sink is added beside it (P30)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3)


def install_logging(path: Path) -> None:
    """Point both logging stacks at the daemon's own file (P30).

    The package logs through loguru and through stdlib `logging`, and `auditor/__init__` disables
    loguru until something enables it, so a stdlib handler alone would capture at most half.
    """
    stdlib = logging.getLogger("auditor")
    stdlib.setLevel(logging.INFO)
    stdlib.addHandler(observer_log_handler(path))
    logger.add(path, rotation="5 MB", retention=3)
    logger.enable("auditor")


def daemon_ask(
    record: DaemonRecord, method: str, path: str, model: type[_P], body: str = ""
) -> _P | None:
    """One loopback request to a published daemon, read as ``model``, or None when nothing does.

    Liveness is asked for, never taken: probing the flock meant acquiring it, which could take it
    from a daemon that was still starting (E5).
    """
    conn = http.client.HTTPConnection("127.0.0.1", record.port, timeout=_ASK_TIMEOUT)
    try:
        conn.request(method, path, body or None, {"Content-Type": "application/json"})
        return model.model_validate_json(conn.getresponse().read())
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        conn.close()


def daemon_health(record: DaemonRecord) -> HealthPayload | None:
    """What the daemon in ``record`` answers on ``/health``, or None when nothing is there."""
    return daemon_ask(record, "GET", "/health", HealthPayload)


def daemon_started_at(record: DaemonRecord) -> float:
    """When the daemon on this port started, or 0.0 when nothing answers.

    The pid survives ``os.execv``, so this is what tells a restarted daemon from the one that
    asked for the restart.
    """
    status = daemon_ask(record, "GET", "/api/status", StatusPayload)
    return status.started_at if status is not None else 0.0


def restart_daemon(record: DaemonRecord) -> bool:
    """Ask a daemon whose wire this install does not speak to re-exec. False when it declined."""
    ack = daemon_ask(
        record,
        "POST",
        "/admin/restart",
        RestartAck,
        RestartRequest(compat=OBSERVER_API_VERSION).model_dump_json(),
    )
    return ack is not None and ack.restarting


def stop_daemon(record: DaemonRecord) -> bool:
    """Ask the daemon at ``record.pid`` to exit. False when the process is already gone.

    SIGTERM rather than a route: the API has no stop verb, and the handler `serve` installs is what
    turns the signal into a clean release of the lock and of `daemon.json`.
    """
    try:
        os.kill(record.pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def read_daemon_record() -> DaemonRecord | None:
    """What a running daemon published, or None when there is nothing readable to find."""
    try:
        return DaemonRecord.model_validate(read_json_dict(daemon_json_path()))
    except ValueError:
        return None


class LoopHost:
    """The asyncio thread every `RepoLoop` is built and ticked on (spec 8.1, seam 1).

    The daemon's own thread drains and looks a loop up; it never constructs one and never awaits,
    because `IndexStore` is bound to one event loop and that loop is this thread's.
    """

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        """Bring the thread up and wait until its loop is running."""
        self._thread = threading.Thread(target=self._run, name="observer-loops")
        self._thread.daemon = True
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()
            self.loop = None

    def run(self, coro: Coroutine[Any, Any, _T], *, timeout: float = 60.0) -> _T:
        """Run one coroutine on the host thread and wait for its answer."""
        loop = self.loop
        if loop is None:
            coro.close()
            raise RuntimeError("loop host is not running")
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

    def spawn(self, coro: Coroutine[Any, Any, object]) -> None:
        """Start one coroutine on the host thread and do not wait for it."""
        loop = self.loop
        if loop is None:
            coro.close()
            raise RuntimeError("loop host is not running")
        asyncio.run_coroutine_threadsafe(coro, loop)

    def stop(self) -> None:
        """Cancel what is running, stop the loop and join the thread.

        The cancel is what turns a tick killed mid-await into an ordinary `CancelledError` the
        driver unwinds through; stopping a host that never started is a no-op.
        """
        loop, thread = self.loop, self._thread
        if loop is not None:
            loop.call_soon_threadsafe(self._cancel_all, loop)
        if thread is not None:
            thread.join(timeout=5.0)
        self._thread = None
        self._ready.clear()

    @staticmethod
    def _cancel_all(loop: asyncio.AbstractEventLoop) -> None:
        """Cancel every task, then stop once they have all had a turn to unwind."""
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        loop.call_later(_CANCEL_GRACE, loop.stop)


class Daemon:
    """The one background process per home: it drains, sweeps and decides when to stop.

    Takes its parts so a test can drive it with no port, no lock and no process. `consume` is the
    one attribute S8c replaces, and the daemon's own thread calls it rather than S8c polling (P29).
    """

    def __init__(
        self,
        *,
        queue: EventQueue,
        sessions: SessionBook,
        idle: IdleTimer,
        now: Callable[[], float] = time.time,
        consume: Callable[[str, tuple[Event, ...]], object] | None = None,
        slots: RunSlots | None = None,
        host: LoopHost | None = None,
        readers: Readers | None = None,
        settings: UserSettings | None = None,
        on_change: Callable[[], object] | None = None,
    ) -> None:
        self.queue = queue
        self.sessions = sessions
        self.idle = idle
        self.now = now
        self.consume = consume or self.offer
        #: what a state change the page can see calls; `serve` passes the router's tag counter
        self.on_change = on_change or (lambda: None)
        #: spec 8.4's "two globally" is across every loop in one daemon, so the daemon owns it
        self.slots = slots or RunSlots()
        self.host = host or LoopHost()
        self.readers = readers
        self.settings = settings or UserSettings()
        #: one `RepoLoop` per attached repo, keyed by the spool key `consume` is handed (C-2)
        self.loops: dict[str, RepoLoop] = {}
        #: what `/api/status` draws, pushed by the loops rather than pulled across threads (H-9)
        self.meters: tuple[BudgetPayload | None, RateLimitPayload] = (
            None,
            RateLimitPayload(),
        )
        #: what `consume` counted; S8c is what fills `StatusPayload.drained_events` from it
        self.drained = 0
        self.stopping = False

    def offer(self, key: str, events: tuple[Event, ...]) -> int:
        """Hand one drained spool to its repo's loop, or drop it when no loop owns the key.

        A lookup, never a constructor: this runs on the drain thread, which has no event loop, and
        every `RepoLoop` was built on the asyncio thread when its repo attached.
        """
        held = self.loops.get(key)
        if held is None:
            _LOG.warning("dropped %d events for unattached repo %s", len(events), key)
            return 0
        held.feed.offer(events)
        return len(events)

    def loop_state(self, key: str) -> str:
        """What one repo's loop is doing, or "" for a key no loop owns yet (seam 2)."""
        held = self.loops.get(key)
        return held.state.value if held is not None else ""

    def reconcile(self) -> None:
        """Give every live session's repo, and every adopted spool, a loop on the host thread.

        Before the drain rather than after, so the first batch a newly attached repo posts finds
        a feed waiting for it instead of being dropped.
        """
        if self.readers is None:
            return
        roots = {Path(s.repo) for s in self.sessions.live(now=self.now())}
        for key in self.queue.keys():  # noqa: SIM118 - EventQueue, not a dict
            if key not in self.loops:
                adopted = repo_root_from_key(key)
                if adopted is not None:
                    roots.add(adopted)
        for root in sorted(roots, key=str):
            self.ensure_loop(root)

    def ensure_loop(self, root: Path) -> str:
        """Build this repo's loop if it has none, and answer its spool key.

        The two handles are resolved here, on the drain thread: `Readers.index` opens its store
        through `asyncio.run`, which raises on a thread that is already running a loop.
        """
        key = repo_dir_key(root)
        if key in self.loops:
            return key
        readers = self.readers
        if readers is None:
            return key
        try:
            settings = readers.config(root)
            index = readers.index(root)
            self.loops[key] = self.host.run(self._build(root, index, settings))
        except Exception:
            _LOG.exception("could not start a loop for %s", root)
            return key
        self.host.spawn(self._drive(self.loops[key]))
        return key

    async def _build(
        self, root: Path, index: IndexStore, settings: AuditorSettings
    ) -> RepoLoop:
        """One `RepoLoop`, constructed on the host thread that will own every await it makes."""
        service = RefinementService(index, root, settings, self.settings)
        return RepoLoop(
            root=root,
            index=index,
            settings=settings,
            user=self.settings,
            feed=QueueFeed(),
            runner_for=self._runner_for,
            service=service,
            slots=self.slots,
            on_change=self.on_change,
        )

    def _runner_for(self, service: RefinementService):
        """The runner this daemon opens observer runs with, or the fake when none is available."""
        choice = select_runner(self.settings.observer.runner)
        return build_runner(choice.kind or RunnerKind.FAKE, service)

    async def _drive(self, loop: RepoLoop) -> None:
        """Spec 8.3's ladder for one repo: attach once, then tick until the daemon stops."""
        try:
            await loop.attach()
            await self._publish(loop)
            while not self.stopping:
                await loop.tick(poll=self.settings.observer.scheduling.tick_seconds)
                await self._publish(loop)
        except asyncio.CancelledError:
            _LOG.info("loop for %s cancelled at shutdown", loop.root)
        finally:
            loop.detach()

    async def _publish(self, loop: RepoLoop) -> None:
        """Push this loop's two meters onto the daemon, which is what `/api/status` reads (H-9)."""
        self.meters = (
            BudgetPayload.of(await loop.budget()),
            RateLimitPayload(
                max_utilization=self.settings.observer.budget.max_utilization,
                paused=loop.state
                in {LoopState.PAUSED_RATELIMIT, LoopState.PAUSED_AUTH},
                resumes_at=loop.pauses.resumes_at,
            ),
        )

    def adopt_home(self) -> int:
        """Spec 8.1's start-time drain: every spool under this home becomes a pending key.

        The `repos/<key>` directories name themselves, and a directory whose name is not a key
        cannot hold one of this daemon's spools.
        """
        repos = auditor_home() / "repos"
        if not repos.is_dir():
            return 0
        return self.queue.adopt(
            sorted(
                e.name
                for e in repos.iterdir()
                if e.is_dir() and is_repo_dir_key(e.name)
            )
        )

    def tick(self) -> None:
        """Drain every pending key into `consume`, sweep expired sessions, decide about stopping."""
        self.reconcile()
        for key in self.queue.keys():  # noqa: SIM118 - EventQueue, not a dict
            events = self.queue.drain(key)
            if events:
                self.drained += len(events)
                self.consume(key, events)
            # the staged batch is dropped only once its consumer has returned (P26)
            self.queue.consumed(key)
        now = self.now()
        if self.sessions.sweep(now=now):
            self.on_change()  # an expired session is a badge change the page has to see
        if self.idle.due(now) and not self.sessions.live(now=now):
            self.stopping = True


def repo_gate(
    daemon_home: Path, settings: UserSettings
) -> Callable[[AttachRequest], str]:
    """Spec 8.2's five-clause AND gate, bound to this daemon's home and the user's settings."""

    def gate(request: AttachRequest) -> str:
        root = Path(request.repo)
        try:
            allowed = load_config(root).observer_allowed
        except (
            Exception
        ):  # a repo whose config will not load cannot consent to being observed
            _LOG.exception("could not read %s's config; refusing the attach", root)
            allowed = False
        try:  # this repo's own overlay wins over the daemon's home-level answer
            personal = load_user_settings(root)
        except Exception:
            _LOG.exception(
                "could not read %s's user settings; using the daemon's", root
            )
            personal = settings
        return attach_refusal(
            home=Path(request.home) if request.home else None,
            daemon_home=daemon_home,
            configured=is_configured(root),
            observer_allowed=allowed,
            enabled=personal.observer.enabled,
            worktrees=personal.observer.worktrees,
            main_worktree=is_main_worktree(root),
        )

    return gate


def repo_page(readers: Readers) -> Callable[[str | None], str]:
    """The page at `/`: the built UI for one repo, or the status document with no bundle (P16)."""

    def page(repo: str | None) -> str:
        """Render this repo's document, or an empty one when the query named no repo at all."""
        empty: dict[str, list] = {"nodes": [], "edges": [], "clusters": []}
        return render_app_or_status(readers.graph(Path(repo)).graph if repo else empty)

    return page


def serve(settings: UserSettings | None = None) -> int:
    """Spec 8.1's foreground daemon: take the lock, publish, serve, drain, and stop or re-exec.

    Returns 0 when another process already holds the lock, which makes a second `start` a report
    rather than a second daemon.
    """
    lock = DaemonLock(observer_lock_path())
    if not lock.acquire():
        return 0
    install_logging(observer_log_dir() / "observer.log")
    settings = settings or load_home_settings()
    home = auditor_home()
    scheduling = settings.observer.scheduling
    readers = Readers(settings=settings)
    queue = EventQueue()
    sessions = SessionBook(expiry_minutes=scheduling.session_expiry_minutes)
    idle = IdleTimer(minutes=scheduling.idle_shutdown_minutes, now=time.time())
    daemon = Daemon(
        queue=queue,
        sessions=sessions,
        idle=idle,
        readers=readers,
        settings=settings,
    )
    router = Router(
        RouterDeps(
            identity=DaemonIdentity(
                home=home,
                db_path=index_db_path(),
                version=__version__,
                compat=OBSERVER_API_VERSION,
            ),
            queue=queue,
            sessions=sessions,
            readers=readers,
            page=repo_page(readers),
            gate=repo_gate(home, settings),
            open_page=open_url if settings.observer.open_browser else lambda url: None,
            loop_state=daemon.loop_state,
            meters=lambda: daemon.meters,
        ),
        started_at=time.time(),
    )
    # late-bound like `router.url`: `RouterDeps` is frozen, so the daemon is built first and
    # takes the router's tag counter once there is a router to take it from
    daemon.on_change = router.bump
    daemon.adopt_home()
    try:
        server = ObserverServer(router.dispatch, port=observer_port())
    except (
        OSError
    ):  # the port rule is a hash over 500 slots, so a collision is not exotic
        _LOG.exception("observer could not bind its port; not starting")
        readers.close()
        lock.release()
        return 1

    router.url = server.url
    server.start()
    daemon.host.start()
    write_json_dict(
        daemon_json_path(),
        DaemonRecord(
            pid=os.getpid(),
            port=server.port,
            home=str(home),
            version=__version__,
            compat=OBSERVER_API_VERSION,
        ).model_dump(),
    )
    _LOG.info("observer listening on %s", server.url)

    def stop(signum: int, frame: object) -> None:
        daemon.stopping = True

    try:
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
    except ValueError:  # not the main thread, so whoever started us owns the signals
        pass
    try:
        while not daemon.stopping and not router.restarting:
            queue.wait(scheduling.tick_seconds)
            if router.last_request > idle.last:
                idle.touch(router.last_request)
            daemon.tick()
    finally:
        daemon.stopping = True
        daemon.host.stop()
        server.stop()
        readers.close()
        daemon_json_path().unlink(missing_ok=True)
        lock.release()  # released before the exec, so the new daemon can take it
    if router.restarting:
        # no drain window: every accepted event is already spooled and the next daemon adopts it
        _LOG.info("observer re-execing its install spec")
        argv = daemon_argv()
        os.execv(argv[0], argv)
    return 0
