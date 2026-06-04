"""Manifest language core: filename classification, parsing, and line anchoring.

The manifest language is dispatched by *filename* (not suffix) and parses each manifest once into
structured ``data`` + raw lines. These tests pin that core independent of any one detector."""

import pytest
from _support import AuditorSettings, ResolvedConfig

from auditor.languages.manifest.base import (
    NPM,
    UNKNOWN,
    ManifestContext,
    manifest_type,
)
from auditor.models import FileRole


@pytest.mark.parametrize(
    "path, expected",
    [
        ("package.json", NPM),
        ("a/b/package.json", NPM),
        (
            "pyproject.toml",
            UNKNOWN,
        ),  # not parsed — dependency-graph scanning is out of scope
        ("requirements.txt", UNKNOWN),
        ("tsconfig.json", UNKNOWN),
        ("setup.py", UNKNOWN),
        ("README.md", UNKNOWN),
    ],
)
def test_manifest_type_classification(path, expected):
    assert manifest_type(path) == expected


def _ctx(source: str, rel_path: str = "package.json") -> ManifestContext:
    rc = ResolvedConfig(AuditorSettings(), role=FileRole.PRODUCTION, rel_path=rel_path)
    return ManifestContext(
        file_path=rel_path, source=source, role=FileRole.PRODUCTION, config=rc
    )


def test_parses_npm_json():
    ctx = _ctx('{"name": "x", "scripts": {"build": "tsc"}}')
    assert ctx.manifest_type == NPM
    assert ctx.data["name"] == "x"


@pytest.mark.parametrize(
    "rel_path, source",
    [
        ("package.json", "{ not valid json"),
        ("pyproject.toml", "[project\nbroken = "),
        ("package.json", "[1, 2, 3]"),  # valid JSON but not an object
    ],
)
def test_malformed_or_non_object_manifest_parses_to_empty(rel_path, source):
    # a manifest that doesn't parse (or isn't a mapping) yields {}, never an exception
    assert _ctx(source, rel_path=rel_path).data == {}


def test_line_of_finds_first_match_else_one():
    ctx = _ctx('{\n  "scripts": {\n    "postinstall": "x"\n  }\n}')
    assert ctx.line_of('"postinstall"') == 3
    assert ctx.line_of("nonexistent") == 1  # best-effort anchor falls back to line 1


def test_line_of_requires_all_needles_on_one_line():
    ctx = _ctx('{\n  "a": 1,\n  "b": 2\n}')
    assert ctx.line_of('"a"', "1") == 2
    assert ctx.line_of('"a"', "2") == 1  # never co-located → fallback


def test_line_text_is_stripped_and_range_safe():
    ctx = _ctx('  {"a": 1}  ')
    assert ctx.line_text(1) == '{"a": 1}'
    assert ctx.line_text(0) == ""
    assert ctx.line_text(999) == ""


def test_unknown_manifest_has_no_structured_data():
    # a non-npm manifest is `unknown` with no dict payload (raw lines remain available)
    ctx = _ctx("requests>=2.0\nflask\n", rel_path="requirements.txt")
    assert ctx.manifest_type == UNKNOWN
    assert ctx.data == {}
    assert ctx.lines == ["requests>=2.0", "flask"]
