"""``auditor report`` — audit one file statelessly (manifest + findings in one call)."""

from pathlib import Path

from auditor.cli.apps import app
from auditor.cli.helpers import _check_format, _emit, _require_file, _run
from auditor.cli.options import Format, Output, Profile, ReportFile, ShowIgnored
from auditor.engine import audit_target
from auditor.reporters import render


@app.command()
def report(
    file: ReportFile,
    profile: Profile = None,
    output: Output = None,
    fmt: Format = "json",
    show_ignored: ShowIgnored = False,
) -> None:
    """Audit one file (stateless) — manifest + findings in one call."""
    _require_file(file)
    _check_format(fmt)
    results = _run(
        audit_target(Path(file), profile=profile, show_ignored=show_ignored),
        f"auditing {file.name}…",
    )
    _emit(render(results, fmt), output)
