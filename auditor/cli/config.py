"""``auditor config show`` — print the resolved configuration."""

from pathlib import Path

import typer
from pydantic import ValidationError

from auditor.cli.helpers import (
    fail,
    format_config_error,
    parse_config_json,
    present,
    warn_unknown_config,
)
from auditor.cli.options import ConfigJson, RootArg
from auditor.cli.render import render_config_show
from auditor.config import load_config_report
from auditor.discovery import find_root

config_app = typer.Typer(no_args_is_help=True, help="Inspect resolved configuration.")


@config_app.command("show")
def config_show(
    target: RootArg = Path("."),
    config_json: ConfigJson = None,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Print the resolved configuration."""
    try:
        loaded = load_config_report(
            find_root(target), overrides=parse_config_json(config_json)
        )
    except ValidationError as exc:
        fail(f"invalid config — {format_config_error(exc)}")
    warn_unknown_config(loaded.unknown_keys)
    present(loaded.settings.model_dump(mode="json"), render_config_show, as_json=json_)
