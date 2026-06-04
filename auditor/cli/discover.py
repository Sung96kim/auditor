"""``auditor discover`` — list auditable files with their classified role."""

from pathlib import Path

from auditor.cli.apps import app
from auditor.cli.helpers import _echo_json, _require_exists
from auditor.cli.options import DirTarget
from auditor.config import load_config
from auditor.discovery import FileDiscovery, find_root
from auditor.roles import RoleClassifier


@app.command()
def discover(target: DirTarget = Path(".")) -> None:
    """List auditable files with their classified role."""
    _require_exists(target)
    root = find_root(target)
    classifier = RoleClassifier(load_config(root).role_globs)
    out = []
    for path in FileDiscovery(root).files(target):
        rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        role = classifier.classify(
            rel, path.read_text(encoding="utf-8", errors="replace")
        )
        out.append({"file": rel, "role": role.value})
    _echo_json(out)
