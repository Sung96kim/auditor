"""``auditor crossfile`` — recompute cross-file duplicate findings from the index."""

from pathlib import Path

from auditor import crossfile as crossfile_pass
from auditor.cli.apps import app
from auditor.cli.helpers import _echo_json, _index_db, _run
from auditor.cli.options import DirTarget
from auditor.discovery import find_root
from auditor.index import IndexStore


@app.command()
def crossfile(target: DirTarget = Path(".")) -> None:
    """Recompute cross-file duplicate findings from the index."""
    root = find_root(target)
    count = _run(_crossfile(root), "cross-file pass…")
    _echo_json({"cross_file_findings": count})


async def _crossfile(root: Path) -> int:
    async with await IndexStore.connect(_index_db(root)) as index:
        per_file = await crossfile_pass.run(index)
        return sum(len(v) for v in per_file.values())
