"""languages/python/detectors/_util.py: shared AST helpers."""

import ast

import pytest

from auditor.languages.python.detectors._util import (
    call_attr,
    dotted_name,
    from_import_map,
    function_params,
    import_alias_map,
    is_const_false,
    is_const_true,
    kwarg,
    nearest_enclosing_function,
    resolve_dotted,
)
from auditor.languages.python.detectors._util import (
    test_functions as find_test_functions,
)


def _expr(src: str) -> ast.expr:
    return ast.parse(src, mode="eval").body


def test_dotted_name():
    assert dotted_name(_expr("os.environ.get")) == "os.environ.get"
    assert dotted_name(_expr("requests.get(x)")) == "requests.get"
    assert dotted_name(_expr("plain")) == "plain"


def test_call_attr_and_kwarg():
    call = _expr("obj.method(a, timeout=5, verify=False)")
    assert call_attr(call) == "method"
    assert isinstance(kwarg(call, "timeout"), ast.Constant)
    assert kwarg(call, "missing") is None


def test_const_true_false():
    assert is_const_true(_expr("True")) is True
    assert is_const_false(_expr("False")) is True
    assert is_const_true(_expr("1")) is False


def test_nearest_enclosing_function():
    tree = ast.parse("x = 1\ndef f():\n    y = 2\n    return y\n")
    enclosing = nearest_enclosing_function(tree)
    # the `y = 2` assignment is inside f; the module-level `x = 1` is not
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    inner = next(n for n in ast.walk(fn) if isinstance(n, ast.Assign))
    module_assign = tree.body[0]
    assert enclosing.get(id(inner)) is fn
    assert enclosing.get(id(module_assign)) is None


# ---------------------------------------------------------------------------
# import_alias_map
# ---------------------------------------------------------------------------


def test_import_alias_map_dotted():
    tree = ast.parse("import a.b as c\n")
    aliases = import_alias_map(tree)
    assert aliases == {"c": "a.b"}


def test_import_alias_map_simple():
    tree = ast.parse("import pickle as pkl\n")
    aliases = import_alias_map(tree)
    assert aliases == {"pkl": "pickle"}


def test_import_alias_map_no_alias():
    # a plain `import os` binds `os` but has no asname → not included
    tree = ast.parse("import os\n")
    aliases = import_alias_map(tree)
    assert aliases == {}


# ---------------------------------------------------------------------------
# resolve_dotted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, aliases, expected",
    [
        ("pkl", {"pkl": "pickle"}, "pickle"),  # bare head, no trailing dot
        ("pkl.loads", {"pkl": "pickle"}, "pickle.loads"),  # head + rest
        ("json.loads", {"pkl": "pickle"}, "json.loads"),  # head not aliased
        ("plain", {}, "plain"),  # empty aliases
    ],
)
def test_resolve_dotted(name: str, aliases: dict, expected: str) -> None:
    assert resolve_dotted(name, aliases) == expected


# ---------------------------------------------------------------------------
# from_import_map
# ---------------------------------------------------------------------------


def test_from_import_map_aliased():
    tree = ast.parse("from hashlib import sha256 as s\n")
    result = from_import_map(tree, "hashlib")
    assert result == {"s": "sha256"}


def test_from_import_map_non_aliased():
    tree = ast.parse("from hashlib import sha256\n")
    result = from_import_map(tree, "hashlib")
    assert result == {"sha256": "sha256"}


def test_from_import_map_wrong_module():
    # module filter: import from `os` is not returned when asking for `hashlib`
    tree = ast.parse("from os import path\n")
    result = from_import_map(tree, "hashlib")
    assert result == {}


# ---------------------------------------------------------------------------
# test_functions
# ---------------------------------------------------------------------------


def test_test_functions_class_branch():
    src = "class TestFoo:\n    def test_bar(self): pass\n    def helper(self): pass\n"
    tree = ast.parse(src)
    names = [fn.name for fn in find_test_functions(tree)]
    assert names == ["test_bar"]
    assert "helper" not in names


def test_test_functions_top_level():
    src = "def test_x(): pass\ndef not_a_test(): pass\n"
    tree = ast.parse(src)
    names = [fn.name for fn in find_test_functions(tree)]
    assert "test_x" in names
    assert "not_a_test" not in names


def test_test_functions_non_test_class_excluded():
    # methods of a class whose name doesn't start with Test are not included
    src = "class Helpers:\n    def test_bar(self): pass\n"
    tree = ast.parse(src)
    assert find_test_functions(tree) == []


# ---------------------------------------------------------------------------
# function_params
# ---------------------------------------------------------------------------


def test_function_params_vararg_kwarg():
    tree = ast.parse("def f(a, *args, **kwargs): pass\n")
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    params = function_params(fn)
    assert "a" in params
    assert "args" in params
    assert "kwargs" in params


def test_function_params_positional_only():
    tree = ast.parse("def f(a, b): pass\n")
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    params = function_params(fn)
    assert params == {"a", "b"}


def test_function_params_kwonly():
    tree = ast.parse("def f(*, kw): pass\n")
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    params = function_params(fn)
    assert "kw" in params
