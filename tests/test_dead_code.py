import ast
import asyncio

from auditor import dead_code
from auditor.engine import audit_target
from auditor.fixture_usage import find_unused
from auditor.languages.python.shapes import _symbol_defs


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


# ---------------------------------------------------------------------------
# PY-DEAD-SYMBOL — complex edge-case tests
# ---------------------------------------------------------------------------


def test_dead_metaclass_class_not_flagged():
    # Characterize the layer contract: find_dead would flag a class row if given one,
    # but shapes._def_may_register ensures no row is emitted for metaclass-using classes.
    # Verify shapes.py upstream filter using ast directly.
    src = "class Meta(type): pass\n\nclass _X(metaclass=Meta): pass\n"
    tree = ast.parse(src)
    defs = _symbol_defs(tree)
    # _X has keywords (metaclass=) → _def_may_register → excluded from defs
    names = [name for name, _kind, _line in defs]
    assert "_X" not in names, (
        "_X with metaclass= must not be emitted as a dead-symbol candidate"
    )


def test_scan_metaclass_class_not_flagged(tmp_path):
    # End-to-end: shapes._def_may_register exempts classes with keywords (e.g. metaclass=)
    _write(tmp_path, "pyproject.toml", '[project]\nname="x"\nversion="0"\n')
    _write(
        tmp_path,
        "pkg/a.py",
        (
            "class Meta(type): pass\n\n\n"
            "class _X(metaclass=Meta): pass\n\n\n"
            "def use(): return 1\n"
        ),
    )
    # _X has keywords (metaclass=Meta) → _def_may_register → not emitted → not flagged
    assert "PY-DEAD-SYMBOL" not in _scan(tmp_path)


def test_scan_decorated_private_function_not_flagged(tmp_path):
    # A private function with any decorator is conservatively exempt; the decorator
    # may register or expose the function (e.g. @register, @app.route).
    _write(tmp_path, "pyproject.toml", '[project]\nname="x"\nversion="0"\n')
    _write(
        tmp_path,
        "pkg/a.py",
        (
            "def some_decorator(fn): return fn\n\n\n"
            "@some_decorator\n"
            "def _private_fn():\n"
            "    return 42\n\n\n"
            "def use(): return 1\n"
        ),
    )
    assert "PY-DEAD-SYMBOL" not in _scan(tmp_path)


def test_scan_private_enum_subclass_not_flagged(tmp_path):
    # A private class subclassing Enum has a base → _def_may_register → exempt.
    # Conservative: Enum members are referenced via the class, not by name.
    _write(tmp_path, "pyproject.toml", '[project]\nname="x"\nversion="0"\n')
    _write(
        tmp_path,
        "pkg/a.py",
        (
            "from enum import Enum\n\n\n"
            "class _Status(Enum):\n"
            "    ACTIVE = 'active'\n"
            "    INACTIVE = 'inactive'\n\n\n"
            "def use(): return 1\n"
        ),
    )
    assert "PY-DEAD-SYMBOL" not in _scan(tmp_path)


def test_scan_plain_private_class_flagged(tmp_path):
    # A plain private class with no decorator, no base, no keywords IS genuinely dead.
    _write(tmp_path, "pyproject.toml", '[project]\nname="x"\nversion="0"\n')
    _write(
        tmp_path,
        "pkg/a.py",
        ("class _Helper:\n    pass\n\n\ndef use(): return 1\n"),
    )
    assert "PY-DEAD-SYMBOL" in _scan(tmp_path)


def test_scan_allexported_name_not_dead(tmp_path):
    # A public name listed in __all__ appears as a ref (string constant in __all__),
    # so it's treated as used and not flagged dead.
    _write(tmp_path, "pyproject.toml", '[project]\nname="x"\nversion="0"\n')
    _write(
        tmp_path,
        "pkg/a.py",
        "__all__ = ['PUBLIC']\n\nPUBLIC = 1\n",
    )
    _write(
        tmp_path, "pkg/b.py", "from pkg.a import PUBLIC\n\n\ndef use(): return PUBLIC\n"
    )
    assert "PY-DEAD-SYMBOL" not in _scan(tmp_path)


def test_call_valued_binding_not_emitted_literal_is():
    # Characterize the shapes layer: X = compute() is NOT emitted as a def row because
    # _value_may_register returns True for call-valued rhs (side-effect exemption).
    # X = (1, 2, 3) IS emitted (literal rhs has no side effects).
    src = "def compute(): return object()\n\nREGISTERED = compute()\nDEAD_TUPLE = (1, 2, 3)\n"
    tree = ast.parse(src)
    defs = _symbol_defs(tree)
    names = [name for name, _kind, _line in defs]
    assert "REGISTERED" not in names, (
        "call-valued binding must not be emitted as dead candidate"
    )
    assert "DEAD_TUPLE" in names, (
        "literal-tuple binding must be emitted as dead candidate"
    )


def test_scan_call_valued_binding_not_flagged_end_to_end(tmp_path):
    # End-to-end: X = some_func() must NOT be flagged (shapes.py never emits a def row).
    # X = (1, 2, 3) as a module-level unused constant IS flagged.
    _write(tmp_path, "pyproject.toml", '[project]\nname="x"\nversion="0"\n')
    _write(
        tmp_path,
        "pkg/a.py",
        (
            "def compute(): return object()\n\n\n"
            "REGISTERED = compute()\n"
            "DEAD_TUPLE = (1, 2, 3)\n\n\n"
            "def use(): return 1\n"
        ),
    )
    messages = _dead_symbol_messages(tmp_path)
    # call-valued binding is never emitted → never flagged
    assert not any("REGISTERED" in m for m in messages)
    # literal tuple binding IS emitted and unreferenced → flagged
    assert any("DEAD_TUPLE" in m for m in messages)
