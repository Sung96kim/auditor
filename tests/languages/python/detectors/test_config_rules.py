"""Detectors in config_rules.py: each rule flags its anti-pattern and ignores the clean version."""

import pytest
from _detector_cases import GROUPS
from _support import rule_ids, run_audit

_CASES = GROUPS["config_rules"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


# regression: a chained call on an I/O result (`requests.get(...).json()`) matched the
# `requests.` prefix twice — once as `requests.get`, once as `requests.get.json` — yielding two
# findings on one statement (and a baseline fingerprint collision). It must produce exactly one.
@pytest.mark.parametrize(
    "src",
    [
        '_M = requests.get("https://x.invalid/m.json", timeout=5).json()\n',
        "_C = httpx.get(URL).json()\n",
        "_D = urllib.request.urlopen(URL).read()\n",
    ],
    ids=["requests.get.json", "httpx.get.json", "urlopen.read"],
)
def test_import_time_io_counts_chained_call_once(src):
    findings = run_audit(src).findings
    hits = [f for f in findings if f.rule_id == "PY-CONFIG-IMPORT-TIME-IO"]
    assert len(hits) == 1, f"expected 1 import-time-IO finding, got {len(hits)}"
