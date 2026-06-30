"""Shared CLI helpers: clean one-line error exits, the async-run spinner bridge, JSON echo,
format validation, report emission, and index-path resolution. Command modules import what
they need; anything used by a single command lives in that command's module instead.
"""

import asyncio
import difflib
import json
import time
from collections.abc import Callable, Coroutine, Iterable
from pathlib import Path
from typing import Any, NoReturn, TypeVar

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.text import Text

from auditor.cli.console import ACCENT, console, err_console
from auditor.database import IndexStore
from auditor.paths import index_db_path, repo_key
from auditor.registry import REGISTRY

_T = TypeVar("_T")

_SPINNER = "dots12"
_SPINNER_STYLE = ACCENT


def _echo_json(payload: object) -> None:
    typer.echo(json.dumps(payload, indent=2))


def present(
    payload: object,
    render: Callable[[Console, Any], None],
    *,
    as_json: bool = False,
) -> None:
    """Emit a command result: pretty for a human at a TTY, else raw JSON (so piped/
    captured/agent callers and --json still get the exact machine-readable output)."""
    if as_json or not console.is_terminal:
        _echo_json(payload)
    else:
        render(console, payload)


def fail(message: str) -> NoReturn:
    """Emit a clean one-line error to stderr and exit non-zero (no traceback)."""
    err_console.print(f"[red]error:[/red] {message}")
    raise typer.Exit(1)


def suggest(value: str, candidates: Iterable[str]) -> str:
    """`" Did you mean 'X'?"` when a candidate closely matches ``value``, else ``""`` — for
    friendlier 'unknown rule/category/…' errors."""
    match = difflib.get_close_matches(value, list(candidates), n=1, cutoff=0.6)
    return f" Did you mean '{match[0]}'?" if match else ""


def parse_config_json(raw: str | None) -> dict | None:
    """Parse a ``--config-json`` blob to a dict, or exit cleanly on bad JSON / non-object."""
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"invalid --config-json: {exc}")
    if not isinstance(value, dict):
        fail("--config-json must be a JSON object")
    return value


def format_config_error(exc: ValidationError) -> str:
    """First validation error as ``'<dotted loc>: <msg>'`` for a clean one-line failure."""
    err = exc.errors()[0]
    loc = ".".join(str(p) for p in err["loc"])
    return f"{loc}: {err['msg']}" if loc else err["msg"]


def require_exists(path: Path) -> None:
    if not path.exists():
        fail(f"no such file or directory: {path}")


def require_file(path: Path) -> None:
    if not path.is_file():
        fail(f"no such file: {path}")


def check_format(fmt: str) -> str:
    if REGISTRY.reporter(fmt) is None:
        fail(f"unknown format {fmt!r}; choose from {sorted(REGISTRY.formats())}")
    return fmt


def run(
    coro: Coroutine[Any, Any, _T], message: str = "auditing…", *, spinner: bool = True
) -> _T:
    """Run an async core call. Shows a stderr spinner unless ``spinner`` is off (e.g. when
    ``-v`` logging is driving the progress output instead)."""
    if not spinner:
        return asyncio.run(coro)
    with err_console.status(message, spinner=_SPINNER, spinner_style=_SPINNER_STYLE):
        return asyncio.run(coro)


def run_staged(
    make_coro: Callable[[Callable[[str], None]], Coroutine[Any, Any, _T]],
    message: str = "working…",
    *,
    spinner: bool = True,
) -> _T:
    """Like run but passes the coro factory a `report(text)` callback that live-updates the
    spinner so long multi-stage ops can show progress. make_coro: (report) -> Coroutine."""
    if not spinner:
        return asyncio.run(make_coro(lambda _msg: None))
    with err_console.status(
        message, spinner=_SPINNER, spinner_style=_SPINNER_STYLE
    ) as st:

        def report(text: str) -> None:
            st.update(
                f"[dim]{text}[/dim]", spinner=_SPINNER, spinner_style=_SPINNER_STYLE
            )

        return asyncio.run(make_coro(report))


class _Working:
    """A spinner status line whose trailing dots pulse (so the '…' animates) and whose label can
    be updated in place with the current file/action. rich re-renders it every refresh, so the
    dots advance with the clock even while the scan blocks the main thread."""

    def __init__(self, label: str) -> None:
        self._label = label

    def update(self, label: str) -> None:
        self._label = label

    def __rich__(self) -> Text:
        dots = "." * (int(time.monotonic() * 3) % 4)
        return Text(f"{self._label}{dots}")


def run_live(
    make_coro: Callable[[Callable[[str], None]], Coroutine[Any, Any, _T]],
    label: str,
    *,
    spinner: bool = True,
) -> _T:
    """Like run_staged, but the status line animates its trailing dots and is updated in place
    with the progress text (e.g. the file currently being audited). make_coro: (progress) ->
    Coroutine."""
    if not spinner:
        return asyncio.run(make_coro(lambda _msg: None))
    work = _Working(label)
    with err_console.status(work, spinner=_SPINNER, spinner_style=_SPINNER_STYLE):
        return asyncio.run(make_coro(work.update))


def open_index(root: Path) -> Coroutine[Any, Any, IndexStore]:
    """Connect to the shared global index, scoped to ``root``'s partition. Returns the
    awaitable from ``IndexStore.connect`` (use as ``async with await open_index(root)``)."""
    return IndexStore.connect(index_db_path(), repo_key(root))


def open_shared_index() -> Coroutine[Any, Any, IndexStore]:
    """Connect to the shared global index for cross-repo operations (listing/forgetting repos),
    not bound to any one repo's partition."""
    return IndexStore.connect(index_db_path())


def emit(rendered: str, output: Path | None) -> None:
    """Write a rendered report to ``output`` (with a stderr note) or echo it to stdout."""
    if output is None:
        typer.echo(rendered)
        return
    output.write_text(rendered, encoding="utf-8")
    err_console.print(f"[green]✓[/green] wrote [bold]{output}[/bold]")
