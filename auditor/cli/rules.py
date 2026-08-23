"""``auditor rules list`` — enumerate every registered detector rule."""

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from auditor.cli.console import err_console
from auditor.cli.helpers import fail, format_config_error, present
from auditor.cli.options import RootArg
from auditor.cli.render import render_rules_list
from auditor.config import load_config
from auditor.discovery import find_root
from auditor.plugins import PluginLoader
from auditor.registry import REGISTRY

rules_app = typer.Typer(no_args_is_help=True, help="Inspect detector rules.")


def _known_standards() -> set[str]:
    return {
        ref.split(":", 1)[0]
        for rid in REGISTRY.rule_ids()
        for ref in REGISTRY.detector(rid).standard_refs
    }


@rules_app.command("list")
def rules_list(
    target: RootArg = Path("."),
    category: Annotated[
        str | None, typer.Option("-c", "--category", help="Filter by category.")
    ] = None,
    standard: Annotated[
        str | None, typer.Option("-s", "--standard", help="bandit | owasp coverage.")
    ] = None,
    framework: Annotated[
        str | None,
        typer.Option("-f", "--framework", help="Filter by framework (e.g. pytest)."),
    ] = None,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """List every registered detector rule, plus the target repo's trusted plugin rules."""
    loader = PluginLoader()
    try:
        # loads the repo's plugins as a side effect, so their rules are registered before we list
        load_config(find_root(target), loader=loader)
    except ValidationError as exc:
        fail(f"invalid config — {format_config_error(exc)}")
    for warning in loader.warnings:
        err_console.print(f"[yellow]warning:[/] {warning}")
    if category is not None and category not in REGISTRY.categories():
        fail(
            f"unknown category {category!r}; choose from {sorted(REGISTRY.categories())}"
        )
    if standard is not None and standard not in (known := _known_standards()):
        fail(f"unknown standard {standard!r}; choose from {sorted(known)}")
    if framework is not None and framework not in REGISTRY.frameworks():
        fail(
            f"unknown framework {framework!r}; choose from {sorted(REGISTRY.frameworks())}"
        )
    rows = REGISTRY.rule_rows(category=category, standard=standard, framework=framework)
    present([row.model_dump() for row in rows], render_rules_list, as_json=json_)
