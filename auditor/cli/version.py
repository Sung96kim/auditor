"""``auditor version`` — print the installed auditr version (a rich banner in a terminal)."""

import importlib.metadata
import platform
from pathlib import Path

from rich.panel import Panel
from rich.table import Table

import auditor
from auditor import __version__
from auditor.cli.apps import app
from auditor.cli.banner import logo
from auditor.cli.console import ACCENT, console
from auditor.cli.self_update import fetch_pypi, is_newer, pick_latest

_DIST_NAME = "auditr"


def _resolve_version() -> str:
    try:
        return importlib.metadata.version(_DIST_NAME)
    except importlib.metadata.PackageNotFoundError:
        return __version__  # source checkout with no installed dist


def _install_path() -> Path:
    """Where the `auditor` package is installed (site-packages, or the repo for a source run)."""
    return Path(auditor.__file__).resolve().parent


def _update_status(ver: str) -> str:
    """Best-effort PyPI check (short timeout, degrades to 'offline')."""
    try:
        data = fetch_pypi(timeout=3)
        latest = pick_latest(data["releases"], data["info_version"], include_pre=False)
    except (RuntimeError, KeyError):
        return "[dim]offline — couldn't reach PyPI[/dim]"
    if is_newer(ver, latest):
        return f"[yellow]↑ {latest} available[/yellow] [dim](auditr self update)[/dim]"
    return f"[green]✓ up to date[/green] [dim]({ver})[/dim]"


@app.command()
def version() -> None:
    """Print the installed auditr version (with install + update info in a terminal)."""
    ver = _resolve_version()

    # Piped / non-TTY (scripts, agents): stay plain + offline so it's trivially parseable.
    if not console.is_terminal:
        console.print(f"{_DIST_NAME} {ver}")
        return

    rows = Table.grid(padding=(0, 3))
    rows.add_column(style="dim")
    rows.add_column()
    rows.add_row("version", f"[bold {ACCENT}]{ver}[/]")
    rows.add_row("python", platform.python_version())
    rows.add_row("install", str(_install_path()))
    rows.add_row("updates", _update_status(ver))

    body = Table.grid()
    body.add_row(logo())
    body.add_row("")
    body.add_row(rows)
    console.print(
        Panel.fit(
            body, title=f"[bold {ACCENT}]auditr[/]", border_style=ACCENT, padding=(1, 3)
        )
    )
