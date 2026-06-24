"""``auditor manifest`` — print the AST class+function manifest for one Python file."""

import ast

import typer

from auditor.cli.apps import app
from auditor.cli.helpers import _fail, _present, _require_file
from auditor.cli.options import ManifestFile
from auditor.cli.render import render_manifest_list
from auditor.models import ManifestEntry


@app.command()
def manifest(
    file: ManifestFile,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Print the AST class+function manifest for one file (no detectors). Python-only."""
    _require_file(file)
    if file.suffix not in (".py", ".pyi"):
        _fail(f"manifest is Python-only; {file.name} is not a .py file")
    try:
        tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError) as exc:  # ValueError: source contains null bytes
        _fail(f"could not parse {file.name}: {exc}")
    entries = ManifestEntry.from_module(tree)
    _present(
        [e.model_dump(mode="json") for e in entries],
        render_manifest_list,
        as_json=json_,
    )
