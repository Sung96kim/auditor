"""``auditr-observer`` - the observer daemon's lifecycle and hook client.

Deliberately outside the ``auditor`` package and stdlib-only: hooks run on every session event and
``import auditor`` costs about 0.17 s. The five lifecycle verbs talk to a running daemon over
loopback; ``hook`` posts one client's events. Nothing exits non-zero, so a hook can never fail
a session.
"""

import argparse
import contextlib
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
import uuid
import webbrowser
from collections.abc import Callable
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import NamedTuple

# Wire-compat literal; auditor/observer/__init__.py declares the same value and a test pins them.
OBSERVER_API_VERSION = 1

_UNAVAILABLE = "auditr-observer: not available in this release"
_LIFECYCLE = ("ensure", "start", "stop", "status", "open")
#: the same six strings ``auditor.paths.OFF_VALUES`` holds; a test pins the pair (P4)
_OFF = frozenset({"0", "f", "false", "n", "no", "off"})
_DISABLED = "auditr-observer: disabled by AUDITOR_OBSERVER=0"
_FAILED = "auditr-observer: the hook client failed; the session is unaffected"
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
#: spec 13.1's per-request budgets, and the client's own: no `SchedulingConfig` field holds
#: them, because nothing in `auditor/` reads them and this file may not import pydantic-settings.
#: 200 ms for an edit event, 3 s for the session-start attach, 1 s for the Stop heartbeat's
#: repair attach (which runs the same handler). `HOOK_BUDGETS` below sums the ladder each event
#: spends, and `tests/plugin/test_hooks_wiring.py` is what pins a plugin script against it (M3).
_POST_TIMEOUT = 0.2
_ATTACH_TIMEOUT = 3.0
_REPAIR_TIMEOUT = 1.0
#: a full `_MAX_PATHS` Stop batch runs Stage 0 once per path on the daemon's request thread:
#: measured 127 ms median / 129 ms max here, and up to 897 ms on the S9 review's box, so the
#: Stop batch gets its own budget rather than the per-edit one (spec 13.1, amended)
_STOP_POST_TIMEOUT = 2.0
#: what one `git` subprocess may spend, named by its caller rather than fixed inside `_git`.
#: Two budgets because the two calls are different shapes: `rev-parse` reads a few bytes of
#: metadata (1.2 ms median here) and `status` walks the whole worktree (2.4 ms median here, and
#: seconds on a large dirty one). Deadlines rather than costs, and they run before the batch is
#: on disk, which is why they are terms in `HOOK_BUDGETS` and not just arguments (M2).
_IDENTITY_TIMEOUT = 0.5
_STATUS_TIMEOUT = 2.0
#: the four events spec 13.1 names, and the two clients spec 19 splits between S9 and S12
_EVENTS = ("session-start", "post-tool-use", "stop", "session-end")
_CLIENTS = ("claude-code", "codex")
#: the whole client-side ladder each event can spend, sockets and git subprocesses alike, which
#: is what a plugin script's own `OBSERVE_TIMEOUT` has to cover. One home for a relationship that
#: spans two processes, and `tests/plugin/` pins every script against it. The identity term is
#: counted twice because `repo_identity` falls back to a second spelling of `rev-parse` on git
#: older than 2.31, and every git term runs *before* the batch reaches the spool, so a parent
#: that kills inside one loses the whole batch rather than one delivery. `session-start` is the
#: deliberate exception: the `ensure` launch behind it may outrun the whole hook, and the next
#: Stop repairs it (P30).
HOOK_BUDGETS = {
    "session-start": _ATTACH_TIMEOUT,
    "post-tool-use": 2 * _IDENTITY_TIMEOUT + _POST_TIMEOUT,
    "stop": (
        _POST_TIMEOUT
        + _REPAIR_TIMEOUT
        + _STATUS_TIMEOUT
        + 2 * _IDENTITY_TIMEOUT
        + _STOP_POST_TIMEOUT
    ),
    "session-end": _POST_TIMEOUT,
}
#: the statuses that mean *this daemon refused this body*, so no retry changes the answer and the
#: client's own copy is deleted: `routes.py` answers 400 for a body its models will not validate,
#: and `server.py` answers 400 for an unreadable Content-Length, 403 for a cross-origin or
#: non-loopback request and 413 for a body over 1 MiB. Its 411 is not here because this client
#: always sends a Content-Length, so a 411 came from someone else. Every other 4xx is someone
#: else's too: a 404 is what a recycled port's own HTTP server answers, and deleting a durable
#: batch on it destroys work no daemon ever took (M1).
_AUTHORITATIVE_REFUSALS = frozenset({400, 403, 413})
#: `auditor.observer.events.MAX_EVENT_PATHS`: a longer body is a 400, which is dropped, not spooled
_MAX_PATHS = 2000
#: how many undelivered batches one repo's spool holds before the client stops adding to it. Past
#: this, a daemon has not run for a very long time and one more dropped batch costs nothing.
_MAX_SPOOL_BATCHES = 128
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


def daemon_record() -> dict[str, object]:
    """What a running daemon published, or an empty dict when there is nothing to read."""
    try:
        data = json.loads((home() / "observer" / "daemon.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _send(
    port: int, method: str, path: str, body: str = "", timeout: float = _TIMEOUT
) -> tuple[int, dict[str, object]] | None:
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


def _ask(port: int, method: str, path: str, body: str = "") -> dict[str, object] | None:
    """The lifecycle verbs' reader: the answer alone, or None when nothing answered."""
    sent = _send(port, method, path, body)
    return sent[1] if sent is not None else None


def _page_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def _status(
    action: str, record: dict[str, object], health: dict[str, object] | None
) -> dict[str, object]:
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


def _live() -> tuple[dict[str, object], dict[str, object] | None]:
    """The published record and the ``/health`` answer behind it, if a daemon is really there."""
    record = daemon_record()
    port = record.get("port")
    if not isinstance(port, int):
        return record, None
    return record, _ask(port, "GET", "/health")


# --- the hook client (spec 13.1, 8.2) ---------------------------------------


def _git(root: Path, *args: str, timeout: float) -> str | None:
    """Raw stdout of a git subcommand, or None when it failed or outran `timeout`.

    None rather than an empty string is `discovery.git_output`'s own sentinel, so the two cannot
    drift apart. Every caller strips, because this does not. `timeout` is required and named by
    the caller: these calls run before the batch is spooled, so they sit inside the parent hook's
    kill window and `HOOK_BUDGETS` has to be able to count them (M2).
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
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
    absolute = _git(
        root,
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
        timeout=_IDENTITY_TIMEOUT,
    )
    if absolute is not None:
        return str(Path(absolute.strip()).resolve())
    relative = _git(  # git < 2.31
        root, "rev-parse", "--git-common-dir", timeout=_IDENTITY_TIMEOUT
    )
    if relative is not None:
        return str((root / relative.strip()).resolve())
    return str(root.resolve())


def key_for_identity(identity: str) -> str:
    """The hash half of `repo_dir_key`, so one `repo_identity` serves both the key and the crumb.

    Split out rather than inlined twice: resolving the identity is up to two git subprocesses on
    the tightest budget the client has, and an edit event needs the answer in two places (L4).
    """
    return hashlib.sha1(identity.encode(), usedforsecurity=False).hexdigest()


def repo_dir_key(root: Path) -> str:
    """`auditor.paths.repo_dir_key`: the sha1 that names `repos/<key>` and rides on `/events`."""
    return key_for_identity(repo_identity(root))


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


def _relative(path: str, root: Path, cwd: Path) -> str:
    """One posted path as the repo-relative string the graph keys on, or "" for one outside it.

    Claude Code sends an absolute `file_path` and `git status -z` sends a repo-relative one; the
    daemon stores whichever arrives, and every reader below it is keyed on the relative form, so
    a relative name is anchored at the session's `cwd` rather than passed through. A path that is
    not under `root` has no repo-relative spelling at all, and "" is what the caller drops on.
    """
    named = Path(path)
    named = named if named.is_absolute() else cwd / named
    try:
        return named.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return ""


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
    payload = _git(root, *_STATUS_ARGS, timeout=_STATUS_TIMEOUT)
    return () if payload is None else parse_status_z(payload)


def _post(
    path: str, body: dict[str, object], timeout: float
) -> tuple[int, dict[str, object]] | None:
    """One POST to whatever daemon `daemon.json` names, or None when nothing answered."""
    port = daemon_record().get("port")
    if not isinstance(port, int):
        return None
    return _send(port, "POST", path, json.dumps(body), timeout)


def spool_name(batch: str) -> str:
    """What one client-written batch is called inside `repos/<key>/`.

    Its own file rather than a line appended to the daemon's `spool.jsonl`: the daemon renames
    that file out from under a writer on every drain, and one file per batch is what makes
    delete-on-2xx a single `unlink` with nothing to interleave with (M4).
    """
    return f"spool.client.{batch}.jsonl"


def _spool(key: str, root: Path, identity: str, body: dict[str, object]) -> Path | None:
    """Write one batch where the daemon adopts it (spec 8.1), and answer where. Best effort.

    Written *before* the POST, not after it: a hook killed by its parent mid-request is the one
    branch nothing else recovers, so durability may not depend on the request finishing. The
    caller deletes the file when the daemon answers that it took the batch, and the `batch` id
    rides along so a delivery this client never saw the answer to is not assessed twice.

    The `root.json` crumb goes with it: `Daemon.reconcile` reads it to give an adopted spool a
    loop when spec 8.2's gate lets it, so a spool without one is drained into nothing (L9). The
    identity is handed in rather than resolved here, because the caller already spent the git
    calls on it to name `key` (L4).
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
                        "identity": identity,
                        "created_at": int(time.time()),
                    }
                )
            )
        if len(list(directory.glob(spool_name("*")))) >= _MAX_SPOOL_BATCHES:
            return None
        event = {
            "repo": body["repo"],
            "paths": body["paths"],
            "kind": body["kind"],
            "client": body["client"],
            "session_id": body["session_id"],
            "batch": body["batch"],
            "at": time.time(),
        }
        written = directory / spool_name(str(body["batch"]))
        written.write_text(json.dumps(event) + "\n", encoding="utf-8")
    except OSError:
        return None
    return written


def _drop(spooled: Path | None) -> None:
    """Forget a batch the daemon has answered for. A missing file is already forgotten."""
    if spooled is not None:
        with contextlib.suppress(OSError):
            spooled.unlink()


class HookRead(NamedTuple):
    """The four fields S9 needs out of any client's hook payload, already type-guarded.

    A named shape rather than a dict so a reader that forgets a field is a signature error here
    rather than a `KeyError` inside a hook whose whole contract is that it cannot fail.
    """

    cwd: str
    session_id: str
    agent_id: str
    path: str


def _text(payload: dict[str, object], field: str) -> str:
    """One payload field as a string, or "" for anything else.

    Every field, not just `path`: a client that sends a number where a string belongs would
    otherwise reach `Path()` or the wire and take the exit code with it (spec 13.1's contract).
    """
    value = payload.get(field)
    return value if isinstance(value, str) else ""


def _claude_event(payload: dict[str, object]) -> HookRead:
    """The four fields S9 needs out of a Claude Code hook payload, whichever event wrote it."""
    tool_input = payload.get("tool_input")
    named = tool_input if isinstance(tool_input, dict) else {}
    return HookRead(
        cwd=_text(payload, "cwd"),
        session_id=_text(payload, "session_id"),
        agent_id=_text(payload, "agent_id"),
        path=_text(named, "file_path"),
    )


def _codex_event(payload: dict[str, object]) -> HookRead:
    """The two fields a Codex hook payload carries that this client can use.

    No `agent_id` and no path: the only `tool_name` Codex ever dispatches is `Bash`, whose
    `tool_input` is `{command}`, so a Codex edit is only ever seen through Stop's git status
    (spec 19.1, 19.3).
    """
    return HookRead(
        cwd=_text(payload, "cwd"),
        session_id=_text(payload, "session_id"),
        agent_id="",
        path="",
    )


#: one reader per client; every branch below is client agnostic, so this is the whole difference
_READERS: dict[str, Callable[[dict[str, object]], HookRead]] = {
    "claude-code": _claude_event,
    "codex": _codex_event,
}


def _attach(root: Path, read: HookRead, client: str, timeout: float) -> None:
    """Tell the daemon this session is working in `root`. Best effort, like everything here."""
    _post(
        "/sessions/attach",
        {
            "repo": str(root),
            "session_id": read.session_id,
            "cwd": read.cwd,
            "client": client,
            "home": str(home()),
        },
        timeout,
    )


def _emit(
    root: Path, paths: tuple[str, ...], kind: str, read: HookRead, client: str
) -> None:
    """Spool one batch, POST it, and drop the spool only once the daemon has answered for it.

    Spool first: this process can be killed by its parent at any point, and only a batch already
    on disk survives that. The answer then decides what the spool line means - a 2xx took it and
    one of `_AUTHORITATIVE_REFUSALS` is this daemon refusing this body for ever, so both delete
    it; a 5xx, a timeout, or any other status is transient or is not the daemon answering at all,
    so the line stays and the daemon adopts it. The `batch` id is what keeps a delivery whose
    answer never arrived from being assessed twice (spec 8.1, amended).

    Truncated at `_MAX_PATHS`, because a longer body is refused whole: losing the tail of one
    Stop batch beats losing all of it.
    """
    if not paths:
        return
    identity = repo_identity(root)
    key = key_for_identity(identity)
    body: dict[str, object] = {
        "repo": str(root),
        "key": key,
        "paths": list(paths)[:_MAX_PATHS],
        "kind": kind,
        "client": client,
        "session_id": read.session_id,
        "batch": uuid.uuid4().hex,
    }
    spooled = _spool(key, root, identity, body)
    budget = _STOP_POST_TIMEOUT if kind == "stop" else _POST_TIMEOUT
    sent = _post("/events", body, budget)
    if sent is not None and (sent[0] < 300 or sent[0] in _AUTHORITATIVE_REFUSALS):
        _drop(spooled)


def _hook(event: str, client: str, payload: dict[str, object]) -> int:
    """One hook event, whatever the client. Never raises and never signals failure."""
    reader = _READERS.get(client)
    if reader is None:  # a client the parser admits and this build has no reader for
        return 0
    read = reader(payload)
    cwd = Path(read.cwd or ".")
    root = find_root(cwd)
    if event == "session-start":
        _run("ensure")
        _attach(root, read, client, _ATTACH_TIMEOUT)
        return 0
    if event == "session-end":
        _post("/sessions/detach", {"session_id": read.session_id}, _POST_TIMEOUT)
        return 0
    if event == "post-tool-use":
        if read.agent_id:  # spec 8.2: a subagent's tool call is not this session's edit
            return 0
        named = (_relative(read.path, root, cwd),) if read.path else ()
        _emit(root, tuple(p for p in named if auditable_shape(p)), "edit", read, client)
        return 0
    beat = _post("/sessions/heartbeat", {"session_id": read.session_id}, _POST_TIMEOUT)
    if beat is not None and not beat[1].get("ok"):
        # a cold `ensure` can outrun session-start's budget, so the daemon may never have been
        # told about this session; this is where that is noticed and repaired, on the budget the
        # same handler gets from session-start rather than an edit event's (S9-6)
        _attach(root, read, client, _REPAIR_TIMEOUT)
    kept = tuple(p for p in status_paths(root) if auditable_shape(p))
    _emit(root, kept, "stop", read, client)
    return 0


def read_payload() -> dict[str, object]:
    """The client's own hook JSON on stdin, or an empty payload when there is none.

    A terminal is not a payload: the plugin always pipes, but `auditr-observer hook stop` run by
    hand from a shell would otherwise block on `json.load` for ever.
    """
    try:
        if sys.stdin.isatty():
            return {}
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


def _stop(record: dict[str, object]) -> str:
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


def _run(command: str) -> dict[str, object]:
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
        try:
            return _hook(args.event, args.client, read_payload())
        except Exception:  # noqa: BLE001 - the file's whole contract is that it exits 0
            print(_FAILED, file=sys.stderr)
            return 0
    print(json.dumps(_run(args.command)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
