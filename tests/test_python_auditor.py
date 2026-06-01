"""languages/python/auditor.py: severity/verdict override application, syntax-error
handling, and the rule_ids filter."""

from conftest import run_audit

from auditor.config import AuditorSettings
from auditor.models import Severity


def test_severity_override_applied():
    settings = AuditorSettings.model_validate({"rules": {"PY-SEC-SSRF": {"severity": "blocking"}}})
    res = run_audit("requests.get(url, timeout=5)", settings=settings)
    ssrf = [f for f in res.findings if f.rule_id == "PY-SEC-SSRF"]
    assert ssrf and ssrf[0].severity is Severity.BLOCKING


def test_disabled_rule_is_skipped_not_run():
    settings = AuditorSettings.model_validate({"rules": {"PY-SEC-DANGEROUS-EVAL": {"enabled": False}}})
    res = run_audit("eval(x)", settings=settings)
    assert "PY-SEC-DANGEROUS-EVAL" not in {f.rule_id for f in res.findings}
    assert "PY-SEC-DANGEROUS-EVAL" in {s.rule_id for s in res.skipped_rules}


def test_syntax_error_returns_skip_marker():
    res = run_audit("def broken(:\n    pass\n")
    assert res.findings == []
    assert any(s.rule_id == "*" for s in res.skipped_rules)


def test_manifest_is_built():
    res = run_audit("class A:\n    pass\n\ndef f():\n    return 1\n")
    symbols = {e.symbol for e in res.manifest}
    assert {"A", "f"} <= symbols
