"""``auditor aggregate`` — roll up the index into AUDIT.md."""

from pathlib import Path

import typer

from auditor.aggregate import AuditAggregator
from auditor.cli.apps import app
from auditor.cli.helpers import _index_db, _run
from auditor.cli.options import AggregateOut, DirTarget
from auditor.discovery import find_root
from auditor.index import IndexStore


@app.command()
def aggregate(
    target: DirTarget = Path("."),
    out: AggregateOut = Path("AUDIT.md"),
) -> None:
    """Roll up the index into AUDIT.md (run `scan --incremental` first)."""
    root = find_root(target)
    path = _run(_aggregate(root, out), "aggregating…")
    typer.echo(f"wrote {path}")


async def _aggregate(root: Path, out: Path) -> Path:
    async with await IndexStore.connect(_index_db(root)) as index:
        return await AuditAggregator(index).write(out)
