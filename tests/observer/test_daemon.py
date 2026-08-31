"""Spec 8.1's process: the singleton, `daemon.json`, the idle window and the install spec."""

import asyncio
import http.client
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import auditr_observer
from auditor.cli import observer as cli_observer
from auditor.config import AuditorSettings
from auditor.database import open_repo_index
from auditor.observer import daemon as daemon_module
from auditor.observer.daemon import (
    LOGGED_REFUSALS,
    Daemon,
    DaemonLock,
    DaemonRecord,
    IdleTimer,
    LoopHost,
    daemon_argv,
    daemon_started_at,
    detach,
    read_daemon_record,
    serve,
    wait_for,
)
from auditor.observer.events import Event, EventQueue, Spool
from auditor.observer.payloads import DaemonStatus
from auditor.observer.routes import Readers, Router
from auditor.observer.scheduling import LoopState
from auditor.observer.sessions import AttachRequest, Session, SessionBook
from auditor.paths import (
    auditor_home,
    daemon_json_path,
    ensure_repo_dir,
    observer_port,
    repo_dir_from_key,
    repo_dir_key,
    write_json_dict,
)
from auditor.user_settings import SchedulingConfig, UserSettings


@pytest.fixture
def queue(tmp_path: Path) -> EventQueue:
    return EventQueue(lambda key: tmp_path / "repos" / key / "spool.jsonl")


def _daemon(queue: EventQueue, *, minutes: float = 30.0) -> Daemon:
    """A daemon over injected parts: no port, no lock, no process."""
    clock = {"now": 0.0}
    daemon = Daemon(
        queue=queue,
        sessions=SessionBook(expiry_minutes=45),
        idle=IdleTimer(minutes=minutes, now=0.0),
        now=lambda: clock["now"],
    )
    daemon.clock = clock
    return daemon


def _session(**kw) -> Session:
    base = {
        "session_id": "s1",
        "repo": "/r",
        "identity": "/r/.git",
        "started_at": 0.0,
        "last_seen": 0.0,
    }
    return Session(**{**base, **kw})


def test_a_second_daemon_cannot_take_the_lock(tmp_path):
    """Liveness is the flock, so a pid rollover can never make a dead daemon look alive."""
    first, second = DaemonLock(tmp_path / "lock"), DaemonLock(tmp_path / "lock")
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_the_lock_file_survives_its_holder_and_is_never_deleted(tmp_path):
    """`lock.py` sets the precedent: the kernel frees a flock, so nothing sweeps the file."""
    lock = DaemonLock(tmp_path / "lock")
    assert lock.acquire() is True
    lock.release()
    assert (tmp_path / "lock").exists()


def test_daemon_json_round_trips_through_the_atomic_writer(tmp_path, monkeypatch):
    """A torn `daemon.json` degrades to `{}` rather than failing a hook (paths.read_json_dict)."""
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    record = DaemonRecord(
        pid=os.getpid(), port=7682, home=str(tmp_path), version="0.10.5", compat=1
    )
    daemon_json_path().parent.mkdir(parents=True, exist_ok=True)
    write_json_dict(daemon_json_path(), record.model_dump())
    assert (
        DaemonRecord.model_validate(json.loads(daemon_json_path().read_text()))
        == record
    )
    assert set(record.model_dump()) == {"pid", "port", "home", "version", "compat"}


@pytest.mark.parametrize(
    ("minutes", "elapsed", "expected"),
    [
        (30, 30 * 60 - 1, False),
        (30, 30 * 60, True),
        (30, 30 * 60 + 1, True),
        (0, 10_000_000.0, False),
    ],
)
def test_the_idle_timer_fires_on_the_configured_window(minutes, elapsed, expected):
    """Spec 8.1's idle shutdown, on an injected clock; zero minutes disables it."""
    timer = IdleTimer(minutes=minutes, now=0.0)
    assert timer.due(elapsed) is expected


def test_a_request_pushes_the_idle_deadline_out():
    timer = IdleTimer(minutes=30, now=0.0)
    timer.touch(29 * 60)
    assert timer.due(30 * 60) is False
    assert timer.due(59 * 60) is True


def test_the_install_spec_is_resolved_fresh_and_falls_back_to_the_interpreter():
    """Spec 8.1's "execs the new version from its own install spec" (spec 8.1, P3).

    P3 overrides recon Q8's `shutil.which("auditr-observer")` option: the console script may not
    import `auditor` and therefore cannot serve.
    """
    assert daemon_argv(lambda name: "/usr/local/bin/auditr") == [
        "/usr/local/bin/auditr",
        "observer",
        "start",
        "--foreground",
    ]
    assert daemon_argv(lambda name: None) == [
        sys.executable,
        "-m",
        "auditor.cli",
        "observer",
        "start",
        "--foreground",
    ]


def test_the_daemon_adopts_a_spool_a_killed_predecessor_left(
    queue, tmp_path, monkeypatch
):
    """Spec 8.1's start-time drain is the same code path as a live one, so it is exercised.

    A `repos/` entry whose name is not a `repo_dir_key` is not one of this daemon's spools and
    is not adopted, because `spool_path` refuses to resolve it at all.
    """
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    key = "a" * 40
    Spool(tmp_path / "repos" / key / "spool.jsonl").append(
        Event(repo="/r", paths=("a.py",), at=1.0)
    )
    (tmp_path / "repos" / "not-a-key").mkdir()
    (tmp_path / "repos" / ("b" * 40)).mkdir()  # a repo dir with no spool left behind
    daemon = _daemon(queue)
    assert daemon.adopt_home() == 1
    assert queue.keys() == (key,)


def test_one_tick_hands_the_drained_batch_to_the_consumer(queue):
    """`consume` is the one attribute S8c replaces, so S8b pins what it is handed (P29)."""
    seen: list[tuple[str, tuple[Event, ...]]] = []
    daemon = _daemon(queue)
    daemon.consume = lambda key, events: seen.append((key, events))
    queue.put("k", Event(repo="/r", paths=("a.py",), at=1.0))
    daemon.tick()
    assert [(key, [e.paths for e in events]) for key, events in seen] == [
        ("k", [("a.py",)])
    ]
    assert queue.keys() == ()


def test_a_tick_drops_the_staged_batch_only_after_its_consumer_returns(queue, tmp_path):
    """The staged file is what a killed daemon's successor adopts, so only the tick clears it."""
    staged = tmp_path / "repos" / "k" / "spool.draining"
    seen: list[str] = []

    def consume(key: str, events: tuple[Event, ...]) -> None:
        assert staged.exists()  # still on disk while the consumer holds the batch
        seen.append(key)

    daemon = _daemon(queue)
    daemon.consume = consume
    queue.put("k", Event(repo="/r", paths=("a.py",), at=1.0))
    daemon.tick()
    assert seen == ["k"]
    assert not staged.exists()


def test_the_default_consumer_counts_what_it_drained(queue):
    """S8b's consumer counts and acknowledges; `drained_events` on the wire is this number."""
    daemon = _daemon(queue)
    queue.put("k", Event(repo="/r", paths=("a.py",), at=1.0))
    queue.put("k", Event(repo="/r", paths=("b.py",), at=2.0))
    daemon.tick()
    assert daemon.drained == 2


def test_a_tick_drops_an_expired_session_and_says_the_badge_moved(queue):
    """`live` filters on read, so only a second sweep finding nothing proves the tick swept."""
    changed: list[int] = []
    daemon = _daemon(queue)
    daemon.on_change = lambda: changed.append(1)
    daemon.sessions.attach(_session())
    daemon.clock["now"] = 45 * 60 + 1
    daemon.tick()
    assert daemon.sessions.live(now=daemon.clock["now"]) == ()
    assert daemon.sessions.sweep(now=daemon.clock["now"]) == 0
    assert changed == [1]


def test_an_idle_daemon_with_no_live_session_stops(queue):
    """Both halves: the window has passed and nothing is attached (spec 8.1)."""
    daemon = _daemon(queue, minutes=1.0)
    daemon.sessions.attach(_session(last_seen=10_000.0))
    daemon.clock["now"] = 61.0
    daemon.tick()
    assert daemon.stopping is False  # a live session holds the daemon open
    daemon.sessions.detach("s1")
    daemon.tick()
    assert daemon.stopping is True


def test_a_zero_idle_window_never_stops_the_daemon(queue):
    """`ge=0.0` is what makes this reachable from settings at all (P28)."""
    daemon = _daemon(queue, minutes=0.0)
    daemon.clock["now"] = 10_000_000.0
    daemon.tick()
    assert daemon.stopping is False


def test_start_launches_a_session_leader_and_returns_its_real_pid(tmp_path):
    """No fork, so the pid is the daemon's own and stderr reaches the log (P2, P30)."""
    calls: list[dict] = []

    class FakeChild:
        pid = 4242

    def spawn(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        return FakeChild()

    log = tmp_path / "log" / "observer.log"
    assert (
        detach(["auditr", "observer", "start", "--foreground"], log, spawn=spawn)
        == 4242
    )
    assert calls[0]["start_new_session"] is True
    assert calls[0]["stdin"] is subprocess.DEVNULL
    assert calls[0]["stdout"] is subprocess.DEVNULL
    assert calls[0]["stderr"] is not subprocess.DEVNULL
    assert log.exists()


def test_a_real_child_leads_its_own_session(tmp_path):
    """`start_new_session` is what survives the shell's SIGHUP; the observable is the sid."""
    log = tmp_path / "log" / "observer.log"
    marker = tmp_path / "who.txt"
    script = (
        "import os, pathlib, sys; "
        "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()} {os.getsid(0)}')"
    )
    pid = detach([sys.executable, "-c", script, str(marker)], log)
    assert wait_for(marker.exists, timeout=10.0)
    reported_pid, sid = (int(part) for part in marker.read_text().split())
    assert reported_pid == pid
    assert (
        sid == pid
    )  # it leads its own session, so the launching shell cannot signal it


def test_no_subcommand_prints_usage_and_exits_zero(capsys):
    """`main([])` is a live case in `test_client.py`; with real verbs it says what they are."""
    assert auditr_observer.main([]) == 0
    err = capsys.readouterr().err
    assert "usage: auditr-observer" in err
    assert "not available in this release" not in err


def test_the_hook_verb_has_a_body_and_still_cannot_fail_a_session(monkeypatch, capsys):
    """S9 gave the branch a body; what survives from S8b is that it still exits 0 and stays quiet.

    Spying on `_hook` rather than only reading the exit code: `main` swallows everything, so
    `main(...) == 0` holds with the whole dispatch deleted, and the notice it also checks is a
    statement about argparse rather than about this branch.
    """
    called: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        auditr_observer,
        "_hook",
        lambda event, client, payload: called.append((event, client, payload)) or 0,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert auditr_observer.main(["hook", "stop", "--client", "claude-code"]) == 0
    assert called == [("stop", "claude-code", {})]
    assert "not available in this release" not in capsys.readouterr().err


def test_the_client_resolves_the_same_home_as_the_package(tmp_path, monkeypatch):
    """The statusline's precedent: a stdlib re-implementation is pinned against its twin (P21)."""
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "elsewhere"))
    assert auditr_observer.home() == auditor_home()
    monkeypatch.delenv("AUDITOR_HOME")
    monkeypatch.setenv(
        "HOME", str(tmp_path)
    )  # both sides fall back, and not to the developer's
    assert auditr_observer.home() == auditor_home() == tmp_path / ".auditor"


@pytest.mark.parametrize(
    "argv", [["ensure"], ["start"], ["stop"], ["status"], ["open"]]
)
def test_the_kill_switch_makes_every_verb_a_no_op(argv, monkeypatch, capsys):
    """Spec 8.1 and 14: `AUDITOR_OBSERVER=0` disables everything, and nothing exits non-zero."""
    monkeypatch.setenv("AUDITOR_OBSERVER", "0")
    assert auditr_observer.main(argv) == 0
    assert "AUDITOR_OBSERVER=0" in capsys.readouterr().err


def test_the_client_and_the_model_name_the_same_status_keys():
    """`auditr-observer status --json` and `auditr observer status --json` are one shape (P19)."""
    assert set(auditr_observer.STATUS_KEYS) == set(DaemonStatus.model_fields)


def test_the_daemon_starts_publishes_answers_and_stops_on_its_own_idle_window(
    tmp_path, monkeypatch, restored_logging
):
    """The whole process end to end, which is the only thing that catches a broken `serve` (P25).

    Regression: `serve` resolved its own settings through `load_user_settings`, which takes a repo
    root the daemon does not have, so every start died with a `TypeError` before it bound a port.
    """
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    monkeypatch.delenv("AUDITOR_OBSERVER_PORT", raising=False)
    hashed = observer_port()
    # an ephemeral port, because the hashed one collides with a real daemon on a colliding home
    monkeypatch.setenv("AUDITOR_OBSERVER_PORT", "0")

    monkeypatch.setenv(
        "AUDITOR_USER_OBSERVER__SCHEDULING__IDLE_SHUTDOWN_MINUTES", "0.02"
    )
    monkeypatch.setenv("AUDITOR_USER_OBSERVER__OPEN_BROWSER", "false")
    exit_code: dict[str, int] = {}
    worker = threading.Thread(
        target=lambda: exit_code.update(code=serve()), daemon=True
    )
    worker.start()
    try:
        assert wait_for(daemon_json_path().exists, timeout=15.0)
        record = read_daemon_record()
        assert record is not None
        assert record.pid == os.getpid()
        assert record.port not in (0, hashed)  # the kernel chose it, not the port rule
        assert set(record.model_dump()) == {"pid", "port", "home", "version", "compat"}

        conn = http.client.HTTPConnection("127.0.0.1", record.port, timeout=5)
        conn.request("GET", "/health")
        health = json.loads(conn.getresponse().read())
        conn.close()
        assert health["home"] == str(tmp_path)
        assert health["compat"] == record.compat
    finally:
        worker.join(timeout=30.0)
    assert (
        not worker.is_alive()
    )  # the idle window is what ended it, with nothing attached
    assert exit_code["code"] == 0
    assert not daemon_json_path().exists()  # and it cleaned up after itself


def _ask(port: int, method: str, path: str, body: object = None) -> dict:
    """One request against a live daemon, answered as the JSON document it sent."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    payload = None if body is None else json.dumps(body)
    conn.request(method, path, payload)
    answer = json.loads(conn.getresponse().read())
    conn.close()
    return answer


def test_a_running_daemon_reports_the_events_it_drained(
    tmp_path, monkeypatch, restored_logging
):
    """`drained_events` shipped declared and always 0, because only `serve` joins its two halves.

    The counter is the daemon's, on the drain thread; the wire is the router's, on a request
    thread. A test on either side alone passes with `serve` never handing one to the other.
    """
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    monkeypatch.setenv("AUDITOR_OBSERVER_PORT", "0")
    monkeypatch.setenv(
        "AUDITOR_USER_OBSERVER__SCHEDULING__IDLE_SHUTDOWN_MINUTES", "0.02"
    )
    monkeypatch.setenv("AUDITOR_USER_OBSERVER__OPEN_BROWSER", "false")
    src = tmp_path / "src"
    src.mkdir()
    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        assert wait_for(daemon_json_path().exists, timeout=15.0)
        record = read_daemon_record()
        assert record is not None
        assert _ask(record.port, "GET", "/api/status")["drained_events"] == 0
        spooled = _ask(
            record.port,
            "POST",
            "/events",
            {"repo": str(src), "key": "a" * 40, "paths": ["a.py"]},
        )
        assert spooled["accepted"] == 1
        assert wait_for(
            lambda: _ask(record.port, "GET", "/api/status")["drained_events"] == 1,
            timeout=15.0,
        )
    finally:
        worker.join(timeout=30.0)


def test_a_second_serve_returns_at_once_because_the_lock_is_held(tmp_path, monkeypatch):
    """The singleton: a second start reports the running daemon rather than binding a port."""
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    lock = DaemonLock(tmp_path / "observer" / "lock")
    assert lock.acquire() is True
    try:
        assert serve() == 0
        assert not daemon_json_path().exists()
    finally:
        lock.release()


def test_a_port_collision_ends_the_start_and_gives_back_the_lock_and_the_handles(
    tmp_path, monkeypatch, restored_logging
):
    """The bind sat outside the try/finally, so a taken port escaped holding lock and readers.

    The stub stands in for the real `close`, which has nothing to release this early; what is
    asserted is that the failure path calls it at all, the way the success path does.
    """
    closed: list[str] = []
    monkeypatch.setattr(Readers, "close", lambda self: closed.append("readers"))
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        monkeypatch.setenv("AUDITOR_OBSERVER_PORT", str(taken.getsockname()[1]))
        assert serve() == 1
    assert closed == ["readers"]
    assert not daemon_json_path().exists()

    lock = DaemonLock(tmp_path / "observer" / "lock")
    assert lock.acquire() is True
    lock.release()


def test_a_started_daemon_shares_the_gate_the_router_answers_from(
    tmp_path, monkeypatch, restored_logging
):
    """Spec 8.2's gate reaches `Daemon` through one keyword in `serve`, and nothing pinned it.

    Deleting `gate=gate` there dropped every adopted spool back to `Daemon.__init__`'s permissive
    default with the whole suite still green, because the only gate test assigned `daemon.gate`
    by hand. Driven through the real `serve` instead: the port is taken first, so the run ends at
    the bind, after both objects are built. The identity is the ruling's own words - one gate,
    shared with the router - and the refusal is what says it is a real gate and not the fallback
    (H2).
    """
    built: dict[str, object] = {}
    make_daemon, make_router = daemon_module.Daemon, daemon_module.Router

    def recorded_daemon(**kwargs: object) -> Daemon:
        built["daemon"] = daemon = make_daemon(**kwargs)
        return daemon

    def recorded_router(deps: object, **kwargs: object) -> Router:
        built["router"] = router = make_router(deps, **kwargs)
        return router

    monkeypatch.setattr(daemon_module, "Daemon", recorded_daemon)
    monkeypatch.setattr(daemon_module, "Router", recorded_router)
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    unconfigured = tmp_path / "unconfigured"
    unconfigured.mkdir()
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        monkeypatch.setenv("AUDITOR_OBSERVER_PORT", str(taken.getsockname()[1]))
        assert serve() == 1
    daemon, router = built["daemon"], built["router"]
    assert daemon.gate is router.deps.gate
    assert daemon.gate(
        AttachRequest(repo=str(unconfigured), session_id="", home=str(tmp_path))
    )


def test_the_client_and_the_settings_name_the_same_lifecycle_timeouts():
    """The client may not import `auditor`, so its two literals are pinned like `_OFF` is (E6)."""
    scheduling = SchedulingConfig()
    assert scheduling.start_timeout_seconds == auditr_observer._START_TIMEOUT
    assert scheduling.stop_timeout_seconds == auditr_observer._STOP_TIMEOUT


def test_the_started_at_probe_reads_the_live_clock_and_not_a_default(
    daemon_server, daemon_router, tmp_path
):
    """`_restarted` waits on this number, so a constant would end the wait before the exec ran."""
    server, _ = daemon_server
    record = DaemonRecord(
        pid=os.getpid(),
        port=server.port,
        home=str(tmp_path),
        version="0.10.5",
        compat=1,
    )
    assert daemon_started_at(record) == daemon_router.started_at
    daemon_router.started_at += 1.0
    assert daemon_started_at(record) == daemon_router.started_at
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        gone = free.getsockname()[1]
    assert daemon_started_at(record.model_copy(update={"port": gone})) == 0.0


def _replace_when_restarting(router, port: int, home: Path) -> None:
    """Stand in for the `os.execv`: a daemon of this install's version takes the port over.

    Only `started_at` distinguishes it, because the pid survives the exec.
    """
    for _ in range(1_500):
        if router.restarting:
            router.started_at += 1.0
            _speaks(router, 1)
            _published(port, home, compat=1)
            return
        time.sleep(0.01)


def _published(port: int, home: Path, *, compat: int) -> None:
    """A `daemon.json` for a live server, declaring a wire version of the caller's choosing."""
    daemon_json_path().parent.mkdir(parents=True, exist_ok=True)
    write_json_dict(
        daemon_json_path(),
        DaemonRecord(
            pid=os.getpid(),
            port=port,
            home=str(home),
            version="0.10.5",
            compat=compat,
        ).model_dump(),
    )


def _speaks(router, compat: int) -> None:
    router.deps = router.deps.model_copy(
        update={"identity": router.deps.identity.model_copy(update={"compat": compat})}
    )


def test_the_mount_ensure_restarts_a_daemon_whose_wire_it_cannot_speak(
    daemon_server, daemon_router, tmp_path, monkeypatch, capsys
):
    """`ensure` reported the mismatch, returned `running: true` and left it running (H4)."""
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    server, _ = daemon_server
    _speaks(daemon_router, 99)
    _published(server.port, tmp_path, compat=99)
    worker = threading.Thread(
        target=lambda: _replace_when_restarting(daemon_router, server.port, tmp_path),
        daemon=True,
    )
    worker.start()
    cli_observer.ensure(json_=True)
    worker.join(timeout=15.0)
    payload = json.loads(capsys.readouterr().out)
    assert (
        daemon_router.restarting is True
    )  # the mount really asked, rather than reporting
    assert payload["action"] == "restarted"
    assert payload["compat"] == 1


def test_the_client_ensure_restarts_a_daemon_whose_wire_it_cannot_speak(
    daemon_server, daemon_router, tmp_path, monkeypatch, capsys
):
    """P19 makes the two front doors one surface, and the client never compared compat at all."""
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    server, _ = daemon_server
    _speaks(daemon_router, 99)
    _published(server.port, tmp_path, compat=99)
    worker = threading.Thread(
        target=lambda: _replace_when_restarting(daemon_router, server.port, tmp_path),
        daemon=True,
    )
    worker.start()
    assert auditr_observer.main(["ensure"]) == 0
    worker.join(timeout=15.0)
    payload = json.loads(capsys.readouterr().out)
    assert daemon_router.restarting is True
    assert payload["action"] == "restarted"
    assert payload["compat"] == 1


def test_a_non_http_listener_on_the_recorded_port_still_exits_zero(
    tmp_path, monkeypatch, capsys
):
    """A recycled port can put anything where the daemon was, and a hook may never fail (F5)."""
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        _published(listener.getsockname()[1], tmp_path, compat=1)

        def answer() -> None:
            conn, _ = listener.accept()
            with conn:
                conn.recv(4096)
                conn.sendall(b"NOT-HTTP\r\n\r\n")

        worker = threading.Thread(target=answer, daemon=True)
        worker.start()
        assert auditr_observer.main(["status"]) == 0
        worker.join(timeout=10.0)
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is False
    assert payload["action"] == "not running"


def test_a_drained_batch_is_offered_to_the_repo_s_loop_and_a_keyless_one_is_dropped(
    queue,
):
    """S8b's `consume` counted; this slice hands the batch to the loop that decides (P1, C-2)."""
    offered: list[tuple[Event, ...]] = []
    daemon = _daemon(queue)
    daemon.loops["k"] = SimpleNamespace(feed=SimpleNamespace(offer=offered.append))
    queue.put("k", Event(repo="/r", paths=("a.py",), at=1.0))
    queue.put("gone", Event(repo="/elsewhere", paths=("b.py",), at=2.0))
    daemon.tick()
    assert [e.paths[0] for batch in offered for e in batch] == ["a.py"]
    assert daemon.drained == 2


def test_reconcile_gives_a_live_session_and_an_adopted_spool_a_loop(
    queue, tmp_path, monkeypatch
):
    """Its two jobs, which the `offer` tests reach past by seeding `daemon.loops` themselves."""
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    adopted = tmp_path / "elsewhere"
    adopted.mkdir()
    key = repo_dir_key(adopted)
    ensure_repo_dir(adopted)
    write_json_dict(repo_dir_from_key(key) / "root.json", {"root": str(adopted)})
    daemon = _daemon(queue)
    daemon.readers = SimpleNamespace()
    built: list[Path] = []
    daemon.ensure_loop = built.append
    daemon.sessions.attach(_session())
    queue.put(key, Event(repo=str(adopted), paths=("a.py",), at=1.0))
    daemon.reconcile()
    assert set(built) == {Path("/r"), adopted}


def test_an_adopted_spool_only_gets_a_loop_when_the_gate_lets_it(
    queue, tmp_path, monkeypatch
):
    """The client's spool plus its `root.json` crumb is a second door into this daemon.

    Nothing on the adoption path consulted spec 8.2's gate, so a repo that never opted in - no
    `[tool.auditor]`, `observer_allowed = false`, `observer.enabled = false` - got a loop, a
    built graph and a rendered `graph` status block by spooling one edit at a daemon that was
    not running. The spool is left on disk rather than drained into nothing, so a daemon whose
    gate answers differently still finds it.
    """
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    adopted = tmp_path / "unconfigured"
    adopted.mkdir()
    key = repo_dir_key(adopted)
    ensure_repo_dir(adopted)
    write_json_dict(repo_dir_from_key(key) / "root.json", {"root": str(adopted)})
    daemon = _daemon(queue)
    daemon.gate = lambda request: "the repo is not configured for auditor"
    daemon.readers = SimpleNamespace()
    built: list[Path] = []
    daemon.ensure_loop = built.append
    queue.put(key, Event(repo=str(adopted), paths=("a.py",), at=1.0))
    spooled = queue.spool(key).path
    daemon.reconcile()
    assert built == []
    assert queue.keys() == ()
    assert spooled.exists()
    assert daemon.ungated[key] == "the repo is not configured for auditor"


def test_the_gate_refusal_log_forgets_keys_rather_than_growing_for_ever(
    queue, tmp_path, monkeypatch
):
    """A refused key never gets a loop, so `retire` never reaches it and nothing else did (L5).

    The map exists only to keep one refusal out of every tick's log, so the oldest entry costs a
    repeated line and nothing more; unbounded, it was one entry per refused repo for the
    daemon's life.
    """
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path))
    daemon = _daemon(queue)
    daemon.gate = lambda request: "the repo is not configured for auditor"
    keys = [f"{n:040x}" for n in range(LOGGED_REFUSALS + 5)]
    for n, key in enumerate(keys):
        assert daemon._adoptable(key, tmp_path / f"r{n}") is False
    assert len(daemon.ungated) == LOGGED_REFUSALS
    assert keys[0] not in daemon.ungated
    assert keys[-1] in daemon.ungated


def test_reconcile_retires_the_loop_of_a_repo_that_is_neither_live_nor_queued(queue):
    """M5: nothing else unclaims a key, so an expired repo would keep spending for ever."""
    daemon = _daemon(queue)
    daemon.readers = SimpleNamespace()
    daemon.ensure_loop = lambda root: None
    daemon.loops["gone"] = SimpleNamespace(root=Path("/gone"))
    daemon.reconcile()
    assert daemon.loops == {}


def test_a_repo_whose_loop_will_not_build_backs_off_instead_of_retrying_every_tick(
    queue,
):
    """M9: `reconcile` runs once a tick, so a broken repo would write a traceback that often."""
    tries: list[float] = []

    class _Broken:
        def config(self, root: Path):
            tries.append(0.0)
            raise RuntimeError("this repo's config will not load")

    daemon = _daemon(queue)
    daemon.readers = _Broken()
    for _ in range(3):
        daemon.ensure_loop(Path("/r"))
    assert len(tries) == 1
    daemon.clock["now"] = 10_000.0
    daemon.ensure_loop(Path("/r"))
    assert len(tries) == 2


def test_a_drain_that_moved_the_counter_moves_the_status_etag(queue):
    """M10: `drained_events` is on the page, and a 304 would freeze it at whatever it was."""
    bumps: list[int] = []
    daemon = _daemon(queue)
    daemon.on_change = lambda: bumps.append(1)
    daemon.tick()
    assert bumps == []
    queue.put("k", Event(repo="/r", paths=("a.py",), at=1.0))
    daemon.tick()
    assert bumps == [1]


def test_the_loop_state_lookup_answers_empty_for_a_key_with_no_loop(queue):
    """`/api/status` and `/api/repos` both read this, and a repo may have no loop yet."""
    daemon = _daemon(queue)
    daemon.loops["k"] = SimpleNamespace(state=LoopState.OBSERVING)
    assert daemon.loop_state("k") == "observing"
    assert daemon.loop_state("nothing-here") == ""


def test_the_loop_host_runs_a_coroutine_on_its_own_thread_and_stops(queue):
    """Seam 1: every `RepoLoop` is built and ticked off the drain thread (P27)."""
    host = LoopHost()
    host.start()
    try:
        where = host.run(_thread_name())
        assert where != threading.current_thread().name
        assert host.loop is not None
    finally:
        host.stop()
    assert host.loop is None


async def _thread_name() -> str:
    return threading.current_thread().name


def test_a_host_that_never_started_refuses_to_run(queue):
    """A caller that submits before `start` gets a refusal, not a silent no-op."""
    host = LoopHost()
    with pytest.raises(RuntimeError, match="loop host is not running"):
        host.run(_thread_name())


def test_stopping_a_host_that_never_started_is_a_no_op(queue):
    host = LoopHost()
    host.stop()
    assert host.loop is None


class _RunningReaders:
    """A `Readers` stand-in whose `index` opens its handle the way the real one does.

    `Readers.index` calls `asyncio.run`, which raises on a thread that already runs a loop, so a
    daemon that resolved the index on its host thread could never build a loop at all.
    """

    def __init__(self, store, user: UserSettings | None = None) -> None:
        self.store = store
        self.overlay = user or UserSettings()
        self.threads: list[str] = []

    def index(self, root: Path, *, identity: str | None = None):
        self.threads.append(threading.current_thread().name)
        return asyncio.run(self._open())

    async def _open(self):
        return self.store

    def config(self, root: Path) -> AuditorSettings:
        return AuditorSettings()

    def user(self, root: Path) -> UserSettings:
        """This repo's own overlay, which the loop takes in place of the daemon's home layer."""
        self.threads.append(threading.current_thread().name)
        return self.overlay


def test_a_loop_is_built_with_the_index_resolved_off_the_host_thread(queue, tmp_path):
    """The reader uses `asyncio.run`, so resolving it on the host thread cannot work (dogfood)."""
    store = asyncio.run(open_repo_index(tmp_path))
    readers = _RunningReaders(store)
    daemon = _daemon(queue)
    daemon.readers = readers
    daemon.host.start()
    try:
        key = daemon.ensure_loop(tmp_path)
    finally:
        daemon.stopping = True
        daemon.host.stop()
        asyncio.run(store.aclose())
    assert key in daemon.loops
    assert readers.threads == [threading.current_thread().name] * 2


def _overlay(**limits) -> UserSettings:
    """A per-repo overlay whose `observer.limits` differ from the shipped home defaults."""
    base = UserSettings()
    return base.model_copy(
        update={
            "observer": base.observer.model_copy(
                update={"limits": base.observer.limits.model_copy(update=limits)}
            )
        }
    )


def test_a_loop_is_built_from_this_repo_s_own_settings_and_not_the_daemon_s_home_layer(
    queue, tmp_path
):
    """Review M1: the daemon serves many repos, and its home layer is nobody's per-repo answer.

    Built through `ensure_loop`, because `_build` is what chooses between the two: a loop whose
    `user` a test assigns by hand cannot fail on the choice at all.
    """
    store = asyncio.run(open_repo_index(tmp_path))
    daemon = _daemon(queue)
    daemon.settings = UserSettings()
    daemon.readers = _RunningReaders(
        store, _overlay(max_nodes_per_run=3, max_feed_events=7)
    )
    daemon.host.start()
    try:
        built = daemon.loops[daemon.ensure_loop(tmp_path)]
    finally:
        daemon.stopping = True
        daemon.host.stop()
        asyncio.run(store.aclose())
    assert (
        daemon.settings.observer.limits.max_nodes_per_run == 12
    )  # the home layer, untouched
    assert built.user.observer.limits.max_nodes_per_run == 3
    assert built.feed.cap == 7  # and the feed cap is a per-repo setting too (L11)


def test_the_loop_host_joins_for_the_window_its_daemon_s_settings_name(queue):
    """L11: the join was a bare constant, and a slow shutdown is what someone would retune."""
    settings = UserSettings()
    tuned = settings.model_copy(
        update={
            "observer": settings.observer.model_copy(
                update={
                    "scheduling": settings.observer.scheduling.model_copy(
                        update={"host_join_seconds": 0.25}
                    )
                }
            )
        }
    )
    assert LoopHost().join_seconds == 5.0
    daemon = Daemon(
        queue=queue,
        sessions=SessionBook(expiry_minutes=45),
        idle=IdleTimer(minutes=30.0, now=0.0),
        settings=tuned,
    )
    assert daemon.host.join_seconds == 0.25


def test_retiring_a_repo_drops_the_backoff_its_failed_build_left_behind(queue):
    """L5: nothing else pruned `unbuildable`, so a repo that stopped existing kept it for ever."""

    class _Broken:
        def config(self, root: Path) -> AuditorSettings:
            raise RuntimeError("this repo's config will not load")

    daemon = _daemon(queue)
    daemon.readers = _Broken()
    key = daemon.ensure_loop(Path("/r"))
    assert key in daemon.unbuildable
    daemon.retire(key)
    assert daemon.unbuildable == {}
