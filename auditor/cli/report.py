"""``auditor report`` — audit one file statelessly (manifest + findings in one call)."""

from pathlib import Path

from pydantic import ValidationError

from auditor.cli.apps import app
from auditor.cli.helpers import (
    _check_format,
    _emit,
    _fail,
    _format_config_error,
    _parse_config_json,
    _require_file,
    _run,
)
from auditor.cli.options import (
    ConfigJson,
    Format,
    Output,
    Profile,
    ReportFile,
    ShowIgnored,
)
from auditor.engine import audit_target
from auditor.reporters import render


@app.command()
def report(
    file: ReportFile,
    profile: Profile = None,
    output: Output = None,
    fmt: Format = "json",
    show_ignored: ShowIgnored = False,
    config_json: ConfigJson = None,
) -> None:
    """Audit one file (stateless) — manifest + findings in one call."""
    _require_file(file)
    _check_format(fmt)
    overrides = _parse_config_json(config_json)
    try:
        results = _run(
            audit_target(
                Path(file),
                profile=profile,
                show_ignored=show_ignored,
                config_overrides=overrides,
            ),
            f"auditing {file.name}…",
        )
    except ValidationError as exc:
        _fail(f"invalid config — {_format_config_error(exc)}")
    _emit(render(results, fmt), output)
