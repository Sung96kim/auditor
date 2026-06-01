"""fingerprints.py: content hashing + per-rule fingerprints."""

from auditor.config import AuditorSettings, ResolvedConfig
from auditor.fingerprints import content_hash, rule_fingerprint
from auditor.models import FileRole


def _eff(rule: str, settings: AuditorSettings):
    rc = ResolvedConfig(settings, role=FileRole.PRODUCTION, rel_path="x.py")
    return rc.effective(rule)


def test_content_hash_deterministic():
    assert content_hash("abc") == content_hash("abc")
    assert content_hash("abc") != content_hash("abd")


def test_fingerprint_stable_for_same_config():
    eff = _eff("PY-STYLE-FILE-SIZE", AuditorSettings())
    assert rule_fingerprint("PY-STYLE-FILE-SIZE", eff) == rule_fingerprint("PY-STYLE-FILE-SIZE", eff)


def test_fingerprint_changes_with_threshold():
    base = _eff("PY-STYLE-FILE-SIZE", AuditorSettings())
    bumped = _eff(
        "PY-STYLE-FILE-SIZE",
        AuditorSettings.model_validate(
            {"rules": {"PY-STYLE-FILE-SIZE": {"threshold": {"file_max_lines": 10}}}}
        ),
    )
    assert rule_fingerprint("PY-STYLE-FILE-SIZE", base) != rule_fingerprint("PY-STYLE-FILE-SIZE", bumped)


def test_fingerprint_changes_with_severity():
    base = _eff("PY-SEC-SSRF", AuditorSettings())
    bumped = _eff(
        "PY-SEC-SSRF",
        AuditorSettings.model_validate({"rules": {"PY-SEC-SSRF": {"severity": "blocking"}}}),
    )
    assert rule_fingerprint("PY-SEC-SSRF", base) != rule_fingerprint("PY-SEC-SSRF", bumped)
