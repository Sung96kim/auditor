"""``auditor config show|check`` — print the resolved configuration, or report unknown keys."""

from pathlib import Path

import typer

from auditor.cli.helpers import (
    cli_root,
    fail,
    load_settings,
    load_user,
    parse_config_json,
    present,
)
from auditor.cli.options import ConfigJson, RootArg, UserConfig
from auditor.cli.payloads import ConfigCheckReport
from auditor.cli.render import render_config_check, render_config_show
from auditor.config import AuditorSettings
from auditor.config_notice import NOTICE
from auditor.user_settings import (
    UserSettings,
    user_key_report,
)

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
    settings: AuditorSettings | UserSettings
    if user:
        if overrides is not None:
            fail(
                "--config-json applies to repo policy; it cannot be combined with --user"
            )
        settings = load_user(root)
    else:
        settings = load_settings(root, overrides=overrides)
    present(settings, render_config_show, as_json=json_)


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
    load_user(root)
    present(
        ConfigCheckReport(
            root=str(root),
            policy_unknown=settings.unknown_keys,
            user_unknown=tuple(user_key_report(root).unknown),
        ),
        render_config_check,
        as_json=json_,
    )
