"""``auditr observer`` - the background daemon's five lifecycle verbs (spec 12.2)."""

import typer

from auditor.cli.helpers import present
from auditor.cli.render import render_observer
from auditor.observer import OBSERVER_API_VERSION
from auditor.observer.daemon import (
    DaemonRecord,
    daemon_argv,
    daemon_health,
    daemon_started_at,
    detach,
    read_daemon_record,
    restart_daemon,
    serve,
    stop_daemon,
    wait_for,
)
from auditor.observer.payloads import DaemonStatus, HealthPayload
from auditor.paths import auditor_home, observer_enabled, observer_log_dir
from auditor.serve import open_url
from auditor.user_settings import SchedulingConfig, load_home_settings

observer_app = typer.Typer(no_args_is_help=True)
_JSON = typer.Option(False, "--json", help="Emit raw JSON.")


def _clocks() -> SchedulingConfig:
    """How long this home's settings say to wait for a daemon to appear or to go."""
    return load_home_settings().observer.scheduling


def _live() -> tuple[DaemonRecord | None, HealthPayload | None]:
    """The published record and the `/health` answer behind it, if a daemon is really there.

    Liveness is the daemon answering, never a flock probe: probing had to acquire the lock, and
    could take it from a daemon that was still starting.
    """
    record = read_daemon_record()
    if record is None:
        return None, None
    return record, daemon_health(record)


def _running() -> DaemonRecord | None:
    """The daemon that is answering for this home, or None."""
    record, health = _live()
    return record if health is not None else None


def _off() -> tuple[str, DaemonRecord | None]:
    """What every verb answers when `AUDITOR_OBSERVER` turns the observer off.

    All five, not just the two that start something: `auditr-observer` disables all five, and P19
    makes the two front doors one command surface.
    """
    return "disabled by AUDITOR_OBSERVER=0", None


def _launched() -> tuple[str, DaemonRecord | None]:
    """Start a daemon unless one already holds the home, and wait for it to publish itself.

    N callers racing each spawn a child and all but one exit at the lock, so the caller whose own
    child did not win reports what it found rather than a start that never happened.
    """
    running = _running()
    if running is not None:
        return "already running", running
    pid = detach(daemon_argv(), observer_log_dir() / "observer.log")
    wait_for(lambda: _running() is not None, timeout=_clocks().start_timeout_seconds)
    started = _running()
    if started is None:
        return "did not start", None
    return ("started" if started.pid == pid else "already running"), started


def _restarted(record: DaemonRecord) -> tuple[str, DaemonRecord | None]:
    """Re-exec a daemon whose wire this install does not speak, and wait for its replacement.

    The pid survives `os.execv`, so the daemon that answers with a later `started_at` is the new
    one; reporting the mismatch and leaving it running is what this branch used to do.
    """
    before = daemon_started_at(record)
    if not restart_daemon(record):
        return "wire compat mismatch", record
    wait_for(
        lambda: daemon_started_at(record) > before,
        timeout=_clocks().start_timeout_seconds,
    )
    fresh, health = _live()
    if health is None:
        return "did not restart", None
    if health.compat != OBSERVER_API_VERSION:
        return "wire compat mismatch", fresh
    return "restarted", fresh


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
    home = auditor_home()
    if foreground and json_:
        raise typer.BadParameter("--foreground is the daemon itself; it emits no JSON")
    if not observer_enabled():
        present(DaemonStatus.of(*_off(), home=home), render_observer, as_json=json_)
        return
    if foreground:
        raise typer.Exit(serve())
    present(DaemonStatus.of(*_launched(), home=home), render_observer, as_json=json_)


@observer_app.command("stop")
def stop(json_: bool = _JSON) -> None:
    """Stop this home's observer daemon."""
    home = auditor_home()
    if not observer_enabled():
        present(DaemonStatus.of(*_off(), home=home), render_observer, as_json=json_)
        return
    record = _running()
    reported: DaemonRecord | None = None
    if record is None:
        action = "not running"
    elif stop_daemon(record):
        wait_for(lambda: _running() is None, timeout=_clocks().stop_timeout_seconds)
        reported = _running()
        action = "stopped" if reported is None else "still stopping"
    else:
        action = "already gone"
    present(
        DaemonStatus.of(action, reported, home=home), render_observer, as_json=json_
    )


@observer_app.command("status")
def status(json_: bool = _JSON) -> None:
    """Report where this home's daemon is, if it is anywhere."""
    home = auditor_home()
    if not observer_enabled():
        present(DaemonStatus.of(*_off(), home=home), render_observer, as_json=json_)
        return
    record = _running()
    present(
        DaemonStatus.of("running" if record else "not running", record, home=home),
        render_observer,
        as_json=json_,
    )


@observer_app.command("open")
def open_page(json_: bool = _JSON) -> None:
    """Open the live page of this home's daemon in a browser."""
    home = auditor_home()
    if not observer_enabled():
        present(DaemonStatus.of(*_off(), home=home), render_observer, as_json=json_)
        return
    record = _running()
    if record is not None:
        open_url(f"http://127.0.0.1:{record.port}/")
    present(
        DaemonStatus.of("opened" if record else "not running", record, home=home),
        render_observer,
        as_json=json_,
    )


@observer_app.command("ensure")
def ensure(json_: bool = _JSON) -> None:
    """Make sure a compatible daemon is running, starting or restarting one if it is not."""
    home = auditor_home()
    if not observer_enabled():
        present(DaemonStatus.of(*_off(), home=home), render_observer, as_json=json_)
        return
    record, health = _live()
    if health is None:
        action, record = _launched()
    elif health.compat != OBSERVER_API_VERSION:
        action, record = _restarted(record)  # type: ignore[arg-type]
    else:
        action = "already running"
    present(DaemonStatus.of(action, record, home=home), render_observer, as_json=json_)
