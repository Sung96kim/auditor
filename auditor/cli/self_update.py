"""``auditor self update`` — check PyPI for a newer ``auditr`` release and optionally install it."""

import importlib.metadata
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, NamedTuple

import typer
from packaging.requirements import Requirement
from packaging.version import parse as parse_version
from rich.panel import Panel
from rich.table import Table

from auditor.cli.banner import animate, logo
from auditor.cli.console import ACCENT, err_console

self_app = typer.Typer(no_args_is_help=True, help="Manage the auditor install.")

_PYPI_URL = "https://pypi.org/pypi/auditr/json"
_DIST_NAME = "auditr"


class InstallContext(NamedTuple):
    """How auditr was installed, so an upgrade can reproduce it. ``uv_tool`` installs must be
    upgraded with ``uv tool`` (their venv has no pip); ``extras``/``python`` come from the uv
    receipt when present, else best-effort from the environment."""

    uv_tool: bool
    python: str | None
    extras: tuple[str, ...]


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


def _norm_extra(name: str) -> str:
    """PEP 685 extra-name normalization (lowercase, runs of ``-_.`` → single ``-``)."""
    return re.sub(r"[-_.]+", "-", name.lower())


def guard_extras(
    desired: list[str] | tuple[str, ...], provided: list[str] | None
) -> tuple[list[str], list[str]]:
    """Split ``desired`` into (kept, dropped) against the extras the target release provides.
    ``provided=None`` means unknown (older metadata) → keep all, drop nothing. Prevents an
    upgrade from hard-failing on an extra that was removed upstream."""
    wanted = sorted({_norm_extra(e) for e in desired})
    if provided is None:
        return wanted, []
    prov = {_norm_extra(e) for e in provided}
    kept = [e for e in wanted if e in prov]
    dropped = [e for e in wanted if e not in prov]
    return kept, dropped


def _uv_tool_receipt() -> Path | None:
    """The uv-tool receipt for this install, if auditr is running as a uv tool (its venv sits at
    ``<data>/uv/tools/<name>/`` with a ``uv-receipt.toml`` recording the requirement)."""
    prefix = Path(sys.prefix)
    receipt = prefix / "uv-receipt.toml"
    return receipt if receipt.is_file() and "tools" in prefix.parts else None


def _receipt_context(receipt: Path) -> tuple[tuple[str, ...], str | None]:
    data = tomllib.loads(receipt.read_text(encoding="utf-8"))
    tool = data.get("tool", {})
    extras: tuple[str, ...] = ()
    for req in tool.get("requirements", []):
        if req.get("name") == _DIST_NAME:
            extras = tuple(req.get("extras", ()))
            break
    python = tool.get("python")
    return extras, python if isinstance(python, str) else None


def _is_installed(dist: str) -> bool:
    try:
        importlib.metadata.version(dist)
        return True
    except importlib.metadata.PackageNotFoundError:
        return False


def _installed_extras_from_env() -> tuple[str, ...]:
    """Best-effort extras present in a pip/pipx/venv install: pip records no "requested extras",
    so infer from which extra-gated dependencies are installed. An extra counts as present only
    if *all* of its dependencies resolve (conservative — avoids over-claiming)."""
    try:
        meta = importlib.metadata.metadata(_DIST_NAME)
    except importlib.metadata.PackageNotFoundError:
        return ()
    all_extras = meta.get_all("Provides-Extra") or []
    deps: dict[str, list[str]] = {e: [] for e in all_extras}
    for raw in meta.get_all("Requires-Dist") or []:
        req = Requirement(raw)
        if req.marker is None:
            continue  # unconditional dep — not gated by any extra
        for e in all_extras:
            if req.marker.evaluate({"extra": e}) and not req.marker.evaluate(
                {"extra": ""}
            ):
                deps[e].append(req.name)
    return tuple(
        e for e, ds in deps.items() if ds and all(_is_installed(d) for d in ds)
    )


def install_context() -> InstallContext:
    """Detect how auditr was installed so the upgrade reproduces it (mechanism + extras)."""
    if (receipt := _uv_tool_receipt()) is not None:
        extras, python = _receipt_context(receipt)
        return InstallContext(uv_tool=True, python=python, extras=extras)
    return InstallContext(
        uv_tool=False, python=None, extras=_installed_extras_from_env()
    )


def upgrade_command(
    extras: list[str], *, uv_tool: bool, python: str | None, version: str
) -> list[str]:
    """The command that upgrades auditr to ``version`` while preserving ``extras``. A uv-tool
    install must go through ``uv tool`` (its venv has no pip); a pip/venv install uses pip
    (falling back to ``uv pip``). The spec is pinned so the installed version matches what the
    user was told (and to honor a ``--pre`` pick without extra prerelease flags)."""
    suffix = f"[{','.join(extras)}]" if extras else ""
    spec = f"{_DIST_NAME}{suffix}=={version}"
    if uv_tool:
        cmd = ["uv", "tool", "install", spec, "--force"]
        if python:
            cmd += ["--python", python]
        return cmd
    if importlib.util.find_spec("pip") is not None:
        return [sys.executable, "-m", "pip", "install", "--upgrade", spec]
    if shutil.which("uv") is not None:
        return ["uv", "pip", "install", "--upgrade", spec]
    raise RuntimeError(
        f"no pip/uv found; upgrade manually: pip install --upgrade {_DIST_NAME}{suffix}"
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
        "provides_extra": data["info"].get("provides_extra"),
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

    ctx = install_context()
    extras, dropped = guard_extras(ctx.extras, data.get("provides_extra"))
    if dropped:
        err_console.print(
            f"[yellow]note:[/yellow] {latest} no longer offers extra(s) "
            f"{', '.join(dropped)} — dropping from the upgrade"
        )
    try:
        cmd = upgrade_command(
            extras, uv_tool=ctx.uv_tool, python=ctx.python, version=latest
        )
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
