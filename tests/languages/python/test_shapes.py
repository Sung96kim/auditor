"""shapes.py: normalized model/function shape hashing for cross-file dedup."""

from auditor.languages.python.shapes import ShapeExtractor

_MODEL_A = (
    "from pydantic import BaseModel\nclass A(BaseModel):\n    x: int\n    y: str\n"
)
_MODEL_B = (
    "from pydantic import BaseModel\nclass B(BaseModel):\n    x: int\n    y: str\n"
)
_MODEL_DIFF = (
    "from pydantic import BaseModel\nclass C(BaseModel):\n    p: float\n    q: bool\n"
)
_FUNC_A = "def a(x: int, y: int):\n    z = x + y\n    return z\n"
_FUNC_B = "def b(x: int, y: int):\n    z = x + y\n    return z\n"


_DEDUP_KINDS = {"model", "function", "pytest-fixture-def", "pytest-fixture-ref"}


def _hashes(source: str) -> set[str]:
    ex = ShapeExtractor.for_source(source)
    return {
        row.shape_hash
        for row in (ex.shapes() if ex else [])
        if row.kind
        in _DEDUP_KINDS  # dedup shapes only; class-base/symbol rows are separate
    }


def test_same_model_shape_collides():
    assert _hashes(_MODEL_A) == _hashes(_MODEL_B)


def test_different_model_shape_differs():
    assert _hashes(_MODEL_A).isdisjoint(_hashes(_MODEL_DIFF))


def test_same_function_shape_collides():
    assert _hashes(_FUNC_A) == _hashes(_FUNC_B)


def test_trivial_defs_have_no_shape():
    # single-field model and single-statement function are too trivial to dedup
    assert _hashes("class S(BaseModel):\n    x: int\n") == set()
    assert _hashes("def f():\n    return 1\n") == set()


def test_docstring_does_not_count_toward_clone_min_statements():
    # regression: a leading docstring must not inflate a one-statement function past the
    # min-statements clone threshold (orion `get_app` and accessor one-liners were noise)
    assert _hashes('def f():\n    """doc"""\n    return 1\n') == set()
    # a docstring plus two real statements is still shaped (docstring stripped, 2 remain)
    assert _hashes('def f():\n    """doc"""\n    x = 1\n    return x\n') != set()


def test_syntax_error_returns_empty():
    assert ShapeExtractor.for_source("def broken(:\n") is None


# Typer/Click passthrough commands (`uv`/`pnpm`/`docker` forwarding FORWARD_ARGS) share a shape by
# framework design; a CLI-command module's top-level functions must not be indexed as xfile dups.
_CLI_PASSTHROUGH = (
    "import typer\napp = typer.Typer()\n"
    "@app.command()\ndef uv(args):\n    env = get_env()\n    return env.run('uv', args)\n"
)


def test_cli_command_module_top_level_fns_not_shaped():
    assert _hashes(_CLI_PASSTHROUGH) == set()
    # but a non-CLI module with the same body IS shaped (default behavior preserved)
    non_cli = "def uv(args):\n    env = get_env()\n    return env.run('uv', args)\n"
    assert _hashes(non_cli) != set()


def test_cli_frameworks_is_configurable():
    src = (
        "import mycli\n"
        "def uv(args):\n    env = get_env()\n    return env.run('uv', args)\n"
    )
    ex = ShapeExtractor.for_source(src)
    # not a known CLI framework by default -> shaped
    assert [r for r in ex.shapes() if r.kind == "function"]
    # configured as one -> top-level command not shaped
    assert not [r for r in ex.shapes(cli_frameworks=("mycli",)) if r.kind == "function"]


def _shapes(src: str):
    return ShapeExtractor.for_source(src).shapes()


def _defs(src: str) -> set[str]:
    return {r.symbol for r in _shapes(src) if r.kind == "py-symbol-def"}


def _refs(src: str) -> set[str]:
    return {r.symbol for r in _shapes(src) if r.kind == "py-symbol-ref"}


def test_symbol_def_eligibility():
    src = (
        "import os\n"
        "_PRIVATE = 1\n"
        "MAX = 2\n"
        "def _helper():\n    return 1\n"
        "class _Impl:\n    pass\n"
        "def public():\n    return 1\n"  # public func -> not a def
        "class Public:\n    pass\n"  # public class -> not a def
        "__version__ = '1'\n"  # dunder -> excluded
    )
    assert _defs(src) == {
        "const\x1f_PRIVATE",
        "const\x1fMAX",
        "func\x1f_helper",
        "class\x1f_Impl",
    }


def test_symbol_refs_include_names_attrs_imports_strings():
    src = (
        "from mod import thing\n"
        "import pkg.sub\n"
        "x = thing()\n"
        "y = obj.attr\n"
        "z = 'dynamic_name'\n"
        "w = 'main:app'\n"
    )
    r = _refs(src)
    assert {"thing", "attr", "dynamic_name", "main", "app", "pkg"} <= r


def test_symbol_def_excludes_conditional_and_init_is_handled_in_pass():
    # defs nested in if/try are NOT top-level -> not emitted
    src = "if True:\n    _COND = 1\ntry:\n    _T = 2\nexcept Exception:\n    pass\n"
    assert _defs(src) == set()


def test_definition_site_is_not_a_self_reference():
    # a constant defined and never loaded -> appears as a def but NOT in refs
    src = "_DEAD = 1\n"
    assert "const\x1f_DEAD" in _defs(src)
    assert "_DEAD" not in _refs(src)


# --- pytest-fixture-ref shapes ------------------------------------------------------------


def _fixture_ref_symbols(src: str) -> set[str]:
    ex = ShapeExtractor.for_source(src)
    assert ex is not None
    return {r.symbol for r in ex.shapes() if r.kind == "pytest-fixture-ref"}


def test_usefixtures_refs():
    src = (
        "import pytest\n"
        "@pytest.mark.usefixtures('widget')\n"
        "def test_a():\n"
        "    assert True\n"
    )
    assert "widget" in _fixture_ref_symbols(src)


def test_getfixturevalue_ref():
    src = "def test_a(request):\n    x = request.getfixturevalue('db')\n"
    assert "db" in _fixture_ref_symbols(src)


def test_indirect_parametrize_refs():
    src = (
        "import pytest\n"
        "@pytest.mark.parametrize('myfix', [1, 2], indirect=True)\n"
        "def test_a(myfix):\n"
        "    assert myfix\n"
    )
    assert "myfix" in _fixture_ref_symbols(src)
