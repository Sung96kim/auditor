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
from auditor.languages.typescript.nodes import Tsx

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
_MIN_FN_TOKENS = 4  # a function shape needs real substance, else small fns collide by luck


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
        return rows

    def _shape(self, body: Tsx | None, name: Tsx | None, at: Tsx) -> list[ShapeRow]:
        if body is None or name is None:
            return []
        symbol = name.text
        if symbol[:1].isupper() and body.contains_jsx():
            tags = _jsx_skeleton(body)
            if len(tags) >= _MIN_JSX_TAGS:
                return [ShapeRow(_hash("tsc|" + ">".join(tags)), "component", symbol, at.line)]
            return []
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
    """Ordered tag names of every JSX element rendered in the body — the visual structure
    with text/props/handlers stripped."""
    return [n.jsx_name() for n in body.walk() if n.is_jsx_element]


def _function_signature(body: Tsx) -> list[str]:
    """Control-flow nodes interleaved with the names of what the body *calls* — so two
    functions match only when they do the same operations, not merely share a silhouette."""
    parts: list[str] = []
    for node in body.walk():
        if node.type in _SKELETON_NODES:
            parts.append(node.type)
        elif node.type == "call_expression":
            parts.append("c:" + _callee(node.field("function")))
        elif node.type == "new_expression":
            parts.append("new:" + _callee(node.field("constructor")))
    return parts


def _callee(fn: Tsx | None) -> str:
    if fn is None:
        return ""
    if fn.type == "member_expression":
        prop = fn.field("property")
        return prop.text if prop is not None else ""
    if fn.type == "identifier":
        return fn.text
    return fn.type


def _param_types(body: Tsx) -> str:
    params = body.field("parameters")
    if params is None:
        return "0"
    types = []
    for param in params.named_children():
        annotation = param.field("type")
        types.append(annotation.text if annotation is not None else "_")
    return ",".join(types)
