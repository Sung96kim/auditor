"""Where the auditor keeps the data it generates and the settings the user owns.

Everything lives under one global home dir (``~/.auditor`` by default, override with
``$AUDITOR_HOME``): the shared SQLite index partitioned by repo, the user's own ``config.json``,
and one directory per repo keyed by :func:`repo_dir_key`. Repo-*authored* input
(``[tool.auditor]``, ``.auditor/config.toml``, ``.auditor/plugins/``, ``.auditor/baseline.json``)
stays in the repo and is read from there; nothing is written back into it.
"""

import functools
import hashlib
import json
import os
import re
import time
import zlib
from pathlib import Path, PurePosixPath

from auditor.config import GlobalPaths
from auditor.discovery import git_output
from auditor.models import Partition


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
    """The JSON object at ``path``, an empty dict when nothing is there, or ``None`` when
    something is there that cannot be read as a JSON object. Callers that rewrite a file take
    ``None`` as a refusal: replacing an unparseable file would throw away whatever the user typed.

    A missing parent counts as absent, not unreadable: there is no content to preserve.
    """
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, NotADirectoryError):
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


def schema_ref_from(directory: Path) -> str:
    """The ``$schema`` value a settings file in ``directory`` should carry: a relative path to the
    generated schema, forward-slashed so the written JSON is identical on every platform. Lives
    here because the ``repos/<key>`` depth it has to walk out of is this module's layout."""
    ref = os.path.relpath(user_schema_path(), directory).replace(os.sep, "/")
    return ref if ref.startswith(".") else f"./{ref}"


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


def partition_for(root: Path) -> Partition:
    """The identity and toplevel-relative prefix that bind an index handle to one checkout.

    Cached per process on the resolved root, because every CLI invocation opens the index and the
    two git calls would otherwise land on the fast commands too.
    """
    return _partition_for(root.resolve())


@functools.cache
def _partition_for(root: Path) -> Partition:
    """:func:`partition_for` on an already-resolved root. Outside git, and for a root that is not
    under its own toplevel, the prefix is empty and the identity falls back to the partition key."""
    identity = repo_identity(root)
    toplevel = git_output(root, "rev-parse", "--show-toplevel")
    if toplevel is None:
        return Partition(identity=identity)
    rel = os.path.relpath(root.resolve(), Path(toplevel).resolve()).replace(os.sep, "/")
    if rel == "." or rel.startswith(".."):
        return Partition(identity=identity)
    return Partition(identity=identity, prefix=f"{PurePosixPath(rel)}/")


def identity_key(identity: str) -> str:
    """Filesystem-safe key for one repo identity: the sha1 both ``repos/<key>`` and the rebuild
    lock file are named after."""
    return hashlib.sha1(identity.encode(), usedforsecurity=False).hexdigest()


#: the shape of every ``repos/<key>`` name, and of the ``key`` a hook posts to ``POST /events``
REPO_KEY_PATTERN = r"^[0-9a-f]{40}$"
_REPO_KEY = re.compile(REPO_KEY_PATTERN)


def is_repo_dir_key(key: str) -> bool:
    """Whether ``key`` is a :func:`repo_dir_key`, which is the only name ``repos/`` ever holds."""
    return _REPO_KEY.fullmatch(key) is not None


def repo_dir_key(root: Path) -> str:
    """Directory name for this repo's user state: sha1 of :func:`repo_identity`. Keyed on the
    identity rather than the path so a symlink or a second worktree lands in the same place."""
    return identity_key(repo_identity(root))


def repo_dir_from_key(key: str) -> Path:
    """Where one already-hashed ``repo_dir_key`` keeps its per-user state.

    The one owner of the ``repos/<key>`` layout; every other spelling of it goes through here,
    so a key that is not one raises rather than resolving to a directory outside the home.
    """
    if not is_repo_dir_key(key):
        raise ValueError(f"{key!r} is not a repo dir key")
    return auditor_home() / "repos" / key


def repo_dir_for_identity(identity: str) -> Path:
    """Where an already-resolved identity's per-user state lives, for callers holding an identity
    that cost a git subprocess."""
    return repo_dir_from_key(identity_key(identity))


def repo_root_from_key(key: str) -> Path | None:
    """The checkout one ``repos/<key>`` directory belongs to, from its ``root.json`` breadcrumb.

    The restart path knows a spool key and has read no event yet, so the breadcrumb is the only
    way back to a root. None when the directory or the crumb is absent or unreadable.
    """
    recorded = read_json_dict(repo_dir_from_key(key) / "root.json").get("root")
    return Path(recorded) if isinstance(recorded, str) and recorded else None


def repo_dir(root: Path) -> Path:
    """Where this repo's per-user state lives. Pure; :func:`ensure_repo_dir` creates it."""
    return repo_dir_for_identity(repo_identity(root))


def ensure_repo_dir(root: Path, *, identity: str | None = None) -> Path:
    """Create the repo's user-state dir and its ``root.json`` breadcrumb, which ``auditr init
    --check`` reads to spot a checkout that has moved. Pass ``identity`` when the caller already
    resolved it, to save a git subprocess. Raises OSError on an unwritable home."""
    identity = repo_identity(root) if identity is None else identity
    out = repo_dir_for_identity(identity)
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


_PORT_BASE = 7490
_PORT_SPAN = 500
#: the values that read as "off"; ``auditr_observer._OFF`` is the same set and a test pins the pair
OFF_VALUES = frozenset({"0", "f", "false", "n", "no", "off"})


def observer_dir() -> Path:
    """The daemon's directory under the home. Never created wholesale and never cleared.

    ``observer/locks/`` belongs to the rebuild lock and predates any daemon, so the daemon creates
    only the three leaves it owns.
    """
    return auditor_home() / "observer"


def observer_lock_path() -> Path:
    """The daemon singleton: whoever holds this flock is the daemon for this home."""
    return observer_dir() / "lock"


def daemon_json_path() -> Path:
    """Where a running daemon publishes its pid, port, home, version and wire compat."""
    return observer_dir() / "daemon.json"


def observer_log_dir() -> Path:
    """Where the daemon's rotating log lives."""
    return observer_dir() / "log"


def spool_path(key: str) -> Path:
    """One repo's pending-events spool, keyed by :func:`repo_dir_key` (spec 8.1)."""
    return repo_dir_from_key(key) / "spool.jsonl"


def observer_port() -> int:
    """The loopback port this home's daemon binds: ``AUDITOR_OBSERVER_PORT``, else the home's hash.

    ``0`` is a legal bind asking the kernel for any free port. An unreadable or out-of-range value
    falls back to the hash rather than raising: a typo here must not take every ``auditr`` down.
    """
    raw = GlobalPaths().observer_port.strip()
    try:
        configured = int(raw)
    # the unset case lands here too, and its empty string names no port
    except ValueError:
        configured = -1
    if 0 <= configured < 65536:  # out of range is unreadable: `bind` would raise on it
        return configured
    return _PORT_BASE + zlib.crc32(str(auditor_home().resolve()).encode()) % _PORT_SPAN


def observer_enabled() -> bool:
    """Whether ``AUDITOR_OBSERVER`` leaves the observer on at all (spec 8.1, 14).

    Anything that is not one of :data:`OFF_VALUES` leaves it on, which is the client's own rule.
    """
    return GlobalPaths().observer.strip().lower() not in OFF_VALUES


def is_main_worktree(root: Path) -> bool:
    """Whether ``root`` is the checkout's main worktree, not one ``git worktree add`` made.

    ``repo_identity`` deliberately gives every worktree of one checkout the same value, so the
    question needs the git dir against the common dir. Outside git there is one tree, so True.
    """
    own = git_output(root, "rev-parse", "--path-format=absolute", "--git-dir")
    common = git_output(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if (
        own is None or common is None
    ):  # git < 2.31 has no --path-format, as repo_identity records
        own = git_output(root, "rev-parse", "--git-dir")
        common = git_output(root, "rev-parse", "--git-common-dir")
    if own is None or common is None:
        return True
    return (root / own).resolve() == (root / common).resolve()
