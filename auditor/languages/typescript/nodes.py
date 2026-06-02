"""``Tsx`` — a thin wrapper over a tree-sitter ``Node`` giving text/line/walk navigation and
the JSX element/attribute model, so detectors compose method calls instead of threading raw
nodes through free functions. Methods that return nodes return wrapped ``Tsx`` instances.
"""

from collections.abc import Iterator

from tree_sitter import Node

_JSX_ELEMENTS = ("jsx_element", "jsx_self_closing_element")


class Tsx:
    """A wrapped tree-sitter node. Generic navigation + JSX accessors on one cursor type."""

    __slots__ = ("node",)

    def __init__(self, node: Node) -> None:
        self.node = node

    @property
    def type(self) -> str:
        return self.node.type

    @property
    def text(self) -> str:
        return self.node.text.decode("utf-8", "replace")

    @property
    def line(self) -> int:
        """1-indexed start line, for ``Finding.line``."""
        return self.node.start_point.row + 1

    @property
    def is_jsx_element(self) -> bool:
        return self.type in _JSX_ELEMENTS

    def field(self, name: str) -> "Tsx | None":
        child = self.node.child_by_field_name(name)
        return Tsx(child) if child is not None else None

    def named_children(self) -> list["Tsx"]:
        return [Tsx(c) for c in self.node.named_children]

    def walk(self) -> Iterator["Tsx"]:
        """Pre-order over every named descendant (including self)."""
        stack = [self.node]
        while stack:
            cur = stack.pop()
            yield Tsx(cur)
            stack.extend(reversed(cur.named_children))

    def descendants(self, *types: str) -> Iterator["Tsx"]:
        for node in self.walk():
            if node.type in types:
                yield node

    def unwrap_export(self) -> "Tsx":
        """The declaration an ``export ...`` statement exports, else self unchanged."""
        if self.type == "export_statement":
            decl = self.field("declaration")
            if decl is not None:
                return decl
        return self

    def contains_jsx(self) -> bool:
        return any(node.is_jsx_element for node in self.walk())

    def top_declarations(self) -> list[tuple[str, "Tsx", "Tsx", bool]]:
        """(name, body, anchor, exported) for each top-level function and arrow/value const —
        the one place that walks module-level declarations, so detectors don't each re-roll it.
        """
        out: list[tuple[str, Tsx, Tsx, bool]] = []
        for top in self.named_children():
            exported = top.type == "export_statement"
            decl = top.unwrap_export()
            if decl.type == "function_declaration":
                out.extend(_named(decl.field("name"), decl, decl, exported))
            elif decl.type == "lexical_declaration":
                for d in decl.named_children():
                    if d.type == "variable_declarator":
                        out.extend(
                            _named(d.field("name"), d.field("value"), d, exported)
                        )
        return out

    # --- JSX element ---------------------------------------------------------

    def opening(self) -> "Tsx":
        """The tag carrying name + attributes: self when self-closing, else the
        ``jsx_opening_element`` child."""
        if self.type == "jsx_self_closing_element":
            return self
        for child in self.named_children():
            if child.type == "jsx_opening_element":
                return child
        return self

    def jsx_name(self) -> str:
        name = self.opening().field("name")
        return name.text if name is not None else ""

    def jsx_attributes(self) -> list["Tsx"]:
        return [c for c in self.opening().named_children() if c.type == "jsx_attribute"]

    def attributes(self) -> dict[str, "Tsx"]:
        return {a.attr_name(): a for a in self.jsx_attributes()}

    def child_elements(self) -> list["Tsx"]:
        if self.type == "jsx_self_closing_element":
            return []
        return [c for c in self.named_children() if c.is_jsx_element]

    def has_text_child(self) -> bool:
        """True if the element directly renders non-whitespace text (a real label), not only
        other elements/icons."""
        if self.type == "jsx_self_closing_element":
            return False
        for child in self.named_children():
            if child.type == "jsx_text" and child.text.strip():
                return True
            # an expression child renders text/value (`{label}`, `{n}`, `{fmt()}`) unless it
            # is itself an element (`{<Icon/>}`), which is markup, not text.
            if child.type == "jsx_expression":
                inner = child.named_children()
                if inner and not inner[0].is_jsx_element:
                    return True
        return False

    # --- JSX attribute -------------------------------------------------------

    def attr_name(self) -> str:
        for child in self.named_children():
            if child.type == "property_identifier":
                return child.text
        return ""

    def attr_value(self) -> "Tsx | None":
        seen_name = False
        for child in self.named_children():
            if child.type == "property_identifier" and not seen_name:
                seen_name = True
                continue
            return child
        return None

    def attr_value_text(self) -> str:
        """The attribute value as text: the literal for ``className="..."``, or the inner
        expression for ``tabIndex={3}`` / ``onClick={go}`` (braces stripped). Empty for a
        boolean attribute (``disabled``)."""
        value = self.attr_value()
        if value is None:
            return ""
        if value.type == "string":
            return "".join(
                c.text for c in value.named_children() if c.type == "string_fragment"
            )
        if value.type == "jsx_expression":
            inner = value.named_children()
            return inner[0].text if inner else ""
        return value.text


def _named(
    name: Tsx | None, body: Tsx | None, at: Tsx, exported: bool
) -> list[tuple[str, Tsx, Tsx, bool]]:
    if name is None or body is None:
        return []
    return [(name.text, body, at, exported)]


def is_pascal_case(name: str) -> bool:
    """A React component name: starts uppercase and is not all-caps (``GapRow``, ``Tabs``, or
    a single ``A``), excluding SCREAMING_SNAKE_CASE constants (``ACTION_META``,
    ``STATUS_LABEL``) that merely hold JSX values like an icon map."""
    return bool(name) and name[0].isupper() and (len(name) == 1 or not name.isupper())


def callee(call: Tsx) -> str:
    """The name of the function a call invokes: ``f()`` -> ``f``, ``a.b()`` -> ``b``."""
    fn = call.field("function")
    if fn is None:
        return ""
    if fn.type == "member_expression":
        return field_text(fn, "property")
    if fn.type == "identifier":
        return fn.text
    return ""


def field_text(node: Tsx, field: str) -> str:
    child = node.field(field)
    return child.text if child is not None else ""


def import_source(node: Tsx) -> str:
    """The module string of an ``import ... from "x"`` statement (quotes stripped)."""
    source = node.field("source")
    if source is None or source.type != "string":
        return ""
    return "".join(
        c.text for c in source.named_children() if c.type == "string_fragment"
    )
