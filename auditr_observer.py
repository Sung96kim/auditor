"""``auditr-observer`` - the observer daemon's lifecycle and hook client.

Deliberately outside the ``auditor`` package and stdlib-only: hooks run on every session event and
``import auditor`` costs about 0.17 s. The five lifecycle verbs talk to a running daemon over
loopback; ``hook`` posts one client's events. Nothing exits non-zero, so a hook can never fail
a session.
"""

import argparse
import hashlib
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
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

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
#: spec 13.1's per-request budgets: 200 ms for an event, 3 s for the session-start attach
_POST_TIMEOUT = 0.2
_ATTACH_TIMEOUT = 3.0
#: the four events spec 13.1 names, and the two clients spec 19 splits between S9 and S12
_EVENTS = ("session-start", "post-tool-use", "stop", "session-end")
_CLIENTS = ("claude-code", "codex")
#: `auditor.observer.events.MAX_EVENT_PATHS`: a longer body is a 400, which is dropped, not spooled
_MAX_PATHS = 2000
#: `auditor.discovery.find_root`'s markers, re-implemented here the way the statusline does
_ROOT_MARKERS = (".git", "pyproject.toml", ".auditor")
#: Stage 0's cheap half: `FileDiscovery._supported`'s two sets, pinned against it by a test
_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".sh",
        ".bash",
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".properties",
        ".tfvars",
        ".pem",
        ".key",
    }
)
_FILENAMES = (".env", ".env.*", ".npmrc", ".pypirc", ".netrc", "package.json")
#: `auditor.discovery._EXCLUDE_DIRS`, the only exclusion a config-free hook can be sure of
_EXCLUDE_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".auditor",
        "build",
        "dist",
        ".tox",
        ".eggs",
    }
)
_STATUS_ARGS = (
    "status",
    "--porcelain=v1",
    "-z",
    "--untracked-files=all",
    "--ignore-submodules=all",
)


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


def _send(
    port: int, method: str, path: str, body: str = "", timeout: float = _TIMEOUT
) -> tuple[int, dict] | None:
    """One loopback request as ``(status, answer)``, or None when nothing answers.

    ``http.client.HTTPException`` is in the tuple because a recycled port can put a non-HTTP
    listener where the daemon was, and nothing here may exit non-zero. The status is returned
    because a spooling caller has to tell a 202 from a 400 (spec 13.1).
    """
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(method, path, body or None, {"Content-Type": "application/json"})
        response = conn.getresponse()
        answer = json.loads(response.read())
        return response.status, answer if isinstance(answer, dict) else {}
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        conn.close()


def _ask(port: int, method: str, path: str, body: str = "") -> dict | None:
    """The lifecycle verbs' reader: the answer alone, or None when nothing answered."""
    sent = _send(port, method, path, body)
    return sent[1] if sent is not None else None


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


# --- the hook client (spec 13.1, 8.2) ---------------------------------------


def _git(root: Path, *args: str) -> str | None:
    """Raw stdout of a git subcommand, or None when it failed.

    None rather than an empty string is `discovery.git_output`'s own sentinel, so the two cannot
    drift apart. Every caller strips, because this does not.
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def find_root(start: Path) -> Path:
    """The repo root `auditor.discovery.find_root` resolves, walking up from `start`."""
    start = (start if start.is_dir() else start.parent).resolve()
    for parent in [start, *start.parents]:
        if any((parent / marker).exists() for marker in _ROOT_MARKERS):
            return parent
    return start


def repo_identity(root: Path) -> str:
    """`auditor.paths.repo_identity`: the resolved git common dir, else the resolved root."""
    absolute = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if absolute is not None:
        return str(Path(absolute.strip()).resolve())
    relative = _git(root, "rev-parse", "--git-common-dir")  # git < 2.31
    if relative is not None:
        return str((root / relative.strip()).resolve())
    return str(root.resolve())


def repo_dir_key(root: Path) -> str:
    """`auditor.paths.repo_dir_key`: the sha1 that names `repos/<key>` and rides on `/events`."""
    return hashlib.sha1(repo_identity(root).encode(), usedforsecurity=False).hexdigest()


def auditable_shape(rel: str) -> bool:
    """Stage 0's config-free half: a supported name, not under an obviously excluded directory.

    Deliberately narrower than `FileDiscovery.auditable_shape`, which reads the repo's own globs:
    this only ever drops what that would drop too, and the daemon runs the real predicate again.
    """
    named = PurePosixPath(rel)
    supported = named.suffix in _SUFFIXES or any(
        fnmatch(named.name, pattern) for pattern in _FILENAMES
    )
    return supported and not set(rel.split("/")) & _EXCLUDE_DIRS


def _relative(path: str, root: Path) -> str:
    """One posted path as the repo-relative string the graph keys on.

    Claude Code sends an absolute `file_path` and `git status -z` sends a repo-relative one; the
    daemon stores whichever arrives, and every reader below it is keyed on the relative form. A
    path already relative, or one outside `root`, is returned unchanged.
    """
    named = Path(path)
    if not named.is_absolute():
        return path
    try:
        return named.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path


def parse_status_z(payload: str) -> tuple[str, ...]:
    """Every path a `git status --porcelain=v1 -z` answer names, both sides of a rename.

    `auditor.discovery.parse_status_z` re-implemented stdlib side, cursor for cursor: a rename or
    a copy spends two NUL fields, the new path in the record and the old one after it.
    """
    fields = payload.split("\0")
    out: list[str] = []
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if len(record) < 4:
            continue
        code, path = record[:2], record[3:]
        out.append(path)
        if ("R" in code or "C" in code) and index < len(fields):
            out.append(fields[index])
            index += 1
    return tuple(out)


def status_paths(root: Path) -> tuple[str, ...]:
    """The whole dirty tree at `root`, or empty outside a checkout (spec 8.2). Not a delta."""
    payload = _git(root, *_STATUS_ARGS)
    return () if payload is None else parse_status_z(payload)


def _post(path: str, body: dict, timeout: float) -> tuple[int, dict] | None:
    """One POST to whatever daemon `daemon.json` names, or None when nothing answered."""
    port = daemon_record().get("port")
    if not isinstance(port, int):
        return None
    return _send(port, "POST", path, json.dumps(body), timeout)


def _spool(key: str, root: Path, body: dict) -> None:
    """Write one refused batch where the daemon adopts it at start (spec 8.1), best effort.

    The `root.json` crumb goes with it: `Daemon.reconcile` reads it to give an adopted spool a
    loop, so a spool without one is drained into nothing.
    """
    directory = home() / "repos" / key
    try:
        directory.mkdir(parents=True, exist_ok=True)
        crumb = directory / "root.json"
        if not crumb.exists():
            crumb.write_text(
                json.dumps(
                    {
                        "root": str(root.resolve()),
                        "identity": repo_identity(root),
                        "created_at": int(time.time()),
                    }
                )
            )
        event = {
            "repo": body["repo"],
            "paths": body["paths"],
            "kind": body["kind"],
            "client": body["client"],
            "session_id": body["session_id"],
            "at": time.time(),
        }
        with (directory / "spool.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")
    except OSError:
        return


def _claude_event(payload: dict) -> dict:
    """The four fields S9 needs out of a Claude Code hook payload, whichever event wrote it."""
    tool_input = payload.get("tool_input")
    path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    return {
        "cwd": payload.get("cwd") or "",
        "session_id": payload.get("session_id") or "",
        "agent_id": payload.get("agent_id") or "",
        "path": path if isinstance(path, str) else "",
    }


#: one reader per client; S12 adds `codex` here and touches no plugin script
_READERS = {"claude-code": _claude_event}


def _attach(root: Path, read: dict, client: str, timeout: float) -> None:
    """Tell the daemon this session is working in `root`. Best effort, like everything here."""
    _post(
        "/sessions/attach",
        {
            "repo": str(root),
            "session_id": read["session_id"],
            "cwd": read["cwd"],
            "client": client,
            "home": str(home()),
        },
        timeout,
    )


def _emit(
    root: Path, paths: tuple[str, ...], kind: str, read: dict, client: str
) -> None:
    """POST one batch, and spool it when nothing answered. A refusal is dropped, never spooled.

    Truncated at `_MAX_PATHS`, because a longer body is refused whole and a refusal is not
    spooled: losing the tail of one Stop batch beats losing all of it.
    """
    if not paths:
        return
    key = repo_dir_key(root)
    body = {
        "repo": str(root),
        "key": key,
        "paths": list(paths)[:_MAX_PATHS],
        "kind": kind,
        "client": client,
        "session_id": read["session_id"],
    }
    if _post("/events", body, _POST_TIMEOUT) is None:
        _spool(key, root, body)


def _hook(event: str, client: str, payload: dict) -> int:
    """One hook event, whatever the client. Never raises and never signals failure."""
    reader = _READERS.get(client)
    if reader is None:  # `codex` is declared and arrives in S12
        return 0
    read = reader(payload)
    if read["agent_id"]:  # spec 8.2: a subagent's tool call is not this session's edit
        return 0
    root = find_root(Path(read["cwd"] or "."))
    if event == "session-start":
        _run("ensure")
        _attach(root, read, client, _ATTACH_TIMEOUT)
        return 0
    if event == "session-end":
        _post("/sessions/detach", {"session_id": read["session_id"]}, _POST_TIMEOUT)
        return 0
    if event == "post-tool-use":
        named = (_relative(read["path"], root),) if read["path"] else ()
        _emit(root, tuple(p for p in named if auditable_shape(p)), "edit", read, client)
        return 0
    beat = _post(
        "/sessions/heartbeat", {"session_id": read["session_id"]}, _POST_TIMEOUT
    )
    if beat is not None and not beat[1].get("ok"):
        # a cold `ensure` can outrun session-start's budget, so the daemon may never have been
        # told about this session; this is where that is noticed and repaired
        _attach(root, read, client, _POST_TIMEOUT)
    kept = tuple(p for p in status_paths(root) if auditable_shape(p))
    _emit(root, kept, "stop", read, client)
    return 0


def read_payload() -> dict:
    """The client's own hook JSON on stdin, or an empty payload when there is none."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


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
    hook.add_argument("event", choices=_EVENTS)
    # `ClientKind`'s own spelling: "claude" is not one, and `/events` answers 400 for it
    hook.add_argument("--client", default="claude-code", choices=_CLIENTS)
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
        return _hook(args.event, args.client, read_payload())
    print(json.dumps(_run(args.command)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
