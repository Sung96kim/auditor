"""``auditor plugins list`` — show every loaded detector/language/reporter/profile + source."""

from pathlib import Path

import typer

from auditor.cli.helpers import _present
from auditor.cli.options import RootArg
from auditor.cli.render import render_plugins_list
from auditor.config import load_config
from auditor.discovery import find_root
from auditor.plugins import PluginLoader
from auditor.registry import REGISTRY

plugins_app = typer.Typer(no_args_is_help=True, help="Inspect loaded plugins.")


@plugins_app.command("list")
def plugins_list(
    target: RootArg = Path("."),
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Show every loaded detector/language/reporter/profile and its source."""
    loader = PluginLoader()
    load_config(find_root(target), loader=loader)
    payload = REGISTRY.snapshot()
    payload["warnings"] = loader.warnings
    _present(payload, render_plugins_list, as_json=json_)
