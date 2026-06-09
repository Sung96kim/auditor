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


# ---------------------------------------------------------------------------
# BindAllInterfaces — positional arg and annotated-assignment branches
# ---------------------------------------------------------------------------


def test_bind_all_interfaces_positional_arg_fires():
    # 0.0.0.0 as a positional arg to a bind/run/serve function
    src = "server.run('0.0.0.0', 8080)\n"
    assert "PY-SEC-BIND-ALL-INTERFACES" in rule_ids(run_audit(src))


def test_bind_all_interfaces_annotated_assign_fires():
    # annotated assignment whose target name matches the host regex
    src = "host_addr: str = '0.0.0.0'\n"
    assert "PY-SEC-BIND-ALL-INTERFACES" in rule_ids(run_audit(src))


def test_bind_all_interfaces_annotated_assign_safe_does_not_fire():
    # annotated assignment with a loopback address → safe
    src = "host_addr: str = '127.0.0.1'\n"
    assert "PY-SEC-BIND-ALL-INTERFACES" not in rule_ids(run_audit(src))


# ---------------------------------------------------------------------------
# InsecureTls — check_hostname=False branch
# ---------------------------------------------------------------------------


def test_insecure_tls_check_hostname_false_fires():
    src = "import ssl\nssl.wrap_socket(sock, check_hostname=False)\n"
    assert "PY-SEC-INSECURE-TLS" in rule_ids(run_audit(src))


def test_insecure_tls_no_check_hostname_does_not_fire():
    # wrap_socket without check_hostname kwarg → no finding from this branch
    src = "import ssl\nssl.wrap_socket(sock)\n"
    assert "PY-SEC-INSECURE-TLS" not in rule_ids(run_audit(src))
