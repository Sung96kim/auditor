"""Shared CLI helpers: clean one-line error exits, the async-run spinner bridge, JSON echo,
format validation, report emission, and index-path resolution. Command modules import what
they need; anything used by a single command lives in that command's module instead.
"""

import asyncio
import difflib
import json
from collections.abc import Callable, Coroutine, Iterable
from pathlib import Path
from typing import Any, NoReturn, TypeVar

import typer
from pydantic import ValidationError
from rich.console import Console

from auditor.cli.apps import _status
from auditor.database import IndexStore
from auditor.paths import index_db_path, repo_key
from auditor.registry import REGISTRY

_T = TypeVar("_T")

_SPINNER = "dots12"
_SPINNER_STYLE = "#7C7CFF"

_out = Console()


def _echo_json(payload: object) -> None:
    typer.echo(json.dumps(payload, indent=2))


def _present(
    payload: object,
    render: Callable[[Console, Any], None],
    *,
    as_json: bool = False,
) -> None:
    """Emit a command result: pretty for a human at a TTY, else raw JSON (so piped/
    captured/agent callers and --json still get the exact machine-readable output)."""
    if as_json or not _out.is_terminal:
        _echo_json(payload)
    else:
        render(_out, payload)


def _fail(message: str) -> NoReturn:
    """Emit a clean one-line error to stderr and exit non-zero (no traceback)."""
    _status.print(f"[red]error:[/red] {message}")
    raise typer.Exit(1)


def _suggest(value: str, candidates: Iterable[str]) -> str:
    """`" Did you mean 'X'?"` when a candidate closely matches ``value``, else ``""`` — for
    friendlier 'unknown rule/category/…' errors."""
    match = difflib.get_close_matches(value, list(candidates), n=1, cutoff=0.6)
    return f" Did you mean '{match[0]}'?" if match else ""


def _parse_config_json(raw: str | None) -> dict | None:
    """Parse a ``--config-json`` blob to a dict, or exit cleanly on bad JSON / non-object."""
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        _fail(f"invalid --config-json: {exc}")
    if not isinstance(value, dict):
        _fail("--config-json must be a JSON object")
    return value


def _format_config_error(exc: ValidationError) -> str:
    """First validation error as ``'<dotted loc>: <msg>'`` for a clean one-line failure."""
    err = exc.errors()[0]
    loc = ".".join(str(p) for p in err["loc"])
    return f"{loc}: {err['msg']}" if loc else err["msg"]


def _require_exists(path: Path) -> None:
    if not path.exists():
        _fail(f"no such file or directory: {path}")


def _require_file(path: Path) -> None:
    if not path.is_file():
        _fail(f"no such file: {path}")


def _check_format(fmt: str) -> str:
    if REGISTRY.reporter(fmt) is None:
        _fail(f"unknown format {fmt!r}; choose from {sorted(REGISTRY.formats())}")
    return fmt


def _run(
    coro: Coroutine[Any, Any, _T], message: str = "auditing…", *, spinner: bool = True
) -> _T:
    """Run an async core call. Shows a stderr spinner unless ``spinner`` is off (e.g. when
    ``-v`` logging is driving the progress output instead)."""
    if not spinner:
        return asyncio.run(coro)
    with _status.status(message, spinner=_SPINNER, spinner_style=_SPINNER_STYLE):
        return asyncio.run(coro)


def _run_staged(
    make_coro: Callable[[Callable[[str], None]], Coroutine[Any, Any, _T]],
    message: str = "working…",
    *,
    spinner: bool = True,
) -> _T:
    """Like _run but passes the coro factory a `report(text)` callback that live-updates the
    spinner so long multi-stage ops can show progress. make_coro: (report) -> Coroutine."""
    if not spinner:
        return asyncio.run(make_coro(lambda _msg: None))
    with _status.status(message, spinner=_SPINNER, spinner_style=_SPINNER_STYLE) as st:

        def report(text: str) -> None:
            st.update(
                f"[dim]{text}[/dim]", spinner=_SPINNER, spinner_style=_SPINNER_STYLE
            )

        return asyncio.run(make_coro(report))


def _open_index(root: Path) -> Coroutine[Any, Any, IndexStore]:
    """Connect to the shared global index, scoped to ``root``'s partition. Returns the
    awaitable from ``IndexStore.connect`` (use as ``async with await _open_index(root)``)."""
    return IndexStore.connect(index_db_path(), repo_key(root))


def _open_shared_index() -> Coroutine[Any, Any, IndexStore]:
    """Connect to the shared global index for cross-repo operations (listing/forgetting repos),
    not bound to any one repo's partition."""
    return IndexStore.connect(index_db_path())


def _emit(rendered: str, output: Path | None) -> None:
    """Write a rendered report to ``output`` (with a stderr note) or echo it to stdout."""
    if output is None:
        typer.echo(rendered)
        return
    output.write_text(rendered, encoding="utf-8")
    typer.echo(f"wrote {output}", err=True)
