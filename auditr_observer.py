"""``auditr-observer`` - the observer daemon's lifecycle and hook client.

Deliberately outside the ``auditor`` package and stdlib-only: hooks run on every session event and
``import auditor`` costs about 0.17 s. The five lifecycle verbs talk to a running daemon over
loopback; ``hook`` is still inert. Nothing exits non-zero, so a hook can never fail a session.
"""

import argparse
import http.client
import importlib.metadata
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# Wire-compat literal; auditor/observer/__init__.py declares the same value and a test pins them.
OBSERVER_API_VERSION = 1

_UNAVAILABLE = "auditr-observer: not available in this release"
_LIFECYCLE = ("ensure", "start", "stop", "status", "open")
#: the same six strings ``auditor.paths.OFF_VALUES`` holds; a test pins the pair (P4)
_OFF = frozenset({"0", "f", "false", "n", "no", "off"})
_DISABLED = "auditr-observer: disabled by AUDITOR_OBSERVER=0"
#: the key set ``auditr observer status --json`` prints; a test pins it against ``DaemonStatus``
STATUS_KEYS = (
    "running",
    "action",
    "pid",
    "port",
    "home",
    "version",
    "compat",
    "page_url",
)
_TIMEOUT = 2.0
#: `SchedulingConfig.start_timeout_seconds` and `.stop_timeout_seconds`; a test pins the pair,
#: because this file may not import pydantic-settings to read them
_START_TIMEOUT = 10.0
_STOP_TIMEOUT = 10.0
#: the mount waits `_START_TIMEOUT` itself, so the run that waits on it needs a longer budget
_LAUNCH_TIMEOUT = _START_TIMEOUT * 2


def _version() -> str:
    try:
        return importlib.metadata.version("auditr")
    except importlib.metadata.PackageNotFoundError:
        return "unknown (not installed as a distribution)"


def home() -> Path:
    """``$AUDITOR_HOME`` or ``~/.auditor``, resolved the way ``auditor.paths.auditor_home`` does.

    A deliberate stdlib re-implementation, like the statusline's ``find_root``; the pair is pinned
    by a test. An empty value means unset, which is ``env_ignore_empty`` on the twin.
    """
    raw = os.environ.get("AUDITOR_HOME") or ""
    return Path(raw).expanduser() if raw else Path.home() / ".auditor"


def disabled() -> bool:
    """Whether ``AUDITOR_OBSERVER`` turns the observer off outright (spec 8.1, 14)."""
    return os.environ.get("AUDITOR_OBSERVER", "").strip().lower() in _OFF


def daemon_record() -> dict:
    """What a running daemon published, or an empty dict when there is nothing to read."""
    try:
        data = json.loads((home() / "observer" / "daemon.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _ask(port: int, method: str, path: str, body: str = "") -> dict | None:
    """One loopback request, or None when nothing answers. A dead daemon is an answer, not a crash.

    ``http.client.HTTPException`` is in the tuple because a recycled port can put a non-HTTP
    listener where the daemon was, and nothing here may exit non-zero.
    """
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=_TIMEOUT)
    try:
        conn.request(method, path, body or None, {"Content-Type": "application/json"})
        answer = json.loads(conn.getresponse().read())
        return answer if isinstance(answer, dict) else None
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        conn.close()


def _page_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def _status(action: str, record: dict, health: dict | None) -> dict:
    """One ``DaemonStatus`` shaped dict; the mount prints the same keys from the model (P19)."""
    running = health is not None
    port = record.get("port")
    return {
        "running": running,
        "action": action,
        "pid": record.get("pid") if running else None,
        "port": port if running else None,
        "home": str(home()),
        "version": (health or {}).get("version", "") if running else "",
        "compat": (health or {}).get("compat", 0) if running else 0,
        "page_url": _page_url(port) if running and port else "",
    }


def _live() -> tuple[dict, dict | None]:
    """The published record and the ``/health`` answer behind it, if a daemon is really there."""
    record = daemon_record()
    port = record.get("port")
    if not isinstance(port, int):
        return record, None
    return record, _ask(port, "GET", "/health")


def build_parser() -> argparse.ArgumentParser:
    """Full observer command surface: the lifecycle verbs plus ``hook <event> --client <c>``."""
    parser = argparse.ArgumentParser(
        prog="auditr-observer", description="auditor observer client."
    )
    parser.add_argument(
        "--version", action="version", version=f"auditr-observer {_version()}"
    )
    subparsers = parser.add_subparsers(dest="command")
    for name in _LIFECYCLE:
        subparsers.add_parser(name)
    hook = subparsers.add_parser("hook")
    hook.add_argument("event")
    hook.add_argument("--client", default="claude")
    return parser


def _wait_for(check, timeout: float, poll: float = 0.02) -> bool:
    """Poll ``check`` until it is true or the deadline passes.

    The stdlib twin of ``auditor.observer.daemon.wait_for``, down to the poll interval.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(poll)
    return check()


def _launch() -> bool:
    """Start a daemon through the ``auditr`` mount, which owns the install spec (P3).

    Delegated rather than re-implemented: this file may not import ``auditor``, but it can run it,
    and spelling the daemon's own argv here would be a second copy of ``daemon_argv``.
    """
    found = shutil.which("auditr")
    argv = (
        [found, "observer", "start"]
        if found
        else [sys.executable, "-m", "auditor.cli", "observer", "start"]
    )
    try:
        subprocess.run(argv, check=False, capture_output=True, timeout=_LAUNCH_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return False
    return _wait_for(lambda: _live()[1] is not None, _START_TIMEOUT)


def _started_at(port: int) -> float:
    """When the daemon on this port started, or 0.0 when nothing answers.

    The pid survives ``os.execv``, so this is what tells a restarted daemon from the one that
    was asked to restart.
    """
    started = (_ask(port, "GET", "/api/status") or {}).get("started_at", 0.0)
    return float(started) if isinstance(started, int | float) else 0.0


def _restart(port: int) -> str:
    """Re-exec a daemon whose wire this client does not speak, and wait for its replacement."""
    before = _started_at(port)
    answer = _ask(
        port,
        "POST",
        "/admin/restart",
        json.dumps({"compat": OBSERVER_API_VERSION}),
    )
    if not (answer and answer.get("restarting")):
        return "wire compat mismatch"
    if not _wait_for(lambda: _started_at(port) > before, _START_TIMEOUT):
        return "did not restart"
    return "restarted"


def _stop(record: dict) -> str:
    """SIGTERM the published pid; the daemon's handler releases the lock and `daemon.json`."""
    pid = record.get("pid")
    if not isinstance(pid, int):
        return "not running"
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return "already gone"
    stopped = _wait_for(lambda: _live()[1] is None, _STOP_TIMEOUT)
    return "stopped" if stopped else "still stopping"


def _run(command: str) -> dict:
    """One lifecycle verb against whatever daemon is there, starting one when asked to."""
    record, health = _live()
    if command in ("start", "ensure"):
        if health is not None:
            stale = health.get("compat") != OBSERVER_API_VERSION
            if (
                command == "ensure" and stale
            ):  # P19: the mount's `ensure` restarts it too
                return _status(_restart(int(record["port"])), *_live())
            return _status("already running", record, health)
        launched = _launch()
        record, health = _live()
        return _status("started" if launched else "did not start", record, health)
    if command == "stop":
        if health is None:
            return _status("not running", record, None)
        return _status(_stop(record), *_live())
    if command == "open":
        if health is None:
            return _status("not running", record, None)
        webbrowser.open(_page_url(int(record["port"])))
        return _status("opened", record, health)
    return _status("running" if health else "not running", record, health)


def main(argv: list[str] | None = None) -> int:
    """Run one observer verb, never signalling failure.

    Argparse exits 2 on malformed argv; that is swallowed here so a hook can never fail a
    session. ``--version`` and ``-h`` exit 0 through argparse and pass straight through.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as requested_exit:
        if requested_exit.code == 0:
            raise
        print(_UNAVAILABLE, file=sys.stderr)
        return 0
    if args.command is None:  # no verb: say what the verbs are, and still exit 0
        parser.print_usage(sys.stderr)
        return 0
    if disabled():
        print(_DISABLED, file=sys.stderr)
        return 0
    if args.command == "hook":
        print(_UNAVAILABLE, file=sys.stderr)
        return 0
    print(json.dumps(_run(args.command)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
