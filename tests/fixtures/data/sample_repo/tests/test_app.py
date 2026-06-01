"""A test module (role=test). It deliberately contains things that are noise-by-design in
tests — a hardcoded token, an assert-based auth check, an eval, a shell call — which the
relaxed test policy disables/downgrades. A shared DB-fixture SQL-build (kept) still matters."""

import subprocess

import pytest

API_TOKEN = "sk-test-1234567890abcdef"  # PY-SEC-HARDCODED-SECRET (relaxed in tests)


@pytest.fixture
def admin_user():
    class U:
        is_admin = True

    return U()


def test_requires_admin(admin_user):
    assert admin_user.is_admin  # PY-SEC-ASSERT-FOR-SECURITY (relaxed in tests)


def test_eval_helper():
    assert eval("1 + 1") == 2  # PY-SEC-DANGEROUS-EVAL -> downgraded to candidate in tests


def test_runs_command():
    subprocess.run("echo hi", shell=True, check=False)  # shell=True downgraded in tests


def seed_rows(cursor, table: str) -> None:
    # Even in tests, building SQL from a variable in a shared helper is a real bug.
    cursor.execute(f"DELETE FROM {table}")  # PY-SEC-SQL-STRING-BUILD (kept)
