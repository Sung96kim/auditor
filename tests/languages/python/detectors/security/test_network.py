"""Detectors in security/network.py: each rule flags its anti-pattern and ignores the clean version."""

import pytest
from _detector_cases import GROUPS
from _support import rule_ids, run_audit

_CASES = GROUPS["security/network"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


_SSRF = "PY-SEC-SSRF"


@pytest.mark.parametrize(
    "src",
    [
        # module-constant URL referenced by name — not caller-controlled (the recaptcha case)
        "API = 'https://api.example.com'\ndef f():\n    return requests.post(API, data, timeout=5)",
        # constant literal
        "def f():\n    return requests.get('https://fixed/', timeout=5)",
        # url built from globals only
        "BASE = 'https://x'\ndef f():\n    return requests.get(BASE + '/health', timeout=5)",
    ],
)
def test_ssrf_ignores_non_user_derived_urls(src):
    assert _SSRF not in rule_ids(run_audit(src))


@pytest.mark.parametrize(
    "src",
    [
        "def f(url):\n    return requests.get(url, timeout=5)",  # bare param
        "def f(base):\n    return requests.get(base + '/x', timeout=5)",  # param-built
        "def f(req):\n    return requests.get(req['url'], timeout=5)",  # subscript of input
        "def f(host):\n    return requests.get(f'https://{host}/api', timeout=5)",  # f-string param
    ],
)
def test_ssrf_flags_caller_derived_urls(src):
    assert _SSRF in rule_ids(run_audit(src))
