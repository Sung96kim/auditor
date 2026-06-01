"""Shared fixtures (propagate to the whole mirrored tests/ tree). Module-level helpers
live in ``_support`` so they can be imported from any subdirectory test."""

import shutil
from pathlib import Path

import pytest
from _support import SAMPLE_REPO, run_audit


@pytest.fixture
def audit():
    return run_audit


@pytest.fixture
def sample_repo(tmp_path) -> Path:
    """A writable copy of the realistic sample repo fixture (so the index/.auditor can be
    created without touching the checked-in fixture)."""
    dest = tmp_path / "repo"
    shutil.copytree(SAMPLE_REPO, dest)
    return dest
