"""``auditor plugins list`` — show every loaded detector/language/reporter/profile + source."""

from pathlib import Path

import typer

from auditor.cli.helpers import _echo_json
from auditor.cli.options import RootArg
from auditor.config import load_config
from auditor.discovery import find_root
from auditor.plugins import PluginLoader
from auditor.registry import REGISTRY

plugins_app = typer.Typer(no_args_is_help=True, help="Inspect loaded plugins.")


@plugins_app.command("list")
def plugins_list(target: RootArg = Path(".")) -> None:
    """Show every loaded detector/language/reporter/profile and its source."""
    loader = PluginLoader()
    load_config(find_root(target), loader=loader)
    payload = REGISTRY.snapshot()
    payload["warnings"] = loader.warnings
    _echo_json(payload)
