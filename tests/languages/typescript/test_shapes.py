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


def test_all_div_components_are_too_generic_to_dedup():
    # two distinct components made entirely of <div> collide by luck — not a real duplicate
    # (found via tailor audit: Callout vs EmptyState, both all-<div>)
    a = "export function Callout() {\n  return <div><div><div /></div><div><div /></div></div>;\n}\n"
    b = "export function Empty() {\n  return <div><div><div /></div><div><div /></div></div>;\n}\n"
    assert _shapes(a) == [] and _shapes(b) == []


def test_varied_tag_components_still_dedup():
    a = "export function ListA() {\n  return <section><header><h2>t</h2></header><ul><li>x</li></ul></section>;\n}\n"
    b = "export function ListB() {\n  return <section><header><h2>y</h2></header><ul><li>z</li></ul></section>;\n}\n"
    ra, rb = _shapes(a)[0], _shapes(b)[0]
    assert ra.kind == "component" and ra.shape_hash == rb.shape_hash


def test_data_consts_are_not_function_shapes():
    # two lookup maps with the same keys are not duplicate "functions" (found via tailor audit)
    rows = _shapes(
        'const STATUS_LABEL = { ok: "Healthy", bad: "Down", warn: "Slow" };\n'
        'const STATUS_TONE = { ok: "green", bad: "red", warn: "amber" };\n'
    )
    assert [r for r in rows if r.kind == "ts-function"] == []


def test_paren_free_arrow_identical_bodies_collide():
    """Two structurally-identical paren-free arrow functions (no param parens) must produce
    the same ts-function shape hash."""
    fn_a = "const f = x => { if (x > 0) { return x.toString(); } return String(x); };\n"
    fn_b = "const g = x => { if (x > 0) { return x.toString(); } return String(x); };\n"
    shapes_a = _shapes(fn_a)
    shapes_b = _shapes(fn_b)
    assert shapes_a and shapes_b, "both functions should emit a ts-function shape"
    fa = next(s for s in shapes_a if s.kind == "ts-function")
    fb = next(s for s in shapes_b if s.kind == "ts-function")
    assert fa.shape_hash == fb.shape_hash


def test_thin_wrappers_differing_in_member_do_not_collide():
    # two mutation hooks identical but for which api fn they reference must NOT dedup
    a = _shapes(
        "export function useA() {\n  const qc = useQueryClient();\n  return useMutation({ mutationFn: api.createResume, onSuccess: () => qc.invalidateQueries() });\n}\n"
    )
    b = _shapes(
        "export function useB() {\n  const qc = useQueryClient();\n  return useMutation({ mutationFn: api.createSession, onSuccess: () => qc.invalidateQueries() });\n}\n"
    )
    fa = next(r for r in a if r.kind == "ts-function")
    fb = next(r for r in b if r.kind == "ts-function")
    assert fa.shape_hash != fb.shape_hash
