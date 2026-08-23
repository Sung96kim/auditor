"""``auditor init`` — create or refresh the user config home under ``$AUDITOR_HOME``.

Writes only the marker keys: defaults live in ``UserSettings``, so a later default change is not
pinned forever and a value the user chose stays distinguishable from one init wrote.
"""

import json
from pathlib import Path

import typer

from auditor.cli.apps import app
from auditor.cli.helpers import fail, present
from auditor.cli.options import CleanStatus, InitCheck, InitMigrate, InitRepo, RootArg
from auditor.cli.render import render_init
from auditor.discovery import find_root
from auditor.paths import (
    auditor_home,
    ensure_repo_dir,
    read_json_dict,
    repo_dir,
    user_config_path,
    user_schema_path,
)
from auditor.user_settings import UserSettings, unknown_user_keys

CONFIG_VERSION = 1
_GLOBAL_SCHEMA_REF = "./config.schema.json"
_REPO_SCHEMA_REF = "../../config.schema.json"


def _migrate(current_version: int) -> None:
    """Bring a settings file written by an older ``config_version`` up to the current one.

    A no-op while ``CONFIG_VERSION`` is 1: there is no older version to come from. The seam exists
    so the first bump has exactly one place to add its step.
    """


def _write_markers(path: Path, schema_ref: str) -> bool:
    """Set ``$schema`` and ``config_version`` on a settings file, keeping the user's own keys.
    Returns whether anything changed."""
    current = read_json_dict(path)
    updated = {**current, "$schema": schema_ref, "config_version": CONFIG_VERSION}
    if path.exists() and updated == current:
        return False
    path.write_text(json.dumps(updated, indent=2) + "\n")
    return True


def _moved_from(root: Path) -> str | None:
    """The root a breadcrumb was written for, when that directory is gone. A sibling worktree
    keeps its own root alive, so it shares the settings rather than counting as a move."""
    recorded = read_json_dict(repo_dir(root) / "root.json").get("root")
    if not isinstance(recorded, str) or recorded == str(root.resolve()):
        return None
    return None if Path(recorded).exists() else recorded


@app.command()
def init(
    target: RootArg = Path("."),
    repo: InitRepo = False,
    check: InitCheck = False,
    migrate: InitMigrate = False,
    clean_status: CleanStatus = False,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Create or refresh the user config home ($AUDITOR_HOME)."""
    if migrate and not repo:
        fail("--migrate requires --repo")
    root = find_root(target)
    home = auditor_home()
    written: list[str] = []
    moved = _moved_from(root)
    legacy = root / ".auditor" / ".status.json"
    had_legacy = legacy.exists()

    if not check:
        home.mkdir(parents=True, exist_ok=True)
        found = read_json_dict(user_config_path()).get("config_version")
        _migrate(found if isinstance(found, int) else CONFIG_VERSION)
        schema = json.dumps(UserSettings.model_json_schema(), indent=2) + "\n"
        if not user_schema_path().exists() or user_schema_path().read_text() != schema:
            user_schema_path().write_text(schema)
            written.append(str(user_schema_path()))
        if _write_markers(user_config_path(), _GLOBAL_SCHEMA_REF):
            written.append(str(user_config_path()))
        if repo:
            target_dir = ensure_repo_dir(root)
            if _write_markers(target_dir / "config.json", _REPO_SCHEMA_REF):
                written.append(str(target_dir / "config.json"))
            if migrate and moved is not None:
                crumb = target_dir / "root.json"
                crumb.write_text(
                    json.dumps({**read_json_dict(crumb), "root": str(root.resolve())})
                )
                written.append(str(crumb))
        if clean_status and had_legacy:
            legacy.unlink()

    present(
        {
            "home": str(home),
            "config": str(user_config_path()),
            "schema": str(user_schema_path()),
            "repo_dir": str(repo_dir(root)) if repo else None,
            "written": written,
            "unknown_keys": unknown_user_keys(root),
            "moved_from": moved,
            "legacy_status": str(legacy) if had_legacy else None,
        },
        render_init,
        as_json=json_,
    )
