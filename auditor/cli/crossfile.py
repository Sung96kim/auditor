"""``auditor crossfile`` — recompute cross-file duplicate findings from the index."""

from pathlib import Path

import typer

from auditor import crossfile as crossfile_pass
from auditor.cli.apps import app
from auditor.cli.helpers import open_index, present, run
from auditor.cli.options import DirTarget
from auditor.cli.render import render_crossfile
from auditor.config import load_config
from auditor.discovery import find_root


@app.command()
def crossfile(
    target: DirTarget = Path("."),
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Recompute cross-file duplicate findings from the index."""
    root = find_root(target)
    count = run(_crossfile(root), "cross-file pass…")
    present({"cross_file_findings": count}, render_crossfile, as_json=json_)


async def _crossfile(root: Path) -> int:
    settings = load_config(root)
    async with await open_index(root) as index:
        per_file = await crossfile_pass.run(
            index,
            settings_modules=settings.settings_modules,
            settings_cohesion_on=settings.settings_cohesion,
        )
        return sum(len(v) for v in per_file.values())
