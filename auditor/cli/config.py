"""``auditor config show|check`` — print the resolved configuration, or report unknown keys."""

from pathlib import Path

import typer
from pydantic import BaseModel, ValidationError

from auditor.cli.helpers import (
    fail,
    format_config_error,
    parse_config_json,
    present,
    warn_unknown_config,
)
from auditor.cli.options import ConfigJson, RootArg, UserConfig
from auditor.cli.render import render_config_check, render_config_show
from auditor.config import (
    AuditorSettings,
    load_config_report,
    merged_config_dict,
    unknown_config_keys,
)
from auditor.discovery import find_root
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
    root = find_root(target)
    settings: BaseModel
    if user:
        try:
            settings = load_user_settings(root)
        except ValidationError as exc:
            fail(f"invalid user config — {format_config_error(exc)}")
    else:
        try:
            loaded = load_config_report(root, overrides=parse_config_json(config_json))
        except ValidationError as exc:
            fail(f"invalid config — {format_config_error(exc)}")
        warn_unknown_config(loaded.unknown_keys)
        settings = loaded.settings
    present(settings.model_dump(mode="json"), render_config_show, as_json=json_)


@config_app.command("check")
def config_check(
    target: RootArg = Path("."),
    config_json: ConfigJson = None,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Report config keys no model declares. Exits non-zero when a value fails validation."""
    root = find_root(target)
    overrides = parse_config_json(config_json)
    raw = merged_config_dict(root, overrides=overrides)
    try:
        load_config_report(root, overrides=overrides)
    except ValidationError as exc:
        fail(f"invalid config — {format_config_error(exc)}")
    try:
        load_user_settings(root)
    except ValidationError as exc:
        fail(f"invalid user config — {format_config_error(exc)}")
    # The unknown keys are this command's payload, so it never also calls warn_unknown_config.
    present(
        {
            "root": str(root),
            "policy_unknown": unknown_config_keys(raw, AuditorSettings),
            "user_unknown": unknown_user_keys(root),
        },
        render_config_check,
        as_json=json_,
    )
