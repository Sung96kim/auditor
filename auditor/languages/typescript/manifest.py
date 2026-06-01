"""A light top-level manifest for a TS/TSX file: exported/top-level functions, classes, and
component-shaped arrow consts. Mirrors the Python manifest's role (orient the reader) without
the JSX-tree depth the design-system skill builds by hand."""

from auditor.languages.typescript.nodes import Tsx
from auditor.models import ManifestEntry, ManifestEntryKind


def build_manifest(root: Tsx) -> list[ManifestEntry]:
    entries: list[ManifestEntry] = []
    for top in root.named_children():
        decl = top.unwrap_export()
        if decl.type == "function_declaration":
            entries.append(_function_entry(decl, decl.field("name")))
        elif decl.type == "class_declaration":
            name = decl.field("name")
            entries.append(
                ManifestEntry(
                    line=decl.line,
                    symbol=name.text if name else "",
                    kind=ManifestEntryKind.CLASS,
                )
            )
        elif decl.type == "lexical_declaration":
            entries.extend(_arrow_consts(decl))
    return entries


def _function_entry(node: Tsx, name: Tsx | None) -> ManifestEntry:
    params = node.field("parameters")
    return ManifestEntry(
        line=node.line,
        symbol=name.text if name else "",
        kind=ManifestEntryKind.FUNCTION,
        arg_count=len(params.named_children()) if params else 0,
    )


def _arrow_consts(decl: Tsx) -> list[ManifestEntry]:
    out: list[ManifestEntry] = []
    for declarator in decl.named_children():
        if declarator.type != "variable_declarator":
            continue
        value = declarator.field("value")
        if value is not None and value.type == "arrow_function":
            out.append(_function_entry(value, declarator.field("name")))
    return out
