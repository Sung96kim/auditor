"""shapes.py: normalized model/function shape hashing for cross-file dedup."""

from auditor.languages.python.shapes import ShapeExtractor

_MODEL_A = "from pydantic import BaseModel\nclass A(BaseModel):\n    x: int\n    y: str\n"
_MODEL_B = "from pydantic import BaseModel\nclass B(BaseModel):\n    x: int\n    y: str\n"
_MODEL_DIFF = "from pydantic import BaseModel\nclass C(BaseModel):\n    p: float\n    q: bool\n"
_FUNC_A = "def a(x: int, y: int):\n    z = x + y\n    return z\n"
_FUNC_B = "def b(x: int, y: int):\n    z = x + y\n    return z\n"


def _hashes(source: str) -> set[str]:
    ex = ShapeExtractor.for_source(source)
    return {row.shape_hash for row in (ex.shapes() if ex else [])}


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


def test_syntax_error_returns_empty():
    assert ShapeExtractor.for_source("def broken(:\n") is None
