"""Shared test fixtures."""

import shutil
from pathlib import Path

import pytest

from auditor.config import AuditorSettings, ResolvedConfig
from auditor.languages.python.auditor import PythonAuditor
from auditor.models import FileRole, ScanResult

DATA_DIR = Path(__file__).parent / "fixtures" / "data"
SAMPLE_REPO = DATA_DIR / "sample_repo"
PLUGIN_FILE = DATA_DIR / "plugins" / "house_rules.py"


def run_audit(
    source: str,
    *,
    role: FileRole = FileRole.PRODUCTION,
    settings: AuditorSettings | None = None,
    rel_path: str = "x.py",
    project_deps: frozenset[str] = frozenset({"pydantic"}),
    package_root: str | None = None,
) -> ScanResult:
    """Audit a source string with every rule enabled (default settings) unless overridden."""
    settings = settings if settings is not None else AuditorSettings()
    rc = ResolvedConfig(settings, role=role, rel_path=rel_path)
    return PythonAuditor().audit(
        file_path=rel_path,
        source=source,
        role=role,
        config=rc,
        project_deps=project_deps,
        package_root=package_root,
    )


def rule_ids(result: ScanResult) -> set[str]:
    return {f.rule_id for f in result.findings}


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
