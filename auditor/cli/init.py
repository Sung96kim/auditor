"""``auditor init`` — create or refresh the user config home under ``$AUDITOR_HOME``.

Writes only the marker keys: defaults live in ``UserSettings``, so a later default change is not
pinned forever and a value the user chose stays distinguishable from one init wrote.
"""

import json
from pathlib import Path

import typer

from auditor.cli.apps import app
from auditor.cli.helpers import cli_root, fail, present
from auditor.cli.options import (
    CleanStatus,
    InitCheck,
    InitForce,
    InitMigrate,
    InitRepo,
    RootArg,
)
from auditor.cli.payloads import InitReport
from auditor.cli.render import render_init
from auditor.config_notice import NOTICE, ConfigNotice
from auditor.paths import (
    auditor_home,
    ensure_repo_dir,
    read_json_dict,
    read_json_dict_strict,
    repo_dir_for_identity,
    repo_identity,
    schema_ref_from,
    user_config_path,
    user_schema_path,
    write_json_dict,
)
from auditor.user_settings import UserSettings

CONFIG_VERSION: int = UserSettings.model_fields["config_version"].default


def _read_settings(path: Path) -> dict[str, object]:
    """The settings file's own keys, or a clean failure when the file cannot be parsed.

    ``init`` rewrites these files, so a torn one has to stop the command: the lossy reader the
    rest of the codebase uses would report ``{}`` and the rewrite would delete the user's keys.
    """
    current = read_json_dict_strict(path)
    if current is None:
        fail(f"{path} is not valid JSON; fix or delete it before re-running")
    return current


def _write_markers(path: Path, schema_ref: str) -> bool:
    """Set ``$schema`` and ``config_version`` on a settings file, keeping the user's own keys.
    Returns whether anything changed."""
    current = _read_settings(path)
    updated = {**current, "$schema": schema_ref, "config_version": CONFIG_VERSION}
    if path.exists() and updated == current:
        return False
    write_json_dict(path, updated)
    return True


def _moved_from(root: Path, directory: Path) -> str | None:
    """The root a breadcrumb was written for, when that directory is gone. A sibling worktree
    keeps its own root alive, so it shares the settings rather than counting as a move."""
    recorded = read_json_dict(directory / "root.json").get("root")
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
    force: InitForce = False,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Create or refresh the user config home ($AUDITOR_HOME)."""
    if migrate and not repo:
        fail("--migrate requires --repo")
    for flag, name in ((migrate, "--migrate"), (clean_status, "--clean-status")):
        if flag and check:
            fail(f"{name} writes; it cannot be combined with --check")
    root = cli_root(target)
    NOTICE.owned()  # render_init lists the unknown keys and --json carries them
    home = auditor_home()
    identity = repo_identity(root)
    directory = repo_dir_for_identity(identity)
    moves = NOTICE.user_keys(directory=directory).moves()
    if moves and not (check or force):
        # Stamping the new version on a file that still holds the old keys would claim a shape
        # the file does not have, and the values behind those keys are already not being read.
        fail(
            f"{ConfigNotice.MOVED}: {', '.join(moves)}; move them, "
            "then re-run with --force"
        )
    written: list[str] = []
    moved = _moved_from(root, directory)
    migrated = False
    legacy = root / ".auditor" / ".status.json"
    had_legacy = legacy.exists()
    # Both modes stop on a torn settings file: --check would otherwise report it as empty.
    _read_settings(user_config_path())
    _read_settings(directory / "config.json")

    if not check:
        try:
            home.mkdir(parents=True, exist_ok=True)
            schema = json.dumps(UserSettings.model_json_schema(), indent=2) + "\n"
            if (
                not user_schema_path().exists()
                or user_schema_path().read_text() != schema
            ):
                user_schema_path().write_text(schema)
                written.append(str(user_schema_path()))
            if _write_markers(user_config_path(), schema_ref_from(home)):
                written.append(str(user_config_path()))
            if repo:
                ensure_repo_dir(root, identity=identity)
                overlay = directory / "config.json"
                if _write_markers(overlay, schema_ref_from(directory)):
                    written.append(str(overlay))
                if migrate and moved is not None:
                    crumb = directory / "root.json"
                    crumb.write_text(
                        json.dumps(
                            {**read_json_dict(crumb), "root": str(root.resolve())}
                        )
                    )
                    written.append(str(crumb))
                    migrated = True
            if clean_status and had_legacy:
                legacy.unlink()
        except OSError as exc:
            fail(f"cannot write the auditor home at {home}: {exc}")

    present(
        InitReport(
            home=str(home),
            config=str(user_config_path()),
            schema_path=str(user_schema_path()),
            repo_dir=str(directory) if repo else None,
            written=tuple(written),
            checked=check,
            # Both families, from the notice this command opted out of: reporting only the user
            # half made init the one command that would not mention a [tool.auditor] typo.
            unknown_keys=tuple(NOTICE.keys(directory=directory)),
            moved_from=moved,
            migrated=migrated,
            legacy_status=str(legacy) if had_legacy else None,
        ),
        render_init,
        as_json=json_,
    )
