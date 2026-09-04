"""``auditor discover`` — list auditable files with their classified role."""

from pathlib import Path

import typer

from auditor.cli.apps import app
from auditor.cli.helpers import (
    cli_root,
    load_settings,
    parse_config_json,
    present,
    require_exists,
)
from auditor.cli.options import ConfigJson, DirTarget
from auditor.cli.payloads import DiscoveredFile, DiscoverReport
from auditor.cli.render import render_discover
from auditor.discovery import FileDiscovery
from auditor.roles import RoleClassifier


@app.command()
def discover(
    target: DirTarget = Path("."),
    config_json: ConfigJson = None,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """List auditable files with their classified role."""
    require_exists(target)
    overrides = parse_config_json(config_json)
    root = cli_root(target, overrides=overrides)
    settings = load_settings(root, overrides=overrides)
    classifier = RoleClassifier(settings.role_globs)
    rows: list[DiscoveredFile] = []
    discovery = FileDiscovery(
        root,
        exclude_globs=tuple(settings.exclude),
        respect_gitignore=settings.respect_gitignore,
    )
    for path in discovery.files(target):
        rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        role = classifier.classify(
            rel, path.read_text(encoding="utf-8", errors="replace")
        )
        rows.append(DiscoveredFile(file=rel, role=role))
    present(DiscoverReport(tuple(rows)), render_discover, as_json=json_)
