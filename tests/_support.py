"""Shared test helpers + fixture paths, importable from any test in the mirrored tree
(``tests/`` is on the path via ``pythonpath``)."""

from pathlib import Path

from auditor.config import AuditorSettings, ResolvedConfig
from auditor.languages.bash.auditor import BashAuditor
from auditor.languages.python.auditor import PythonAuditor
from auditor.languages.typescript.auditor import TypeScriptAuditor
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
TS_DATA = DATA_DIR / "ts"


def run_ts_audit(
    source: str,
    *,
    role: FileRole = FileRole.PRODUCTION,
    settings: AuditorSettings | None = None,
    rel_path: str = "Component.tsx",
) -> ScanResult:
    """Audit a TS/TSX source string with every rule enabled unless overridden."""
    settings = settings if settings is not None else AuditorSettings()
    rc = ResolvedConfig(settings, role=role, rel_path=rel_path)
    return TypeScriptAuditor().audit(
        file_path=rel_path, source=source, role=role, config=rc
    )


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


def run_sh_audit(
    source: str,
    *,
    role: FileRole = FileRole.PRODUCTION,
    settings: AuditorSettings | None = None,
    rel_path: str = "install.sh",
) -> ScanResult:
    """Audit a shell source string with every rule enabled unless overridden."""
    settings = settings if settings is not None else AuditorSettings()
    rc = ResolvedConfig(settings, role=role, rel_path=rel_path)
    return BashAuditor().audit(file_path=rel_path, source=source, role=role, config=rc)


# (label, value) representative high-confidence secrets — each must trip `*-SECRET-DETECTED`.
# Values are well-known fakes / format-valid dummies, never live credentials.
SECRET_SAMPLES: list[tuple[str, str]] = [
    ("aws", "AKIAIOSFODNN7EXAMPLE"),
    ("github", "ghp_0123456789abcdefghijklmnopqrstuvwxyz"),
    ("stripe", "sk_live_" + "a" * 24),
    ("slack", "xoxb-" + "1" * 12 + "-" + "1" * 12 + "-" + "a" * 24),
    ("openai", "sk-" + "a" * 20 + "T3BlbkFJ" + "b" * 20),
    ("jwt", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF1234567890"),
    ("dburi", "postgres://user:s3cretpass@db.example.com:5432/app"),
    ("pem", "-----BEGIN RSA PRIVATE KEY-----"),
    # newer-wave providers
    ("groq", "gsk_" + "a" * 52),
    ("supabase", "sbp_" + "0" * 40),
    ("planetscale", "pscale_pw_" + "a" * 40),
    ("postman", "PMAK-" + "0" * 24 + "-" + "0" * 34),
    ("hubspot", "pat-na1-12345678-1234-1234-1234-123456789012"),
    ("azure", "AccountKey=" + "a" * 86 + "=="),
]
# benign lookalikes that must NOT trip the secret sweep
BENIGN_SECRET_LOOKALIKES: list[str] = [
    "https://api.example.com/v1/users",
    "a1b2c3d4-e5f6-7890-abcd-ef1234567890",  # UUID
    "d41d8cd98f00b204e9800998ecf8427e",  # md5 hash
    "this is just a normal sentence in a string",
]


def rule_ids(result: ScanResult) -> set[str]:
    return {f.rule_id for f in result.findings}


def result_with(file: str, *severities: Severity) -> ScanResult:
    """A ScanResult with one finding per given severity — for ordering tests."""
    findings = [
        Finding(
            rule_id="PY-TEST-RULE",
            category=Category.STYLE,
            severity=sev,
            verdict_kind=VerdictKind.AUTO,
            line=i + 1,
            message=f"{sev.value} finding",
        )
        for i, sev in enumerate(severities)
    ]
    return ScanResult(
        file=file, language="python", role=FileRole.PRODUCTION, findings=findings
    )


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
