"""``auditor plugins list`` — show every loaded detector/language/reporter + its source."""

from pathlib import Path

import typer

from auditor.cli.helpers import present, warn_unknown_config
from auditor.cli.options import RootArg
from auditor.cli.render import render_plugins_list
from auditor.config import load_config_report
from auditor.discovery import find_root
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
    loaded = load_config_report(find_root(target), loader=loader)
    warn_unknown_config(loaded.unknown_keys)
    payload = REGISTRY.snapshot()
    payload["warnings"] = loader.warnings
    present(payload, render_plugins_list, as_json=json_)
