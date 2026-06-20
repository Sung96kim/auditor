"""Identifier tokenization + the naming-document fallback cascade (spec §9a). Stdlib only."""

import re
from collections.abc import Iterable

TEXT_FLOOR = 4  # min unique concept tokens before a symbol is `text_sparse`

_VERB = {}
for _canon, _syns in {
    "read": "get fetch load read retrieve lookup find query select list",
    "write": "save store write persist update put set insert",
    "create": "make create build new construct init",
    "delete": "delete remove drop clear discard",
    "check": "check validate verify ensure assert",
    "convert": "convert parse render serialize format dump",
}.items():
    for _w in _syns.split():
        _VERB[_w] = _canon

_STOP = set(
    [
        "self",
        "cls",
        "the",
        "a",
        "an",
        "of",
        "to",
        "in",
        "for",
        "and",
        "or",
        "not",
        "is",
        "be",
        "as",
        "if",
        "else",
        "return",
        "def",
        "class",
        "with",
        "from",
        "import",
        "args",
        "kwargs",
        "none",
        "true",
        "false",
        "value",
        "val",
        "obj",
        "item",
        "items",
        "data",
        "result",
        "results",
        "out",
        "res",
        "tmp",
        "i",
        "j",
        "k",
        "n",
        "x",
        "y",
        "str",
        "int",
        "bool",
        "list",
        "dict",
        "set",
        "tuple",
        "type",
        "name",
        "names",
        "id",
    ]
)

_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def split_ident(s: str) -> list[str]:
    out: list[str] = []
    for part in re.split(r"[_\W]+", s or ""):
        out += _CAMEL.findall(part) or ([part] if part else [])
    return [t.lower() for t in out if t]


def normalize_tokens(tokens: Iterable[str]) -> list[str]:
    return [_VERB.get(t, t) for t in tokens if len(t) >= 2 and t not in _STOP]


def _toks(text: str) -> list[str]:
    return normalize_tokens(split_ident(text))


def symbol_document(
    *,
    name: str,
    args: list[str],
    docstring: str,
    body_idents: list[str],
    param_types: list[str],
    path_tokens: list[str],
    class_name: str | None,
) -> list[str]:
    """Fallback cascade (§9a): declaration (name+args+docstring) weighted 3x over body identifiers,
    plus always-present context (signature types, enclosing-class name, module-path tokens). Pure
    numeric/hash path tokens are dropped (e.g. alembic revision ids)."""
    head = _toks(name) + [t for a in args for t in _toks(a)] + _toks(docstring)
    body = [t for ident in body_idents for t in _toks(ident)]
    types = [t for pt in param_types for t in _toks(pt)]
    ctx = [t for t in path_tokens if not t.isdigit() and not _is_hashlike(t)]
    if class_name:
        ctx += _toks(class_name)
    # when the declaration collapses entirely to stopwords, surface raw name tokens so the
    # symbol is still retrievable (e.g. name="id" with rich path/class context)
    if not head:
        ctx = [t.lower() for t in split_ident(name) if len(t) >= 2] + ctx
    return head * 3 + body + types + ctx


def _is_hashlike(t: str) -> bool:
    return len(t) >= 8 and bool(re.fullmatch(r"[0-9a-f]+", t))


def is_text_sparse(doc: list[str]) -> bool:
    return len(set(doc)) < TEXT_FLOOR
