"""Normalized shape hashes for React components + plain functions, feeding the cross-file
dedup pass. A *shape* abstracts away names/text so structurally-identical definitions in
different files collide:

- a component reduces to its rendered JSX skeleton (the nested tag-name tree),
- a function reduces to its parameter count + a normalized statement skeleton.

Hashes are namespaced (``tsc|`` / ``tsf|``) so a TS shape never collides with a Python one
even though they share the index ``shapes`` table.
"""

import hashlib

from auditor.languages.base import ShapeRow
from auditor.languages.typescript.nodes import Tsx, callee, field_text, is_pascal_case

_SKELETON_NODES = (
    "if_statement",
    "for_statement",
    "for_in_statement",
    "while_statement",
    "switch_statement",
    "return_statement",
    "try_statement",
    "ternary_expression",
)
_MIN_JSX_TAGS = 4  # ignore trivial components (a bare wrapper) — too generic to dedup
_MIN_COMPONENT_DISTINCT = (
    3  # all-<div> components collide by luck; need real tag variety
)
_MIN_FN_TOKENS = (
    4  # a function shape needs real substance, else small fns collide by luck
)
# A recurring JSX *sub-tree* (inline in different components) is worth extracting only when
# it's a real composed block, not a layout wrapper — recurring `<div className="flex">` is
# everywhere and would drown the signal.
_MIN_BLOCK_TAGS = 6
_MIN_BLOCK_DISTINCT = 3
_FUNCTION_BODIES = {"function_declaration", "arrow_function", "function_expression"}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class ShapeExtractor:
    """Extracts component + function shape rows from one parsed TS/TSX module."""

    def __init__(self, root: Tsx) -> None:
        self.root = root

    def shapes(self) -> list[ShapeRow]:
        rows: list[ShapeRow] = []
        for top in self.root.named_children():
            decl = top.unwrap_export()
            if decl.type == "function_declaration":
                rows.extend(self._shape(decl, decl.field("name"), decl))
            elif decl.type == "lexical_declaration":
                for declarator in decl.named_children():
                    if declarator.type == "variable_declarator":
                        rows.extend(
                            self._shape(
                                declarator.field("value"),
                                declarator.field("name"),
                                declarator,
                            )
                        )
        rows.extend(self._block_shapes())
        return rows

    def _block_shapes(self) -> list[ShapeRow]:
        """Shapes for substantial inline JSX sub-trees, so the same hand-rolled block sitting
        inside different components/files groups → extract a shared component. Only the
        *outermost* qualifying block in a region is emitted, so one duplicated region is one
        finding, not one per nesting level."""
        out: list[ShapeRow] = []
        self._collect_blocks(self.root, out)
        return out

    def _collect_blocks(self, node: Tsx, out: list[ShapeRow]) -> None:
        for child in node.named_children():
            # a fragment (`<>...</>`) parses as a jsx_element with no name — it's grouping,
            # not a real block, so descend through it rather than treating it as the block.
            is_block = child.is_jsx_element and bool(child.jsx_name())
            tags = _jsx_skeleton(child) if is_block else []
            if len(tags) >= _MIN_BLOCK_TAGS and len(set(tags)) >= _MIN_BLOCK_DISTINCT:
                out.append(
                    ShapeRow(
                        _hash("tsb|" + ">".join(tags)), "jsx-block", tags[0], child.line
                    )
                )  # maximal block — do not descend into it
            else:
                self._collect_blocks(child, out)

    def _shape(self, body: Tsx | None, name: Tsx | None, at: Tsx) -> list[ShapeRow]:
        if body is None or name is None:
            return []
        symbol = name.text
        if is_pascal_case(symbol) and body.contains_jsx():
            tags = _jsx_skeleton(body)
            if len(tags) >= _MIN_JSX_TAGS and len(set(tags)) >= _MIN_COMPONENT_DISTINCT:
                return [
                    ShapeRow(
                        _hash("tsc|" + ">".join(tags)), "component", symbol, at.line
                    )
                ]
            return []
        if body.type not in _FUNCTION_BODIES:
            return []  # a data const (lookup map, options array) is not a duplicate "function"
        signature = _function_signature(body)
        if len(signature) >= _MIN_FN_TOKENS:
            params = _param_types(body)
            return [
                ShapeRow(
                    _hash(f"tsf|{params}|{'>'.join(signature)}"),
                    "ts-function",
                    symbol,
                    at.line,
                )
            ]
        return []


def _jsx_skeleton(body: Tsx) -> list[str]:
    """Ordered tag names of every named JSX element rendered in the body — the visual
    structure with text/props/handlers stripped. Fragments (no name) are skipped."""
    return [name for n in body.walk() if n.is_jsx_element and (name := n.jsx_name())]


def _function_signature(body: Tsx) -> list[str]:
    """Control flow + the names of what the body calls, *references*, and keys it builds — so
    two functions match only when they do the same operations on the same things, not merely
    share a silhouette. Member/property names keep apart twins that differ only in which
    function/field they touch (``api.createResume`` vs ``api.createSession``, a tone enum vs a
    className object) while preserving genuine copies (same calls + members)."""
    return [token for node in body.walk() if (token := _signature_token(node))]


def _signature_token(node: Tsx) -> str | None:
    t = node.type
    if t in _SKELETON_NODES:
        return t
    if t == "call_expression":
        return "c:" + callee(node)
    if t == "new_expression":
        return "new:" + field_text(node, "constructor")
    if t == "member_expression":
        return "m:" + field_text(node, "property")
    if t == "pair":
        return "k:" + field_text(node, "key")
    return None


def _param_types(body: Tsx) -> str:
    params = body.field("parameters")
    if params is None:
        return "0"
    types = []
    for param in params.named_children():
        annotation = param.field("type")
        types.append(annotation.text if annotation is not None else "_")
    return ",".join(types)
