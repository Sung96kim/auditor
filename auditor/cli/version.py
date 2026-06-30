"""``auditor version`` — print the installed auditr version."""

import importlib.metadata
import platform

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from auditor import __version__
from auditor.cli.apps import app

_DIST_NAME = "auditr"
_ACCENT = "#7C7CFF"
_console = Console()


def _resolve_version() -> str:
    try:
        return importlib.metadata.version(_DIST_NAME)
    except importlib.metadata.PackageNotFoundError:
        return __version__  # source checkout with no installed dist


@app.command()
def version() -> None:
    """Print the installed auditr version."""
    ver = _resolve_version()

    # Piped / non-TTY (scripts, agents): stay plain so the output is trivially parseable.
    if not _console.is_terminal:
        _console.print(f"{_DIST_NAME} {ver}")
        return

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim")
    grid.add_column(style=f"bold {_ACCENT}")
    grid.add_row("version", ver)
    grid.add_row("python", platform.python_version())
    grid.add_row("backend", f"{platform.system().lower()}/{platform.machine()}")

    _console.print(
        Panel.fit(
            grid,
            title=f"[bold {_ACCENT}]◆ auditr[/]",
            border_style=_ACCENT,
            padding=(0, 1),
        )
    )
