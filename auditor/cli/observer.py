"""``auditr observer`` - the background daemon's five lifecycle verbs (spec 12.2)."""

import typer

from auditor.cli.helpers import present
from auditor.cli.render import render_observer
from auditor.observer import OBSERVER_API_VERSION
from auditor.observer.daemon import (
    DaemonLock,
    DaemonRecord,
    daemon_argv,
    detach,
    read_daemon_record,
    serve,
    stop_daemon,
    wait_for,
)
from auditor.observer.payloads import DaemonStatus
from auditor.paths import (
    auditor_home,
    observer_enabled,
    observer_lock_path,
    observer_log_dir,
)
from auditor.serve import open_url

observer_app = typer.Typer(no_args_is_help=True)
#: how long `start` waits for the child to publish `daemon.json` before reporting what it sees
_START_TIMEOUT = 10.0
_STOP_TIMEOUT = 10.0
_JSON = typer.Option(False, "--json", help="Emit raw JSON.")


def _running() -> DaemonRecord | None:
    """The daemon that holds this home's lock, or None. Liveness is the flock, never a pid."""
    if not DaemonLock(observer_lock_path()).held_elsewhere():
        return None
    return read_daemon_record()


def _fields(action: str, record: DaemonRecord | None) -> dict:
    """One shape for all five verbs: where the daemon is and what just changed (P19)."""
    if record is None:
        return {"action": action, "home": str(auditor_home())}
    return {
        "running": True,
        "action": action,
        "pid": record.pid,
        "port": record.port,
        "home": record.home,
        "version": record.version,
        "compat": record.compat,
        "page_url": f"http://127.0.0.1:{record.port}/",
    }


def _launched() -> tuple[str, DaemonRecord | None]:
    """Start a daemon unless one already holds the lock, and wait for it to publish itself."""
    running = _running()
    if running is not None:
        return "already running", running
    detach(daemon_argv(), observer_log_dir() / "observer.log")
    wait_for(lambda: _running() is not None, timeout=_START_TIMEOUT)
    started = _running()
    return ("started" if started else "did not start"), started


@observer_app.command("start")
def start(
    foreground: bool = typer.Option(
        False,
        "--foreground",
        hidden=True,
        help="Be the daemon rather than launching one.",
    ),
    json_: bool = _JSON,
) -> None:
    """Start the observer daemon for this home."""
    if foreground:
        raise typer.Exit(serve())
    if observer_enabled():
        action, record = _launched()
    else:
        action, record = "not started; AUDITOR_OBSERVER=0", None
    present(DaemonStatus(**_fields(action, record)), render_observer, as_json=json_)


@observer_app.command("stop")
def stop(json_: bool = _JSON) -> None:
    """Stop this home's observer daemon."""
    record = _running()
    if record is None:
        action = "not running"
    elif stop_daemon(record):
        wait_for(lambda: _running() is None, timeout=_STOP_TIMEOUT)
        action = "stopped" if _running() is None else "still stopping"
    else:
        action = "already gone"
    present(DaemonStatus(**_fields(action, None)), render_observer, as_json=json_)


@observer_app.command("status")
def status(json_: bool = _JSON) -> None:
    """Report where this home's daemon is, if it is anywhere."""
    record = _running()
    present(
        DaemonStatus(**_fields("running" if record else "not running", record)),
        render_observer,
        as_json=json_,
    )


@observer_app.command("open")
def open_page(json_: bool = _JSON) -> None:
    """Open the live page of this home's daemon in a browser."""
    record = _running()
    if record is not None:
        open_url(f"http://127.0.0.1:{record.port}/")
    present(
        DaemonStatus(**_fields("opened" if record else "not running", record)),
        render_observer,
        as_json=json_,
    )


@observer_app.command("ensure")
def ensure(json_: bool = _JSON) -> None:
    """Make sure a compatible daemon is running, starting one if there is none."""
    if not observer_enabled():
        action, record = "not ensured; AUDITOR_OBSERVER=0", None
    else:
        record = _running()
        if record is None:
            action, record = _launched()
        elif record.compat != OBSERVER_API_VERSION:
            action = "wire compat mismatch"
        else:
            action = "already running"
    present(DaemonStatus(**_fields(action, record)), render_observer, as_json=json_)
