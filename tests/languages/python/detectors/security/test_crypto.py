"""Detectors in security/crypto.py: each rule flags its anti-pattern and ignores the clean version."""

import pytest
from _detector_cases import GROUPS
from _support import rule_ids, run_audit

_CASES = GROUPS["security/crypto"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_audit(bad)), (
        f"{rule_id} did not flag its anti-pattern"
    )
    assert rule_id not in rule_ids(run_audit(good)), (
        f"{rule_id} false-positived on clean code"
    )


_RANDOM = "PY-SEC-INSECURE-RANDOM"


@pytest.mark.parametrize(
    "src",
    [
        "idx = random.choice(items)",  # data sampling
        "sorted(rows, key=lambda r: (r.score, random.random()))",  # sort tiebreaker
        "jitter = random.uniform(0, 1)",  # backoff jitter
        "def pick_sample(items):\n    return random.sample(items, 3)",
    ],
)
def test_insecure_random_ignores_non_security_use(src):
    # `random` for sampling/shuffling/jitter is legitimate — not a crypto finding.
    assert _RANDOM not in rule_ids(run_audit(src))


@pytest.mark.parametrize(
    "src",
    [
        "token = random.randint(0, 1 << 32)",  # security-named assignment target
        "self.session_id = random.getrandbits(64)",  # attribute target
        "def make_otp():\n    return random.choice('0123456789')",  # security-named function
        "password = ''.join(random.choice(chars) for _ in range(12))",  # comprehension value
    ],
)
def test_insecure_random_flags_security_context(src):
    assert _RANDOM in rule_ids(run_audit(src))
