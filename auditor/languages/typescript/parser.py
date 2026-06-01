"""tree-sitter parsing for TS/TSX/JS/JSX.

``.ts``/``.mts``/``.cts`` use the ``typescript`` grammar (it resolves the ``<T>`` cast vs
JSX ambiguity toward casts); everything else uses the ``tsx`` grammar, which is a superset
that also parses plain JS/JSX. The two ``Language`` objects are built once and reused.
"""

from typing import ClassVar

import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Parser, Tree

_TS = Language(tsts.language_typescript())
_TSX = Language(tsts.language_tsx())
_TS_ONLY = (".ts", ".mts", ".cts")


class TsParser:
    """Parses a source string into a tree-sitter tree, choosing the grammar by extension."""

    _parsers: ClassVar[dict[Language, Parser]] = {}

    @classmethod
    def _parser_for(cls, language: Language) -> Parser:
        parser = cls._parsers.get(language)
        if parser is None:
            parser = Parser(language)
            cls._parsers[language] = parser
        return parser

    @classmethod
    def language_for(cls, path: str) -> Language:
        return _TS if path.endswith(_TS_ONLY) else _TSX

    @classmethod
    def parse(cls, source: str, *, path: str) -> Tree:
        parser = cls._parser_for(cls.language_for(path))
        return parser.parse(source.encode("utf-8"))


def root_of(source: str, *, path: str) -> Node:
    return TsParser.parse(source, path=path).root_node
