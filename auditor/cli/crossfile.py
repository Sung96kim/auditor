"""``auditor crossfile`` — recompute cross-file duplicate findings from the index."""

from pathlib import Path

from auditor import crossfile as crossfile_pass
from auditor.cli.apps import app
from auditor.cli.helpers import _echo_json, _open_index, _run
from auditor.cli.options import DirTarget
from auditor.config import load_config
from auditor.discovery import find_root


@app.command()
def crossfile(target: DirTarget = Path(".")) -> None:
    """Recompute cross-file duplicate findings from the index."""
    root = find_root(target)
    count = _run(_crossfile(root), "cross-file pass…")
    _echo_json({"cross_file_findings": count})


async def _crossfile(root: Path) -> int:
    settings = load_config(root)
    async with await _open_index(root) as index:
        per_file = await crossfile_pass.run(
            index,
            settings_modules=settings.settings_modules,
            settings_cohesion_on=settings.settings_cohesion,
        )
        return sum(len(v) for v in per_file.values())
