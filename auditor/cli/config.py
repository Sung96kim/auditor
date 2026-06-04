"""``auditor config show`` — print the resolved configuration."""

from pathlib import Path

import typer

from auditor.cli.helpers import _echo_json
from auditor.cli.options import RootArg
from auditor.config import load_config
from auditor.discovery import find_root

config_app = typer.Typer(no_args_is_help=True, help="Inspect resolved configuration.")


@config_app.command("show")
def config_show(target: RootArg = Path(".")) -> None:
    """Print the resolved configuration."""
    settings = load_config(find_root(target))
    _echo_json(settings.model_dump(mode="json"))
