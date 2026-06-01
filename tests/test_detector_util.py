"""languages/python/detectors/_util.py: shared AST helpers."""

import ast

from auditor.languages.python.detectors._util import (
    call_attr,
    dotted_name,
    is_const_false,
    is_const_true,
    kwarg,
    nearest_enclosing_function,
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
