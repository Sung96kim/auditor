"""Structural pytest test-quality rules (category=testing, framework=pytest)."""

import asyncio

import pytest
from _support import rule_ids, run_audit

from auditor.engine import audit_target
from auditor.models import FileRole


def _ids(source: str, role: FileRole = FileRole.TEST) -> set[str]:
    return rule_ids(run_audit(source, role=role, rel_path="test_x.py"))


# --- C: NO-ASSERTION ---------------------------------------------------------------------


def test_no_assertion_fires_on_assertionless_test():
    src = "def test_thing():\n    do_work(1)\n"
    assert "PY-TEST-NO-ASSERTION" in _ids(src)


@pytest.mark.parametrize(
    "body",
    [
        "    assert do_work(1) == 2\n",
        "    with pytest.raises(ValueError):\n        do_work(1)\n",
        "    mock.assert_called_once()\n",
        "    self.assertEqual(do_work(1), 2)\n",
    ],
)
def test_no_assertion_clean_when_asserting(body):
    src = "def test_thing():\n" + body
    assert "PY-TEST-NO-ASSERTION" not in _ids(src)


def test_no_assertion_suppressed_when_delegating_to_local_helper():
    src = (
        "def _verify(x):\n    assert x\n\ndef test_thing():\n    _verify(do_work(1))\n"
    )
    assert "PY-TEST-NO-ASSERTION" not in _ids(src)


def test_no_assertion_gated_to_test_role():
    src = "def test_thing():\n    do_work(1)\n"
    assert "PY-TEST-NO-ASSERTION" not in _ids(src, role=FileRole.PRODUCTION)


# --- B: LOGIC-IN-TEST --------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "    if cond:\n        assert a\n",
        "    for x in xs:\n        assert x\n",
        "    while go:\n        assert step()\n",
        "    try:\n        assert a\n    except Exception:\n        pass\n",
    ],
)
def test_logic_in_test_fires(body):
    assert "PY-TEST-LOGIC-IN-TEST" in _ids("def test_thing():\n" + body)


def test_logic_in_test_clean_for_straight_line():
    src = "def test_thing():\n    r = do_work(1)\n    assert r == 2\n"
    assert "PY-TEST-LOGIC-IN-TEST" not in _ids(src)


def test_logic_in_test_clean_for_with_block():
    src = "def test_thing():\n    with pytest.raises(ValueError):\n        do_work(1)\n"
    assert "PY-TEST-LOGIC-IN-TEST" not in _ids(src)


# --- H: SLEEP ----------------------------------------------------------------------------


def test_sleep_fires_on_time_sleep():
    src = "import time\n\n\ndef test_thing():\n    time.sleep(1)\n    assert True\n"
    assert "PY-TEST-SLEEP" in _ids(src)


def test_sleep_fires_on_from_import():
    src = (
        "from time import sleep\n\n\ndef test_thing():\n    sleep(1)\n    assert True\n"
    )
    assert "PY-TEST-SLEEP" in _ids(src)


def test_sleep_clean_for_asyncio_sleep():
    src = (
        "import asyncio\n\n\nasync def test_thing():\n"
        "    await asyncio.sleep(1)\n    assert True\n"
    )
    assert "PY-TEST-SLEEP" not in _ids(src)


# --- G: SKIP-NO-REASON -------------------------------------------------------------------


@pytest.mark.parametrize(
    "deco",
    [
        "@pytest.mark.skip\n",
        "@pytest.mark.skip()\n",
        "@pytest.mark.xfail\n",
        "@pytest.mark.skipif(SOME_CONDITION)\n",
    ],
)
def test_skip_no_reason_fires(deco):
    src = deco + "def test_thing():\n    assert True\n"
    assert "PY-TEST-SKIP-NO-REASON" in _ids(src)


@pytest.mark.parametrize(
    "deco",
    [
        '@pytest.mark.skip(reason="flaky")\n',
        '@pytest.mark.skip("flaky")\n',
        '@pytest.mark.xfail(reason="known bug")\n',
        '@pytest.mark.skipif(SOME_CONDITION, reason="py<3.12")\n',
    ],
)
def test_skip_with_reason_clean(deco):
    src = deco + "def test_thing():\n    assert True\n"
    assert "PY-TEST-SKIP-NO-REASON" not in _ids(src)


# --- D: OVER-MOCKING ---------------------------------------------------------------------


def test_over_mocking_fires_above_default():
    body = "".join(f"    m{i} = MagicMock()\n" for i in range(5))  # 5 > default 4
    src = (
        "from unittest.mock import MagicMock\n\n\ndef test_thing():\n"
        + body
        + "    assert True\n"
    )
    assert "PY-TEST-OVER-MOCKING" in _ids(src)


def test_over_mocking_counts_patch_object():
    body = "".join(f"    patch.object(o, 'a{i}')\n" for i in range(5))
    src = (
        "from unittest.mock import patch\n\n\ndef test_thing():\n"
        + body
        + "    assert True\n"
    )
    assert "PY-TEST-OVER-MOCKING" in _ids(src)


def test_over_mocking_clean_at_threshold():
    body = "".join(f"    m{i} = Mock()\n" for i in range(4))  # 4 == default, not over
    src = (
        "from unittest.mock import Mock\n\n\ndef test_thing():\n"
        + body
        + "    assert True\n"
    )
    assert "PY-TEST-OVER-MOCKING" not in _ids(src)


# --- A: PARAMETRIZE-CANDIDATE ------------------------------------------------------------


def _clone(name: str, val: int) -> str:
    return f"def {name}():\n    r = do_work({val})\n    assert r == {val}\n\n\n"


def test_parametrize_candidate_fires_on_three_clones():
    src = _clone("test_a", 1) + _clone("test_b", 2) + _clone("test_c", 3)
    assert "PY-TEST-PARAMETRIZE-CANDIDATE" in _ids(src)


def test_parametrize_candidate_clean_below_min_clones():
    src = _clone("test_a", 1) + _clone("test_b", 2)  # only 2, default min is 3
    assert "PY-TEST-PARAMETRIZE-CANDIDATE" not in _ids(src)


def test_parametrize_candidate_clean_for_distinct_tests():
    src = (
        "def test_a():\n    assert do_work(1) == 1\n\n\n"
        "def test_b():\n    assert other(2) == 2\n\n\n"
        "def test_c():\n    raise RuntimeError\n\n\n"
    )
    assert "PY-TEST-PARAMETRIZE-CANDIDATE" not in _ids(src)


def test_parametrize_candidate_skips_already_parametrized():
    deco = '@pytest.mark.parametrize("v", [1, 2, 3])\n'
    src = (
        (deco + _clone("test_a", 1))
        + (deco + _clone("test_b", 2))
        + (deco + _clone("test_c", 3))
    )
    assert "PY-TEST-PARAMETRIZE-CANDIDATE" not in _ids(src)


# --- E: DUPLICATE-SETUP ------------------------------------------------------------------


def _shared_setup(name: str, tail: str) -> str:
    # identical 2-statement arrange prefix, divergent assertion tail
    return (
        f"def {name}():\n"
        "    client = make_client()\n"
        "    client.login(user)\n"
        f"    {tail}\n\n\n"
    )


def test_duplicate_setup_fires_on_shared_prefix():
    src = (
        _shared_setup("test_a", "assert client.get('/a') == 1")
        + _shared_setup("test_b", "assert client.get('/b') == 2")
        + _shared_setup("test_c", "assert client.post('/c') == 3")
    )
    assert "PY-TEST-DUPLICATE-SETUP" in _ids(src)


def test_duplicate_setup_clean_when_bodies_are_full_clones():
    # identical full bodies -> that's PARAMETRIZE's job, not a fixture
    body = "    client = make_client()\n    client.login(user)\n    assert client.get('/a') == 1\n"
    src = "".join(f"def test_{i}():\n{body}\n\n" for i in range(3))
    assert "PY-TEST-DUPLICATE-SETUP" not in _ids(src)


def test_duplicate_setup_excludes_parametrize_cluster():
    # 3 full clones (PARAMETRIZE's) + 1 divergent sharing the prefix: A fires, E must NOT
    # re-flag the clone cluster (spec invariant: E never double-fires with A).
    src = (
        _shared_setup("test_a", "assert client.get('/x') == 1")
        + _shared_setup("test_b", "assert client.get('/x') == 1")
        + _shared_setup("test_c", "assert client.get('/x') == 1")
        + _shared_setup("test_d", "assert client.post('/y') == 2")
    )
    found = _ids(src)
    assert "PY-TEST-PARAMETRIZE-CANDIDATE" in found  # the 3 clones
    assert "PY-TEST-DUPLICATE-SETUP" not in found  # only test_d remains -> below min


# --- F: UNUSED-FIXTURE (cross-file) ------------------------------------------------------


def _scan(tmp_path) -> set[str]:
    results = asyncio.run(audit_target(tmp_path, no_index=True))
    return {f.rule_id for r in results for f in r.findings}


def _write(tmp_path, rel, src):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src)


def test_unused_fixture_fires(tmp_path):
    _write(tmp_path, "pyproject.toml", '[project]\nname="x"\nversion="0"\n')
    _write(
        tmp_path,
        "tests/conftest.py",
        "import pytest\n\n\n@pytest.fixture\ndef widget():\n    return 1\n",
    )
    _write(tmp_path, "tests/test_x.py", "def test_a():\n    assert True\n")
    assert "PY-TEST-UNUSED-FIXTURE" in _scan(tmp_path)


def test_used_fixture_clean(tmp_path):
    _write(tmp_path, "pyproject.toml", '[project]\nname="x"\nversion="0"\n')
    _write(
        tmp_path,
        "tests/conftest.py",
        "import pytest\n\n\n@pytest.fixture\ndef widget():\n    return 1\n",
    )
    _write(tmp_path, "tests/test_x.py", "def test_a(widget):\n    assert widget == 1\n")
    assert "PY-TEST-UNUSED-FIXTURE" not in _scan(tmp_path)


def test_autouse_fixture_never_flagged(tmp_path):
    _write(tmp_path, "pyproject.toml", '[project]\nname="x"\nversion="0"\n')
    _write(
        tmp_path,
        "tests/conftest.py",
        "import pytest\n\n\n@pytest.fixture(autouse=True)\ndef setup():\n    return 1\n",
    )
    _write(tmp_path, "tests/test_x.py", "def test_a():\n    assert True\n")
    assert "PY-TEST-UNUSED-FIXTURE" not in _scan(tmp_path)


# --- B: LOGIC-IN-TEST nested-lambda suppression -------------------------------------------


def test_logic_in_test_lambda_not_flagged():
    # Control flow inside a lambda does NOT count as logic in the test body
    src = "def test_thing():\n    f = lambda x: x if x else 0\n    assert f(1)\n"
    assert "PY-TEST-LOGIC-IN-TEST" not in _ids(src)


# --- E: DUPLICATE-SETUP below min_clones -------------------------------------------------


def test_duplicate_setup_below_min_clones_not_flagged():
    # Two tests with identical full bodies (< parametrize_min_clones=3) -> must NOT fire
    body = "    client = make_client()\n    client.login(user)\n    assert client.get('/a') == 1\n"
    src = f"def test_a():\n{body}\n\ndef test_b():\n{body}\n"
    assert "PY-TEST-DUPLICATE-SETUP" not in _ids(src)


# --- G: SKIP-NO-REASON on Test* class ----------------------------------------------------


def test_skip_no_reason_on_test_class():
    src = (
        "import pytest\n"
        "@pytest.mark.skip\n"
        "class TestFoo:\n"
        "    def test_a(self):\n"
        "        assert True\n"
    )
    assert "PY-TEST-SKIP-NO-REASON" in _ids(src)


# --- I: FIXTURE-MUTABLE-WIDE-SCOPE -------------------------------------------------------

_FIX = "import pytest\n"
_RULE = "PY-TEST-FIXTURE-MUTABLE-WIDE-SCOPE"


@pytest.mark.parametrize("scope", ["session", "module", "package"])
@pytest.mark.parametrize("lit", ["[]", "[1, 2]", "{}", "{'k': 1}", "{1, 2}"])
def test_fixture_mutable_wide_scope_fires(scope, lit):
    src = f"{_FIX}@pytest.fixture(scope='{scope}')\ndef data():\n    return {lit}\n"
    assert _RULE in _ids(src)


def test_fixture_mutable_wide_scope_yield_fires():
    src = f"{_FIX}@pytest.fixture(scope='session')\ndef data():\n    yield []\n"
    assert _RULE in _ids(src)


@pytest.mark.parametrize(
    "ret", ["set()", "list()", "dict()", "frozenset()", "()", "(1, 2)", "'s'", "42"]
)
def test_fixture_wide_scope_non_literal_clean(ret):
    # only mutable *literals* are shared-state footguns; calls/immutables are fine
    src = f"{_FIX}@pytest.fixture(scope='session')\ndef data():\n    return {ret}\n"
    assert _RULE not in _ids(src)


@pytest.mark.parametrize(
    "deco", ["@pytest.fixture", "@pytest.fixture(scope='function')"]
)
def test_fixture_function_scope_mutable_clean(deco):
    # function scope -> a fresh object per test -> not shared, not flagged
    src = f"{_FIX}{deco}\ndef data():\n    return []\n"
    assert _RULE not in _ids(src)


def test_fixture_factory_returning_function_clean():
    # a factory fixture returns an inner function; the [] is inside the nested def, not shared
    src = (
        f"{_FIX}@pytest.fixture(scope='session')\n"
        "def make():\n"
        "    def build():\n"
        "        return []\n"
        "    return build\n"
    )
    assert _RULE not in _ids(src)


def test_fixture_mutable_wide_scope_not_in_production_role():
    src = f"{_FIX}@pytest.fixture(scope='session')\ndef data():\n    return []\n"
    assert _RULE not in _ids(src, role=FileRole.PRODUCTION)
