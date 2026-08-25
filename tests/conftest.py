"""Shared fixtures (propagate to the whole mirrored tests/ tree). Module-level helpers
live in ``_support`` so they can be imported from any subdirectory test."""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from _support import DEAD_SYMBOL_REGISTRY, SAMPLE_REPO, git
from loguru import logger
from typer import rich_utils


@pytest.fixture(autouse=True)
def _plain_typer_output(monkeypatch):
    """Keep typer's help and error panels plain text, so assertions on CLI output read the same
    in CI (GITHUB_ACTIONS or FORCE_COLOR make typer force a styled, box-wrapped terminal) as
    they do locally."""
    monkeypatch.setattr(rich_utils, "FORCE_TERMINAL", False)


@pytest.fixture(autouse=True)
def _isolated_auditor_home(tmp_path_factory, monkeypatch):
    """Point the global auditor home (the shared ~/.auditor index) at a throwaway dir for every
    test, so scans never touch — or depend on — the real user home."""
    home = tmp_path_factory.mktemp("auditor_home")
    monkeypatch.setenv("AUDITOR_HOME", str(home))
    return home


@pytest.fixture
def warning_log() -> Iterator[list[str]]:
    """Collect every WARNING the ``auditor`` logger emits during the test, message text only."""
    messages: list[str] = []
    sink_id = logger.add(messages.append, level="WARNING", format="{message}")
    logger.enable("auditor")
    try:
        yield messages
    finally:
        logger.disable("auditor")
        logger.remove(sink_id)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A one-commit git repo. The identity helpers need a real .git, and `worktree add` needs
    a HEAD to branch from."""
    repo = tmp_path / "main"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "auditor tests")
    (repo / "a.txt").write_text("x\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


@pytest.fixture
def sample_repo(tmp_path) -> Path:
    """A writable copy of the realistic sample repo fixture (so a scan can write/read config
    and scope without mutating the checked-in fixture; the index itself lives in the isolated
    global home from ``_isolated_auditor_home``)."""
    dest = tmp_path / "repo"
    shutil.copytree(SAMPLE_REPO, dest)
    return dest


@pytest.fixture
def dead_symbol_registry() -> Path:
    """Realistic registry fixture (orion BlueprintTag pattern). Scanned read-only — no copy
    needed since no_index scans don't mutate the tree and the index lives in the isolated home."""
    return DEAD_SYMBOL_REGISTRY
