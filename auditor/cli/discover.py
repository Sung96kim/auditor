"""``auditor discover`` — list auditable files with their classified role."""

from pathlib import Path

from pydantic import ValidationError

from auditor.cli.apps import app
from auditor.cli.helpers import (
    _echo_json,
    _fail,
    _format_config_error,
    _parse_config_json,
    _require_exists,
)
from auditor.cli.options import ConfigJson, DirTarget
from auditor.config import load_config
from auditor.discovery import FileDiscovery, find_root
from auditor.roles import RoleClassifier


@app.command()
def discover(target: DirTarget = Path("."), config_json: ConfigJson = None) -> None:
    """List auditable files with their classified role."""
    _require_exists(target)
    root = find_root(target)
    try:
        settings = load_config(root, overrides=_parse_config_json(config_json))
    except ValidationError as exc:
        _fail(f"invalid config — {_format_config_error(exc)}")
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
    _echo_json(out)
