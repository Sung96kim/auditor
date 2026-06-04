"""``auditor report`` — audit one file statelessly (manifest + findings in one call)."""

from pathlib import Path

from auditor.cli.apps import app
from auditor.cli.helpers import _check_format, _emit, _require_file, _run
from auditor.cli.options import Format, Output, Profile, ReportFile
from auditor.config import load_config
from auditor.discovery import find_root
from auditor.engine import ScanEngine
from auditor.models import ScanResult
from auditor.reporters import render


@app.command()
def report(
    file: ReportFile,
    profile: Profile = None,
    output: Output = None,
    fmt: Format = "json",
) -> None:
    """Audit one file (stateless) — manifest + findings in one call."""
    _require_file(file)
    _check_format(fmt)
    result = _run(_report(file, profile), f"auditing {file.name}…")
    _emit(render([result], fmt), output)


async def _report(file: Path, profile: str | None) -> ScanResult:
    root = find_root(file)
    engine = ScanEngine(root, load_config(root, profile=profile))
    return await engine.scan_file(file)
