"""Shared CLI helpers: clean one-line error exits, the async-run spinner bridge, JSON echo,
format validation, report emission, and index-path resolution. Command modules import what
they need; anything used by a single command lives in that command's module instead.
"""

import asyncio
import difflib
import json
import time
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NoReturn, TypeVar

import typer
from pydantic import BaseModel, ValidationError
from rich.console import Console
from rich.text import Text

from auditor.cli.console import ACCENT, console, err_console
from auditor.config import AuditorSettings, ConfigError, load_config
from auditor.config_notice import NOTICE, ConfigNotice, format_config_error
from auditor.database import IndexStore, open_repo_index
from auditor.database.base import DEFAULT_REPO, UnmigratableColumn
from auditor.discovery import find_root
from auditor.paths import index_db_path
from auditor.plugins import PluginLoader
from auditor.registry import REGISTRY
from auditor.user_settings import UserSettings, load_user_settings

_T = TypeVar("_T")
_P = TypeVar("_P", bound=BaseModel)

_SPINNER = "dots12"
_SPINNER_STYLE = ACCENT


def _echo_json(payload: object) -> None:
    typer.echo(json.dumps(payload, indent=2))


def present(
    payload: _P | None,
    render: Callable[[Console, _P], None],
    *,
    as_json: bool = False,
) -> None:
    """Emit a command result: pretty for a human at a TTY, else raw JSON (so piped/
    captured/agent callers and --json still get the exact machine-readable output).

    The renderer is typed to the payload it is paired with, so a mispairing is a type error
    rather than an ``AttributeError`` in front of a user. ``None`` is the "nothing found"
    payload: it serialises as ``{}``, the shape the graph queries have always returned, and
    still reaches the renderer (the three that can miss declare ``| None``).
    """
    if as_json or not console.is_terminal:
        _echo_json(
            {} if payload is None else payload.model_dump(mode="json", by_alias=True)
        )
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


def cli_root(
    target: Path,
    pinned: Path | None = None,
    *,
    profile: str | None = None,
    overrides: dict[str, object] | None = None,
) -> Path:
    """The project root for a CLI target, recorded so the root callback reports the config keys no
    model declares exactly once.

    ``pinned`` short-circuits the search (``scan --root``). ``profile`` and ``overrides`` are the
    run's extra config layers, so a typo in ``--config-json`` is reported like one in the TOML.
    """
    return NOTICE.record(
        find_root(target) if pinned is None else pinned,
        profile=profile,
        overrides=overrides,
    )


def flush_config_notice() -> None:
    """Print this invocation's config notice on stderr, so stdout stays parseable.

    The notice writes the lines; this only chooses the sink and the styling, with its closing
    advice dimmed.
    """
    for line in NOTICE.report():
        if line == ConfigNotice.HINT:
            err_console.print(f"[dim]{line}[/dim]")
        else:
            err_console.print(f"[yellow]warning:[/yellow] {line}")


@contextmanager
def config_errors_as_one_line() -> Iterator[None]:
    """Turn any configuration failure raised under this block into one clean line and exit 1.

    Every command surface guards with this rather than its own ``except``, so a new kind of
    config failure reaches a user as a message from all of them at once.
    """
    try:
        yield
    except (ConfigError, ValidationError) as exc:
        fail(f"invalid config: {format_config_error(exc)}")


def load_user(root: Path) -> UserSettings:
    """:func:`auditor.user_settings.load_user_settings` with a bad settings file turned into one
    clean line, the way :func:`load_settings` does it for repo policy."""
    try:
        return load_user_settings(root)
    except ValidationError as exc:
        fail(f"invalid user config: {format_config_error(exc)}")


def load_settings(
    root: Path,
    *,
    profile: str | None = None,
    allow_local_plugins: bool = False,
    loader: PluginLoader | None = None,
    overrides: dict[str, object] | None = None,
) -> AuditorSettings:
    """:func:`auditor.config.load_config` with every config failure turned into one clean line.

    The same split as :func:`_connect`: the library raises so a caller can handle it, and the CLI
    edge is where a traceback becomes a message.
    """
    with config_errors_as_one_line():
        settings = load_config(
            root,
            profile=profile,
            allow_local_plugins=allow_local_plugins,
            loader=loader,
            overrides=overrides,
        )
    NOTICE.record_policy(settings.unknown_keys)
    return settings


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


async def open_index(root: Path) -> IndexStore:
    """``open_repo_index`` with a repair instruction instead of a raw schema error.

    Use as ``async with await open_index(root)``.
    """
    return await _repaired(open_repo_index(root))


async def open_shared_index() -> IndexStore:
    """Connect to the shared global index for cross-repo operations (listing/forgetting repos),
    not bound to any one repo's partition."""
    return await _repaired(IndexStore.connect(index_db_path(), DEFAULT_REPO))


async def _repaired(opening: Awaitable[IndexStore]) -> IndexStore:
    """Await an index connect, turning an unmigratable schema into a one-line repair instruction.

    A declaration SQLite cannot add to an existing identity table would otherwise raise on every
    command, with no command left that could repair it.
    """
    try:
        return await opening
    except UnmigratableColumn as exc:
        db_path = index_db_path()
        fail(
            f"the index cannot be upgraded: {exc}. Delete it and re-scan: rm {db_path}"
        )


def emit(rendered: str, output: Path | None) -> None:
    """Write a rendered report to ``output`` (with a stderr note) or echo it to stdout.
    Missing parent directories of ``output`` are created; an unwritable path exits cleanly."""
    if output is None:
        typer.echo(rendered)
        return
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        fail(f"cannot write {output}: {exc.strerror}")
    err_console.print(f"[green]✓[/green] wrote [bold]{output}[/bold]")
