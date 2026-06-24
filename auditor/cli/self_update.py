"""``auditor self update`` — check PyPI for a newer ``auditr`` release and optionally install it."""

import importlib.metadata
import importlib.util
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

import typer
from packaging.version import parse as parse_version
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

_console = Console(stderr=True)

self_app = typer.Typer(no_args_is_help=True, help="Manage the auditor install.")

_PYPI_URL = "https://pypi.org/pypi/auditr/json"
_DIST_NAME = "auditr"


def installed_version() -> str:
    return importlib.metadata.version(_DIST_NAME)


def pick_latest(releases: list[str], info_version: str, *, include_pre: bool) -> str:
    candidates = [parse_version(v) for v in releases]
    if not include_pre:
        stable = [v for v in candidates if not v.is_prerelease]
        candidates = stable if stable else [parse_version(info_version)]
    return str(max(candidates))


def is_newer(installed: str, latest: str) -> bool:
    return parse_version(latest) > parse_version(installed)


def upgrade_command() -> list[str]:
    if importlib.util.find_spec("pip") is not None:
        return [sys.executable, "-m", "pip", "install", "--upgrade", _DIST_NAME]
    if shutil.which("uv") is not None:
        return ["uv", "pip", "install", "--upgrade", _DIST_NAME]
    raise RuntimeError(
        f"no pip/uv found; upgrade manually: pip install --upgrade {_DIST_NAME}"
    )


def fetch_pypi(timeout: int = 15) -> dict[str, Any]:
    req = urllib.request.Request(
        _PYPI_URL,
        headers={"User-Agent": f"auditr/{installed_version()} urllib"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"could not reach PyPI: {exc}") from exc
    return {
        "info_version": data["info"]["version"],
        "releases": list(data["releases"].keys()),
    }


@self_app.command("update")
def self_update(
    check: bool = typer.Option(False, "--check", help="Only check; don't install."),
    pre: bool = typer.Option(False, "--pre", help="Include pre-releases."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Check for a newer auditr release on PyPI and optionally install it."""
    try:
        with _console.status("Checking PyPI…"):
            cur = installed_version()
            data = fetch_pypi()
            latest = pick_latest(
                data["releases"], data["info_version"], include_pre=pre
            )
    except RuntimeError as exc:
        _console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from exc

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_column()
    table.add_column()
    table.add_row("auditr", f"[cyan]{cur}[/cyan]", "→", f"[green]{latest}[/green]")
    _console.print(Panel(table, title="version check", border_style="dim"))

    if not is_newer(cur, latest):
        _console.print(f"[green]✓[/green] already on the latest version ({cur})")
        return

    if check:
        _console.print(
            f"[yellow]↑[/yellow] update available: {cur} → {latest}"
            f"  (run [bold]auditr self update[/bold])"
        )
        return

    if not yes:
        typer.confirm(f"Upgrade auditr {cur} → {latest}?", abort=True)

    try:
        cmd = upgrade_command()
    except RuntimeError as exc:
        _console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from exc

    with _console.status("Upgrading…"):
        result = subprocess.run(cmd, check=False)

    if result.returncode == 0:
        _console.print(
            f"[green]✓[/green] upgraded to {latest} — restart any running auditr processes"
        )
    else:
        _console.print(
            f"[red]upgrade failed[/red] (exit {result.returncode})\n"
            f"command: {' '.join(cmd)}"
        )
        raise typer.Exit(1)
