import asyncio

from auditor import dead_code
from auditor.engine import audit_target
from auditor.fixture_usage import find_unused


def _def(name, kind, path, line=1):
    return {
        "symbol": f"{kind}\x1f{name}",
        "kind": "py-symbol-def",
        "path": path,
        "line": line,
    }


def _ref(name, path):
    return {"symbol": name, "kind": "py-symbol-ref", "path": path}


def _rows(defs, refs):
    return {"py-symbol-def": defs, "py-symbol-ref": refs}


PROD = {
    "a.py": "production",
    "tests/test_a.py": "test",
    "pkg/__init__.py": "production",
}


def test_unreferenced_symbol_is_flagged():
    out = dead_code.find_dead(
        _rows([_def("_dead", "func", "a.py", 5)], []),
        PROD,
        magic_names=frozenset(),
        entry_points=frozenset(),
    )
    assert [f.rule_id for f in out["a.py"]] == ["PY-DEAD-SYMBOL"]
    assert "_dead" in out["a.py"][0].message


def test_referenced_symbol_is_clean():
    out = dead_code.find_dead(
        _rows([_def("_used", "func", "a.py")], [_ref("_used", "b.py")]),
        PROD,
        magic_names=frozenset(),
        entry_points=frozenset(),
    )
    assert out == {}


def test_init_defs_exempt():
    out = dead_code.find_dead(
        _rows([_def("_x", "func", "pkg/__init__.py")], []),
        PROD,
        magic_names=frozenset(),
        entry_points=frozenset(),
    )
    assert out == {}


def test_test_role_defs_not_emitted_but_refs_pool():
    # a def in a test file is not emitted; and a prod symbol used only by a test is clean
    out = dead_code.find_dead(
        _rows(
            [_def("_t", "func", "tests/test_a.py"), _def("_p", "func", "a.py")],
            [_ref("_p", "tests/test_a.py")],
        ),
        PROD,
        magic_names=frozenset(),
        entry_points=frozenset(),
    )
    assert out == {}


def test_magic_and_entry_point_exempt():
    out = dead_code.find_dead(
        _rows(
            [_def("down_revision", "const", "a.py"), _def("cli", "func", "a.py")], []
        ),
        PROD,
        magic_names=frozenset({"down_revision"}),
        entry_points=frozenset({"cli"}),
    )
    assert out == {}


def test_per_language_pooling_keys_on_ref_kind():
    # a ref under a different kind must NOT mark a py-symbol-def used
    rows = {
        "py-symbol-def": [_def("_x", "func", "a.py")],
        "py-symbol-ref": [],
        "other-ref": [{"symbol": "_x", "kind": "other-ref", "path": "a.py"}],
    }
    out = dead_code.find_dead(
        rows, PROD, magic_names=frozenset(), entry_points=frozenset()
    )
    assert "a.py" in out  # still flagged; the foreign-kind ref is ignored


def _scan(tmp_path) -> set[str]:
    results = asyncio.run(audit_target(tmp_path, no_index=True))
    return {f.rule_id for r in results for f in r.findings}


def _write(tmp_path, rel, src):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src)


def test_scan_flags_dead_private_function(tmp_path):
    _write(tmp_path, "pyproject.toml", '[project]\nname="x"\nversion="0"\n')
    _write(
        tmp_path,
        "pkg/a.py",
        "def _dead():\n    return 1\n\n\ndef use():\n    return 2\n",
    )
    assert "PY-DEAD-SYMBOL" in _scan(tmp_path)


def test_scan_clean_when_used_cross_file(tmp_path):
    _write(tmp_path, "pyproject.toml", '[project]\nname="x"\nversion="0"\n')
    _write(tmp_path, "pkg/a.py", "def _used():\n    return 1\n")
    _write(
        tmp_path,
        "pkg/b.py",
        "from pkg.a import _used\n\n\ndef caller():\n    return _used()\n",
    )
    assert "PY-DEAD-SYMBOL" not in _scan(tmp_path)


def test_scan_entry_point_target_exempt(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname="x"\nversion="0"\n[project.scripts]\nx = "pkg.a:_main"\n',
    )
    _write(tmp_path, "pkg/a.py", "def _main():\n    return 1\n")
    assert "PY-DEAD-SYMBOL" not in _scan(tmp_path)


def _dead_symbol_messages(path) -> list[str]:
    results = asyncio.run(audit_target(path, no_index=True))
    return [
        f.message for r in results for f in r.findings if f.rule_id == "PY-DEAD-SYMBOL"
    ]


def test_call_valued_module_binding_not_flagged_dead(dead_symbol_registry):
    # regression (orion services/workflows/component_tags.py): self-registering tag constants
    # are never referenced by name but must NOT be flagged dead — their __init__ has a side
    # effect. A plain unused literal in the same module IS still a real dead constant.
    dead = _dead_symbol_messages(dead_symbol_registry)
    assert any("LEGACY_MAX_TAGS" in m for m in dead)  # literal const still flagged
    # the call-valued tag constants are exempt (construction may register a side effect)
    for name in ("ACCELERATOR", "EXTRACTION", "CLASSIFICATION"):
        assert not any(name in m for m in dead), f"{name} wrongly flagged dead"


def test_registering_subclass_and_decorated_def_not_flagged_dead(dead_symbol_registry):
    # regression: defining a subclass (via __init_subclass__) or applying a registering decorator
    # wires the symbol into machinery we can't see — it isn't provably dead even when unreferenced.
    # A private class with no base and no decorator is still a real dead symbol.
    dead = _dead_symbol_messages(dead_symbol_registry)
    assert any("_UnusedHelper" in m for m in dead)  # plain private class still flagged
    for name in (
        "_AlphaPlugin",
        "_BetaPlugin",
    ):  # subclass-/decorator-registered → exempt
        assert not any(name in m for m in dead), f"{name} wrongly flagged dead"


# --- fixture_usage.find_unused: test_support role -----------------------------------------


def test_find_unused_flags_test_support_fixture():
    # A fixture-def in a test_support-role file with no refs should be flagged
    def_rows = [
        {
            "symbol": "shared_db",
            "kind": "pytest-fixture-def",
            "path": "tests/conftest.py",
            "line": 5,
        }
    ]
    ref_rows: list[dict] = []
    roles = {"tests/conftest.py": "test_support"}
    result = find_unused(def_rows, ref_rows, roles)
    assert "tests/conftest.py" in result
    assert result["tests/conftest.py"][0].rule_id == "PY-TEST-UNUSED-FIXTURE"


def test_find_unused_test_support_ref_marks_used():
    # A ref from a test_support-role file should count as "used" and suppress the finding
    def_rows = [
        {
            "symbol": "shared_db",
            "kind": "pytest-fixture-def",
            "path": "tests/conftest.py",
            "line": 5,
        }
    ]
    ref_rows = [
        {
            "symbol": "shared_db",
            "kind": "pytest-fixture-ref",
            "path": "tests/helpers.py",
        }
    ]
    roles = {"tests/conftest.py": "test_support", "tests/helpers.py": "test_support"}
    result = find_unused(def_rows, ref_rows, roles)
    assert result == {}
