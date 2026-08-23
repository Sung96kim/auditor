"""Where the auditor keeps the data it generates and the settings the user owns.

Everything lives under one global home dir (``~/.auditor`` by default, override with
``$AUDITOR_HOME``): the shared SQLite index partitioned by repo, the user's own ``config.json``,
and one directory per repo keyed by :func:`repo_dir_key`. Repo-*authored* input
(``[tool.auditor]``, ``.auditor/config.toml``, ``.auditor/plugins/``, ``.auditor/baseline.json``)
stays in the repo and is read from there; nothing is written back into it.
"""

import hashlib
import json
import os
import time
from pathlib import Path

from auditor.config import GlobalPaths
from auditor.discovery import git_output


def read_json_dict(path: Path) -> dict[str, object]:
    """The JSON object at ``path``, or an empty dict when it is absent, unreadable, torn, or not
    an object. Every generated file under the home is a cache or a settings layer, so a bad edit
    degrades to defaults instead of failing a scan."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_json_dict_strict(path: Path) -> dict[str, object] | None:
    """The JSON object at ``path``, an empty dict when it is absent, or ``None`` when it exists
    but is unreadable or is not a JSON object. Callers that rewrite a file take ``None`` as a
    refusal: replacing an unparseable file would throw away whatever the user typed."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_json_dict(path: Path, data: dict[str, object]) -> None:
    """Replace ``path`` with ``data`` as indented JSON in one step, via a temp file and
    ``os.replace``, so an interrupted write cannot truncate a settings file the user owns."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def auditor_home() -> Path:
    """The global auditor data dir: ``$AUDITOR_HOME`` if set, else ``~/.auditor``. Instantiated
    per call so a changed environment (e.g. tests) is always reflected."""
    return GlobalPaths().home.expanduser()


def index_db_path() -> Path:
    """The single shared index database covering every repo this user has scanned."""
    return auditor_home() / "index.db"


def user_config_path() -> Path:
    """The user's global settings file, written by ``auditr init``."""
    return auditor_home() / "config.json"


def user_schema_path() -> Path:
    """The generated JSON Schema the settings files point at with ``$schema``."""
    return auditor_home() / "config.schema.json"


def models_dir() -> Path:
    """Cache dir for the optional vector layer's downloaded models."""
    return auditor_home() / "models"


def repo_key(root: Path) -> str:
    """Stable identity of a repo root within the shared index — its resolved absolute path.
    Every row a scan writes is tagged with this so two repos never collide in the one db."""
    return str(root.resolve())


def repo_identity(root: Path) -> str:
    """Identity shared by every worktree of one checkout: the resolved git common dir, or the
    resolved root outside git. A symlinked path or a subdirectory resolves to the same value.

    Both git branches resolve, so `/tmp` and `/private/tmp` on macOS cannot mint two directories
    for one checkout. ``git_output`` returns None when git is missing or the command fails, which
    is the only case that falls through.
    """
    absolute = git_output(
        root, "rev-parse", "--path-format=absolute", "--git-common-dir"
    )
    if absolute is not None:
        return str(Path(absolute).resolve())
    relative = git_output(root, "rev-parse", "--git-common-dir")  # git < 2.31
    if relative is not None:
        return str((root / relative).resolve())
    return repo_key(root)


def _key_for(identity: str) -> str:
    return hashlib.sha1(identity.encode(), usedforsecurity=False).hexdigest()


def repo_dir_key(root: Path) -> str:
    """Directory name for this repo's user state: sha1 of :func:`repo_identity`. Keyed on the
    identity rather than the path so a symlink or a second worktree lands in the same place."""
    return _key_for(repo_identity(root))


def repo_dir(root: Path) -> Path:
    """Where this repo's per-user state lives. Pure; :func:`ensure_repo_dir` creates it."""
    return auditor_home() / "repos" / repo_dir_key(root)


def ensure_repo_dir(root: Path) -> Path:
    """Create the repo's user-state dir and its ``root.json`` breadcrumb, which ``auditr init
    --check`` reads to spot a checkout that has moved. Raises OSError on an unwritable home."""
    identity = repo_identity(root)
    out = auditor_home() / "repos" / _key_for(identity)
    out.mkdir(parents=True, exist_ok=True)
    crumb = out / "root.json"
    if not crumb.exists():
        crumb.write_text(
            json.dumps(
                {
                    "root": str(root.resolve()),
                    "identity": identity,
                    "created_at": int(time.time()),
                }
            )
        )
    return out
