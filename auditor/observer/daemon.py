"""Spec 8.1's process: the singleton lock, `daemon.json`, the idle timer and the restart exec."""

import http.client
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, ConfigDict

from auditor import __version__
from auditor.config import is_configured, load_config
from auditor.graph.refine.lock import flock_nb
from auditor.graph.viz import render_app_or_status
from auditor.observer import MINUTE, OBSERVER_API_VERSION
from auditor.observer.events import Event, EventQueue
from auditor.observer.payloads import HealthPayload, RestartRequest
from auditor.observer.routes import DaemonIdentity, Readers, Router, RouterDeps
from auditor.observer.scheduling import RunSlots
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
    write_json_dict,
)
from auditor.serve import open_url
from auditor.user_settings import UserSettings, load_home_settings, load_user_settings

_LOG = logging.getLogger("auditor.observer")
#: a loopback request to a process that is either answering or gone; never a wait worth naming
_ASK_TIMEOUT = 2.0


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

    def held_elsewhere(self) -> bool:
        """Whether some other process is the daemon for this home right now."""
        if self._fd is not None:
            return False
        probe = DaemonLock(self.path)
        if not probe.acquire():
            return True
        probe.release()
        return False

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
    record: DaemonRecord, method: str, path: str, body: str = ""
) -> dict | None:
    """One loopback request to a published daemon, or None when nothing answers.

    Liveness is asked for, never taken: probing the flock meant acquiring it, which could take it
    from a daemon that was still starting (E5).
    """
    conn = http.client.HTTPConnection("127.0.0.1", record.port, timeout=_ASK_TIMEOUT)
    try:
        conn.request(method, path, body or None, {"Content-Type": "application/json"})
        answer = json.loads(conn.getresponse().read())
        return answer if isinstance(answer, dict) else None
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        conn.close()


def daemon_health(record: DaemonRecord) -> HealthPayload | None:
    """What the daemon in ``record`` answers on ``/health``, or None when nothing is there."""
    answer = daemon_ask(record, "GET", "/health")
    if answer is None:
        return None
    try:
        return HealthPayload.model_validate(answer)
    except ValueError:
        return None


def daemon_started_at(record: DaemonRecord) -> float:
    """When the daemon on this port started, or 0.0 when nothing answers.

    The pid survives ``os.execv``, so this is what tells a restarted daemon from the one that
    asked for the restart.
    """
    answer = daemon_ask(record, "GET", "/api/status") or {}
    started = answer.get("started_at", 0.0)
    return float(started) if isinstance(started, int | float) else 0.0


def restart_daemon(record: DaemonRecord) -> bool:
    """Ask a daemon whose wire this install does not speak to re-exec. False when it declined."""
    answer = daemon_ask(
        record,
        "POST",
        "/admin/restart",
        RestartRequest(compat=OBSERVER_API_VERSION).model_dump_json(),
    )
    return bool(answer and answer.get("restarting"))


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
        on_change: Callable[[], object] | None = None,
    ) -> None:
        self.queue = queue
        self.sessions = sessions
        self.idle = idle
        self.now = now
        self.consume = consume or self._count
        #: what a state change the page can see calls; `serve` passes the router's tag counter
        self.on_change = on_change or (lambda: None)
        #: spec 8.4's "two globally" is across every loop in one daemon, so the daemon owns it;
        #: no S8b reader, because S8b opens no run (S8c seam 6)
        self.slots = slots or RunSlots()
        #: what `consume` counted; S8c is what fills `StatusPayload.drained_events` from it
        self.drained = 0
        self.stopping = False

    def _count(self, key: str, events: tuple[Event, ...]) -> None:
        """S8b's consumer: count the batch and acknowledge it. S8c replaces this (P29)."""
        self.drained += len(events)

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
        for key in self.queue.keys():  # noqa: SIM118 - EventQueue, not a dict
            events = self.queue.drain(key)
            if events:
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
        """What a caller that named no repo, and has no session to fall back on, still gets."""
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
        ),
        started_at=time.time(),
    )
    daemon = Daemon(queue=queue, sessions=sessions, idle=idle, on_change=router.bump)
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
