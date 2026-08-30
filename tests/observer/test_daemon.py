"""Spec 8.1's process: the singleton, `daemon.json`, the idle window and the install spec."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import auditr_observer
from auditor.observer.daemon import (
    Daemon,
    DaemonLock,
    DaemonRecord,
    IdleTimer,
    daemon_argv,
    detach,
    wait_for,
)
from auditor.observer.events import Event, EventQueue, Spool
from auditor.observer.payloads import DaemonStatus
from auditor.observer.sessions import Session, SessionBook
from auditor.paths import auditor_home, daemon_json_path, write_json_dict


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
    assert second.held_elsewhere() is True
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


def test_the_daemon_adopts_a_spool_a_killed_predecessor_left(queue, tmp_path):
    """Spec 8.1's start-time drain is the same code path as a live one, so it is exercised."""
    Spool(tmp_path / "repos" / "k" / "spool.jsonl").append(
        Event(repo="/r", paths=("a.py",), at=1.0)
    )
    daemon = _daemon(queue)
    assert daemon.adopt(["k", "never-seen"]) == 1
    assert queue.keys() == ("k",)


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


def test_the_default_consumer_counts_what_it_drained(queue):
    """S8b's consumer counts and acknowledges; `drained_events` on the wire is this number."""
    daemon = _daemon(queue)
    queue.put("k", Event(repo="/r", paths=("a.py",), at=1.0))
    queue.put("k", Event(repo="/r", paths=("b.py",), at=2.0))
    daemon.tick()
    assert daemon.drained == 2


def test_a_tick_drops_an_expired_session(queue):
    """Nothing ticks for expiry, but the daemon's own tick is what actually collects them."""
    daemon = _daemon(queue)
    daemon.sessions.attach(_session())
    daemon.clock["now"] = 45 * 60 + 1
    daemon.tick()
    assert daemon.sessions.live(now=daemon.clock["now"]) == ()


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


def test_the_hook_verb_is_still_inert(capsys):
    """P20: the five lifecycle verbs got bodies in this slice and `hook` deliberately did not."""
    assert auditr_observer.main(["hook", "SessionStart", "--client", "claude"]) == 0
    assert "not available in this release" in capsys.readouterr().err


def test_the_client_resolves_the_same_home_as_the_package(tmp_path, monkeypatch):
    """The statusline's precedent: a stdlib re-implementation is pinned against its twin (P21)."""
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "elsewhere"))
    assert auditr_observer.home() == auditor_home()
    monkeypatch.delenv("AUDITOR_HOME")
    assert auditr_observer.home() == auditor_home()


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
