"""Where the auditor keeps the data it generates.

Generated state — the incremental index/cache — lives in ONE shared SQLite database under a
global home dir (``~/.auditor`` by default, override with ``$AUDITOR_HOME``), partitioned by
repo inside the db rather than scattered as one file per repo. Repo-*authored* input
(``<repo>/.auditor/config.toml``, ``.auditor/plugins/``, ``.auditor/baseline.json``) stays in
the repo and is read from there — this module is only about generated data.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GlobalPaths(BaseSettings):
    """Global auditor data locations, read from the environment. ``home`` ← ``$AUDITOR_HOME``
    (via the ``AUDITOR_`` prefix), defaulting to ``~/.auditor``."""

    model_config = SettingsConfigDict(env_prefix="AUDITOR_")
    home: Path = Field(default_factory=lambda: Path.home() / ".auditor")


def auditor_home() -> Path:
    """The global auditor data dir: ``$AUDITOR_HOME`` if set, else ``~/.auditor``. Instantiated
    per call so a changed environment (e.g. tests) is always reflected."""
    return GlobalPaths().home.expanduser()


def index_db_path() -> Path:
    """The single shared index database covering every repo this user has scanned."""
    return auditor_home() / "index.db"


def repo_key(root: Path) -> str:
    """Stable identity of a repo root within the shared index — its resolved absolute path.
    Every row a scan writes is tagged with this so two repos never collide in the one db."""
    return str(root.resolve())
