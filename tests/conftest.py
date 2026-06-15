"""Shared fixtures (propagate to the whole mirrored tests/ tree). Module-level helpers
live in ``_support`` so they can be imported from any subdirectory test."""

import shutil
from pathlib import Path

import pytest
from _support import DEAD_SYMBOL_REGISTRY, SAMPLE_REPO


@pytest.fixture(autouse=True)
def _isolated_auditor_home(tmp_path_factory, monkeypatch):
    """Point the global auditor home (the shared ~/.auditor index) at a throwaway dir for every
    test, so scans never touch — or depend on — the real user home."""
    home = tmp_path_factory.mktemp("auditor_home")
    monkeypatch.setenv("AUDITOR_HOME", str(home))
    return home


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
