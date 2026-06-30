"""``auditor self update`` — check PyPI for a newer ``auditr`` release and optionally install it."""

import importlib.metadata
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from typing import Any

import typer
from packaging.version import parse as parse_version
from rich.panel import Panel
from rich.table import Table

from auditor.cli.banner import animate, logo
from auditor.cli.console import ACCENT, err_console

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
        with err_console.status("Checking PyPI…"):
            cur = installed_version()
            data = fetch_pypi()
            latest = pick_latest(
                data["releases"], data["info_version"], include_pre=pre
            )
    except RuntimeError as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from exc

    newer = is_newer(cur, latest)
    info = Table.grid(padding=(0, 3))
    info.add_column(style="dim")
    info.add_column()
    info.add_row("version", f"[bold {ACCENT}]{cur}[/]")
    if newer:
        info.add_row("latest", f"[green]{latest}[/green]")
        info.add_row("status", "[yellow]↑ update available[/yellow]")
    else:
        info.add_row("status", "[green]✓ up to date[/green]")

    body = Table.grid()
    body.add_row(logo())
    body.add_row("")
    body.add_row(info)
    err_console.print(
        Panel.fit(body, title="version check", border_style=ACCENT, padding=(1, 3))
    )

    # the box already states the up-to-date / update-available status — no extra line.
    if not newer or check:
        return

    if not yes:
        typer.confirm(f"Upgrade auditr {cur} → {latest}?", abort=True)

    try:
        cmd = upgrade_command()
    except RuntimeError as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from exc

    # stderr → temp file (not PIPE) so a chatty upgrade can't fill a pipe buffer and wedge while
    # we animate; stdout is hidden so it doesn't fight the animation.
    with tempfile.TemporaryFile(mode="w+") as errf:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=errf)
        animate(lambda: proc.poll() is not None, f"upgrading to {latest}…")
        proc.wait()
        errf.seek(0)
        errout = errf.read().strip()

    if proc.returncode == 0:
        err_console.print(
            f"[green]✓[/green] upgraded to {latest} — restart any running auditr processes"
        )
    else:
        err_console.print(
            f"[red]upgrade failed[/red] (exit {proc.returncode})\n"
            f"command: {' '.join(cmd)}"
        )
        if errout:
            err_console.print(f"[dim]{errout}[/dim]")
        raise typer.Exit(1)
