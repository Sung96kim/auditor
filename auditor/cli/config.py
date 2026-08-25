"""``auditor config show|check`` — print the resolved configuration, or report unknown keys."""

from pathlib import Path

import typer
from pydantic import BaseModel, ValidationError

from auditor.cli.helpers import (
    cli_root,
    fail,
    format_config_error,
    load_settings,
    parse_config_json,
    present,
)
from auditor.cli.options import ConfigJson, RootArg, UserConfig
from auditor.cli.render import render_config_check, render_config_show
from auditor.config_notice import NOTICE
from auditor.user_settings import load_user_settings, unknown_user_keys

config_app = typer.Typer(no_args_is_help=True, help="Inspect resolved configuration.")


@config_app.command("show")
def config_show(
    target: RootArg = Path("."),
    config_json: ConfigJson = None,
    user: UserConfig = False,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Print the resolved configuration (repo policy, or --user for the user settings)."""
    overrides = parse_config_json(config_json)
    root = cli_root(target, overrides=overrides)
    settings: BaseModel
    if user:
        if overrides is not None:
            fail(
                "--config-json applies to repo policy; it cannot be combined with --user"
            )
        try:
            settings = load_user_settings(root)
        except ValidationError as exc:
            fail(f"invalid user config: {format_config_error(exc)}")
    else:
        settings = load_settings(root, overrides=overrides)
    present(settings.model_dump(mode="json"), render_config_show, as_json=json_)


@config_app.command("check")
def config_check(
    target: RootArg = Path("."),
    config_json: ConfigJson = None,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Report config keys no model declares. Exits non-zero when a value fails validation."""
    overrides = parse_config_json(config_json)
    root = cli_root(target, overrides=overrides)
    NOTICE.owned()  # the unknown keys are this command's payload; a stderr block would repeat it
    settings = load_settings(root, overrides=overrides)
    try:
        load_user_settings(root)
    except ValidationError as exc:
        fail(f"invalid user config: {format_config_error(exc)}")
    present(
        {
            "root": str(root),
            "policy_unknown": list(settings.unknown_keys),
            "user_unknown": unknown_user_keys(root),
        },
        render_config_check,
        as_json=json_,
    )
