"""typescript/nodes.py: the Tsx node wrapper — navigation + JSX accessors."""

from auditor.languages.typescript.nodes import Tsx
from auditor.languages.typescript.parser import root_of


def _tsx(source: str) -> Tsx:
    return Tsx(root_of(source, path="X.tsx"))


def _first(root: Tsx, type_: str) -> Tsx:
    return next(root.descendants(type_))


def test_jsx_name_and_attributes():
    el = _first(
        _tsx('const x = <Button size="icon" className="h-7" />;\n'),
        "jsx_self_closing_element",
    )
    assert el.jsx_name() == "Button"
    attrs = el.attributes()
    assert set(attrs) == {"size", "className"}
    assert attrs["size"].attr_value_text() == "icon"
    assert attrs["className"].attr_value_text() == "h-7"


def test_expression_attribute_value_strips_braces():
    el = _first(_tsx("const x = <div tabIndex={3} />;\n"), "jsx_self_closing_element")
    assert el.attributes()["tabIndex"].attr_value_text() == "3"


def test_has_text_child_distinguishes_text_from_icon():
    text_el = _first(_tsx("const x = <span>Active</span>;\n"), "jsx_element")
    icon_el = _first(_tsx("const x = <span><Icon /></span>;\n"), "jsx_element")
    assert text_el.has_text_child() is True
    assert icon_el.has_text_child() is False


def test_unwrap_export_and_contains_jsx():
    exported = (
        _tsx("export function F() {\n  return <div />;\n}\n")
        .named_children()[0]
        .unwrap_export()
    )
    assert exported.type == "function_declaration"
    assert _tsx("const f = () => <div />;\n").contains_jsx() is True
    assert _tsx("const f = () => 1 + 2;\n").contains_jsx() is False


def test_child_elements():
    el = _first(_tsx("const x = <div><a /><b /></div>;\n"), "jsx_element")
    assert {c.jsx_name() for c in el.child_elements()} == {"a", "b"}
