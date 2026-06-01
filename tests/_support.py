"""Shared test helpers + fixture paths, importable from any test in the mirrored tree
(``tests/`` is on the path via ``pythonpath``)."""

from pathlib import Path

from auditor.config import AuditorSettings, ResolvedConfig
from auditor.languages.python.auditor import PythonAuditor
from auditor.models import (
    Category,
    FileRole,
    Finding,
    ScanResult,
    Severity,
    VerdictKind,
)

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


def demo_result() -> ScanResult:
    """A small ScanResult with one auto + one candidate finding, for reporter tests."""
    findings = [
        Finding(
            rule_id="PY-SEC-DANGEROUS-EVAL",
            category=Category.SECURITY,
            severity=Severity.BLOCKING,
            verdict_kind=VerdictKind.AUTO,
            line=2,
            message="eval on input",
            standard_refs=("bandit:B307", "owasp:A03"),
        ),
        Finding(
            rule_id="PY-OOP-CONSTRUCTOR-WALL",
            category=Category.OOP_COMPOSITION,
            severity=Severity.LOW,
            verdict_kind=VerdictKind.CANDIDATE,
            line=10,
            message="wall",
        ),
    ]
    return ScanResult(
        file="pkg/a.py", language="python", role=FileRole.PRODUCTION, findings=findings
    )
