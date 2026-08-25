"""``auditor discover`` — list auditable files with their classified role."""

from pathlib import Path

import typer

from auditor.cli.apps import app
from auditor.cli.helpers import (
    load_settings,
    parse_config_json,
    present,
    require_exists,
    warn_unknown_config,
)
from auditor.cli.options import ConfigJson, DirTarget
from auditor.cli.render import render_discover
from auditor.discovery import FileDiscovery, find_root
from auditor.roles import RoleClassifier


@app.command()
def discover(
    target: DirTarget = Path("."),
    config_json: ConfigJson = None,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """List auditable files with their classified role."""
    require_exists(target)
    root = find_root(target)
    settings = load_settings(root, overrides=parse_config_json(config_json))
    warn_unknown_config(settings.unknown_keys)
    classifier = RoleClassifier(settings.role_globs)
    out = []
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
        out.append({"file": rel, "role": role.value})
    present(out, render_discover, as_json=json_)
