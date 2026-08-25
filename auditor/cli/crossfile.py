"""``auditor crossfile`` — recompute cross-file duplicate findings from the index."""

from pathlib import Path

import typer

from auditor.cli.apps import app
from auditor.cli.helpers import (
    load_settings,
    open_index,
    present,
    run,
    warn_unknown_config,
)
from auditor.cli.options import DirTarget
from auditor.cli.render import render_crossfile
from auditor.config import unknown_repo_keys
from auditor.crossfile import CrossFileInputs
from auditor.discovery import find_root
from auditor.ignores import IgnoreList


@app.command()
def crossfile(
    target: DirTarget = Path("."),
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Recompute cross-file duplicate findings from the index."""
    root = find_root(target)
    warn_unknown_config(unknown_repo_keys(root))
    count = run(_crossfile(root), "cross-file pass…")
    present({"cross_file_findings": count}, render_crossfile, as_json=json_)


async def _crossfile(root: Path) -> int:
    inputs = CrossFileInputs.derive(root, load_settings(root))
    async with await open_index(root) as index:
        per_file = await inputs.recompute(index)
        ignores = IgnoreList.from_rows(await index.ignores.list())
        total = 0
        for rel, findings in per_file.items():
            kept, _ = inputs.apply_skips(rel, findings)
            total += len(ignores.kept(rel, kept))
        return total
