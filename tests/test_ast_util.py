"""ast_util.py: low-level AST helpers used by manifest construction + detectors."""

import ast

from auditor import ast_util


def _first(src: str, node_type) -> ast.AST:
    return next(n for n in ast.walk(ast.parse(src)) if isinstance(n, node_type))


def test_dotted():
    assert ast_util.dotted(ast.parse("os.environ.get", mode="eval").body) == "os.environ.get"
    assert ast_util.dotted(ast.parse("requests.get(x)", mode="eval").body) == "requests.get"
    assert ast_util.dotted(ast.parse("plain", mode="eval").body) == "plain"
    # unparse fallback for non-name/attribute nodes (e.g. a subscript return type)
    assert ast_util.dotted(ast.parse("dict[str, Any]", mode="eval").body) == "dict[str, Any]"


def test_decorator_names():
    cls = _first("@app.get('/x')\n@cached\nclass C: ...", ast.ClassDef)
    assert ast_util.decorator_names(cls) == ("app.get", "cached")


def test_class_field_count():
    cls = _first("class M:\n    a: int\n    b: str\n    c = 1\n", ast.ClassDef)
    assert ast_util.class_field_count(cls) == 2  # only annotated fields


def test_function_flags():
    fn = _first("async def f(x):\n    return {}", ast.AsyncFunctionDef)
    flags = ast_util.function_flags(fn, is_method=False)
    assert "ASYNC" in flags and "UNTYPED_RETURN" in flags and "UNTYPED_ARGS" in flags

    typed = _first("def g(x: int) -> int:\n    return x", ast.FunctionDef)
    assert ast_util.function_flags(typed, is_method=False) == ()

    dictret = _first("def h() -> dict[str, Any]:\n    return {}", ast.FunctionDef)
    assert "UNTYPED_DICT_RETURN" in ast_util.function_flags(dictret, is_method=False)


def test_function_flags_method_self_exempt():
    cls = _first("class C:\n    def m(self) -> int:\n        return 1", ast.ClassDef)
    method = cls.body[0]
    # `self` is the first positional and must not count as untyped
    assert "UNTYPED_ARGS" not in ast_util.function_flags(method, is_method=True)


def test_class_flags():
    basemodel = _first("class M(BaseModel):\n    x: int", ast.ClassDef)
    assert "BASEMODEL" in ast_util.class_flags(basemodel)
    dc = _first("@dataclass\nclass R:\n    x: int", ast.ClassDef)
    assert "DATACLASS" in ast_util.class_flags(dc)
    allstatic = _first("class U:\n    @staticmethod\n    def a(): ...\n    @staticmethod\n    def b(): ...", ast.ClassDef)
    assert "ALL_STATICMETHODS" in ast_util.class_flags(allstatic)
    mixed = _first("class S:\n    @staticmethod\n    def a(): ...\n    def b(self): ...", ast.ClassDef)
    assert "ALL_STATICMETHODS" not in ast_util.class_flags(mixed)
