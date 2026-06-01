"""typescript/parser.py: grammar selection + parsing."""

from auditor.languages.typescript.parser import _TS, _TSX, TsParser, root_of


def test_tsx_grammar_for_tsx_and_js():
    assert TsParser.language_for("X.tsx") is _TSX
    assert TsParser.language_for("a.jsx") is _TSX
    assert TsParser.language_for("a.js") is _TSX


def test_ts_grammar_for_ts():
    assert TsParser.language_for("a.ts") is _TS
    assert TsParser.language_for("a.mts") is _TS


def test_parses_tsx_to_named_root():
    root = root_of("export const x = <div />;\n", path="X.tsx")
    assert root.type == "program"
    types = {c.type for c in root.named_children}
    assert "export_statement" in types


def test_parser_instances_are_reused():
    a = TsParser._parser_for(_TSX)
    b = TsParser._parser_for(_TSX)
    assert a is b
