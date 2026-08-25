"""``auditor plugins list`` — show every loaded detector/language/reporter + its source."""

from pathlib import Path

import typer

from auditor.cli.helpers import cli_root, load_settings, present
from auditor.cli.options import RootArg
from auditor.cli.render import render_plugins_list
from auditor.plugins import PluginLoader
from auditor.registry import REGISTRY

plugins_app = typer.Typer(no_args_is_help=True, help="Inspect loaded plugins.")


@plugins_app.command("list")
def plugins_list(
    target: RootArg = Path("."),
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Show every loaded detector/language auditor/reporter and its source."""
    loader = PluginLoader()
    load_settings(
        cli_root(target), loader=loader
    )  # registers every plugin so REGISTRY.snapshot() below sees them
    payload = REGISTRY.snapshot()
    payload["warnings"] = loader.warnings
    present(payload, render_plugins_list, as_json=json_)
