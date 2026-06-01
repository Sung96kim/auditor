"""typescript/auditor.py: end-to-end audit of TSX fixtures + config/role handling."""

from _support import TS_DATA, run_ts_audit

from auditor.config import AuditorSettings, ResolvedConfig
from auditor.languages.typescript.auditor import TypeScriptAuditor
from auditor.models import FileRole, Severity


def _audit_fixture(name: str):
    source = (TS_DATA / name).read_text()
    rc = ResolvedConfig(AuditorSettings(), role=FileRole.PRODUCTION, rel_path=name)
    return TypeScriptAuditor().audit(
        file_path=name, source=source, role=FileRole.PRODUCTION, config=rc
    )


def test_dirty_fixture_flags_the_expected_rules():
    rules = {f.rule_id for f in _audit_fixture("Dirty.tsx").findings}
    assert {
        "TS-STYLE-DUPLICATE-IMPORT",
        "TS-REACT-MULTI-COMPONENT-FILE",
        "TS-A11Y-NONINTERACTIVE-ONCLICK",
        "TS-A11Y-ICON-BUTTON-NO-LABEL",
        "TS-A11Y-IMG-NO-ALT",
        "TS-A11Y-POSITIVE-TABINDEX",
    } <= rules


def test_clean_fixture_has_no_findings():
    result = _audit_fixture("Clean.tsx")
    assert result.findings == []
    assert result.language == "typescript"


def test_manifest_is_populated():
    assert any(e.symbol == "Panel" for e in _audit_fixture("Dirty.tsx").manifest)


def test_disabled_rule_is_skipped():
    settings = AuditorSettings.model_validate(
        {"rules": {"TS-A11Y-NONINTERACTIVE-ONCLICK": {"enabled": False}}}
    )
    res = run_ts_audit("<div onClick={go}>x</div>;\n", settings=settings)
    assert "TS-A11Y-NONINTERACTIVE-ONCLICK" not in {f.rule_id for f in res.findings}
    assert "TS-A11Y-NONINTERACTIVE-ONCLICK" in {s.rule_id for s in res.skipped_rules}


def test_severity_override_applies():
    settings = AuditorSettings.model_validate(
        {"rules": {"TS-A11Y-NONINTERACTIVE-ONCLICK": {"severity": "high"}}}
    )
    res = run_ts_audit("<div onClick={go}>x</div>;\n", settings=settings)
    pill = [f for f in res.findings if f.rule_id == "TS-A11Y-NONINTERACTIVE-ONCLICK"]
    assert pill and pill[0].severity is Severity.HIGH
