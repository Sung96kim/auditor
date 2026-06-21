import ast

from auditor.graph.semantic_profile import ATTRS, compute


def _fn(src):
    return ast.parse(src).body[0]


def test_compute_reader():
    assert "returns_value" in compute(_fn("def reader(x):\n    return db.get(x)\n"))
    assert "no_params" not in compute(_fn("def reader(x):\n    return db.get(x)\n"))


def test_compute_mutator_async_loop():
    prof = set(
        compute(
            _fn(
                "async def mutator(self, v):\n    self.value = v\n    for i in range(3):\n        await sink(i)\n"
            )
        )
    )
    assert {"is_async", "awaits", "has_loop", "writes_self"} <= prof
    assert "returns_value" not in prof  # returns nothing -> returns_none instead
    assert "returns_none" in prof


def test_compute_checker_bool_branch():
    prof = set(
        compute(
            _fn("def checker(x):\n    if x:\n        return True\n    return False\n")
        )
    )
    assert {"returns_bool", "has_branch", "multi_return", "returns_value"} <= prof


def test_every_computed_attr_is_declared_in_ATTRS():
    # guards against drift: compute() must never emit a name absent from ATTRS
    samples = [
        "def a(x):\n    return x\n",
        "async def b(self):\n    self.x = 1\n    for _ in []:\n        await f()\n",
        "def c():\n    try:\n        raise X()\n    except Y:\n        return [i for i in z]\n",
        "def d():\n    return d()\n",
    ]
    for s in samples:
        assert set(compute(_fn(s))) <= set(ATTRS)
