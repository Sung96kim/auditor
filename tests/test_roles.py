"""File-role classification + the test-policy (relaxed vs strict) behavior."""

from pathlib import Path

import pytest
from _support import rule_ids, run_audit

from auditor.config import AuditorSettings, load_config
from auditor.models import FileRole
from auditor.roles import RoleClassifier

_TEST_BODY = "import pytest\n\ndef test_thing():\n    assert True\n"
_PROD_BODY = "def add(a, b):\n    return a + b\n"


@pytest.mark.parametrize(
    "rel_path, source, expected",
    [
        ("tests/test_foo.py", _TEST_BODY, FileRole.TEST),
        ("pkg/test_foo.py", _TEST_BODY, FileRole.TEST),
        ("tests/conftest.py", "import pytest\n", FileRole.TEST_SUPPORT),
        ("tests/helpers.py", "def make_user():\n    return 1\n", FileRole.TEST_SUPPORT),
        ("pkg/factories.py", "def build():\n    return 1\n", FileRole.TEST_SUPPORT),
        ("pkg/service.py", _PROD_BODY, FileRole.PRODUCTION),
        ("gen/thing_pb2.py", "x = 1\n", FileRole.GENERATED),
        ("run.py", "if __name__ == '__main__':\n    main()\n", FileRole.SCRIPT),
        # unconventional: test code outside tests/ detected by content
        ("weird/checks.py", _TEST_BODY, FileRole.TEST),
    ],
)
def test_classify(rel_path, source, expected):
    assert RoleClassifier().classify(rel_path, source) == expected


def test_role_globs_override():
    # mark a harness package as test_support even though it's not under tests/
    classifier = RoleClassifier({FileRole.TEST_SUPPORT: ["harness/**"]})
    assert classifier.classify("harness/server.py", _PROD_BODY) == FileRole.TEST_SUPPORT


# A test fixture that would trip several rules in production.
_RISKY = (
    "def helper():\n"
    "    assert user.is_admin\n"
    "    token = 'sk-hardcoded-secret-value'\n"
    "    def f(a, b, c, d, e, g, h):\n"
    "        return 1\n"
    "    return token\n"
)


def _settings_for(tmp_path: Path, profile: str) -> AuditorSettings:
    (tmp_path / ".auditor").mkdir(exist_ok=True)
    (tmp_path / ".auditor" / "config.toml").write_text(f'extends = "{profile}"\n')
    return load_config(tmp_path)


def test_test_role_relaxes_noisy_rules(tmp_path):
    settings = _settings_for(tmp_path, "base")
    prod = rule_ids(run_audit(_RISKY, role=FileRole.PRODUCTION, settings=settings))
    test = rule_ids(run_audit(_RISKY, role=FileRole.TEST, settings=settings))
    # hardcoded-secret and assert-for-security are relaxed away in test code
    assert "PY-SEC-HARDCODED-SECRET" in prod
    assert "PY-SEC-HARDCODED-SECRET" not in test


def test_strict_mode_audits_tests_fully(tmp_path):
    settings = _settings_for(tmp_path, "all-strict")
    test = rule_ids(run_audit(_RISKY, role=FileRole.TEST, settings=settings))
    assert "PY-SEC-HARDCODED-SECRET" in test


def test_skipped_rules_surface_reason(tmp_path):
    settings = _settings_for(tmp_path, "base")
    res = run_audit(_RISKY, role=FileRole.TEST, settings=settings)
    skipped = {s.rule_id for s in res.skipped_rules}
    assert "PY-SEC-HARDCODED-SECRET" in skipped
