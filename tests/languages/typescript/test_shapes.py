"""typescript/shapes.py: normalized component + function shape extraction."""

from auditor.languages.typescript.nodes import Tsx
from auditor.languages.typescript.parser import root_of
from auditor.languages.typescript.shapes import ShapeExtractor

_CARD = """
export function Card() {
  return (
    <div>
      <header><h2>t</h2></header>
      <section><p>b</p></section>
    </div>
  );
}
"""


def _shapes(src: str):
    return ShapeExtractor(Tsx(root_of(src, path="X.tsx"))).shapes()


def test_same_structure_different_names_collide():
    a = _shapes(_CARD)[0]
    b = _shapes(_CARD.replace("Card", "Panel").replace("t</h2>", "x</h2>"))[0]
    assert a.kind == "component" and a.shape_hash == b.shape_hash


def test_trivial_component_is_skipped():
    # under the minimum tag count — too generic to dedup
    assert _shapes("export const Tiny = () => <div>hi</div>;\n") == []


_FN = (
    "export function f(values: number[]) {\n"
    "  const total = values.reduce((a, b) => a + b, 0);\n"
    "  if (total > 0) {\n    return Math.round(total);\n  }\n"
    "  return Math.floor(total);\n}\n"
)


def test_function_shape_kind_and_namespacing():
    rows = _shapes(_FN)
    assert rows and rows[0].kind == "ts-function"
    assert rows[0].shape_hash != ""


def test_distinct_operations_do_not_collide():
    # same control-flow silhouette, different calls/param types -> different shape
    split = "function a(name: string) {\n  const p = name.split('-');\n  if (p.length > 2) {\n    return p.join('-');\n  }\n  return name;\n}\n"
    date = "function b(value: string) {\n  const ts = new Date(value).getTime();\n  if (Number.isFinite(ts)) {\n    return Math.floor(ts);\n  }\n  return 0;\n}\n"
    a = _shapes(split)
    b = _shapes(date)
    assert a and b and a[0].shape_hash != b[0].shape_hash
