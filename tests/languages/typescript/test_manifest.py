"""typescript/manifest.py: top-level function/class/component entries."""

from auditor.languages.typescript.manifest import build_manifest
from auditor.languages.typescript.nodes import Tsx
from auditor.languages.typescript.parser import root_of
from auditor.models import ManifestEntryKind


def _manifest(src: str):
    return build_manifest(Tsx(root_of(src, path="X.tsx")))


def test_manifest_lists_functions_classes_and_arrow_components():
    src = (
        "export function Panel() {\n  return <div />;\n}\n"
        "class Store {}\n"
        "const Sidebar = () => <nav />;\n"
    )
    entries = {e.symbol: e for e in _manifest(src)}
    assert entries["Panel"].kind is ManifestEntryKind.FUNCTION
    assert entries["Store"].kind is ManifestEntryKind.CLASS
    assert entries["Sidebar"].kind is ManifestEntryKind.FUNCTION


def test_manifest_records_arg_count():
    src = "export function f(a, b, c) {\n  return a;\n}\n"
    entry = _manifest(src)[0]
    assert entry.symbol == "f" and entry.arg_count == 3
