"""Shared CLI helpers: clean one-line error exits, the async-run spinner bridge, JSON echo,
format validation, report emission, and index-path resolution. Command modules import what
they need; anything used by a single command lives in that command's module instead.
"""

import asyncio
import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, NoReturn, TypeVar

import typer

from auditor.cli.apps import _status
from auditor.registry import REGISTRY

_T = TypeVar("_T")


def _echo_json(payload: object) -> None:
    typer.echo(json.dumps(payload, indent=2))


def _fail(message: str) -> NoReturn:
    """Emit a clean one-line error to stderr and exit non-zero (no traceback)."""
    _status.print(f"[red]error:[/red] {message}")
    raise typer.Exit(1)


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
    with _status.status(message, spinner="dots"):
        return asyncio.run(coro)


def _index_db(root: Path) -> Path:
    return root / ".auditor" / "index.db"


def _emit(rendered: str, output: Path | None) -> None:
    """Write a rendered report to ``output`` (with a stderr note) or echo it to stdout."""
    if output is None:
        typer.echo(rendered)
        return
    output.write_text(rendered, encoding="utf-8")
    typer.echo(f"wrote {output}", err=True)
