"""``auditor aggregate`` — roll up the index into AUDIT.md."""

from pathlib import Path

from auditor.aggregate import AuditAggregator
from auditor.cli.apps import app
from auditor.cli.console import err_console
from auditor.cli.helpers import open_index, run
from auditor.cli.options import AggregateOut, DirTarget
from auditor.discovery import find_root


@app.command()
def aggregate(
    target: DirTarget = Path("."),
    out: AggregateOut = Path("AUDIT.md"),
) -> None:
    """Roll up the index into AUDIT.md (run `scan --incremental` first)."""
    root = find_root(target)
    path = run(_aggregate(root, out), "aggregating…")
    err_console.print(f"[green]✓[/green] wrote [bold]{path}[/bold]")


async def _aggregate(root: Path, out: Path) -> Path:
    async with await open_index(root) as index:
        return await AuditAggregator(index).write(out)
