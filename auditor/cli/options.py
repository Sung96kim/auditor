"""Reusable typer option/argument types — keeps each command signature a flat, readable list."""

from pathlib import Path
from typing import Annotated

import typer

ScanTarget = Annotated[Path, typer.Argument(help="File or directory to audit.")]
DirTarget = Annotated[Path, typer.Argument()]
ReportFile = Annotated[Path, typer.Argument(help="Python file.")]
ManifestFile = Annotated[Path, typer.Argument(help="Python file (.py).")]
ScopePaths = Annotated[
    list[Path], typer.Argument(help="Files to register in the audit scope.")
]

Profile = Annotated[
    str | None,
    typer.Option(
        "-p", "--profile", help="Override the profile: base|strict|pydantic|all-strict."
    ),
]
Output = Annotated[
    Path | None,
    typer.Option("-o", "--output", help="Write the report here instead of stdout."),
]
Format = Annotated[
    str | None, typer.Option("-f", "--format", help="json | sarif | md | html.")
]
RootArg = Annotated[Path, typer.Option("-r", "--root")]
AggregateOut = Annotated[Path, typer.Option("-o", "--out", help="Write AUDIT.md here.")]

Incremental = Annotated[
    bool, typer.Option("-i", "--incremental", help="Use/update the on-disk cache.")
]
NoIndex = Annotated[
    bool, typer.Option("-n", "--no-index", help="Force stateless (no cache).")
]
StrictTests = Annotated[
    bool,
    typer.Option("-t", "--strict-tests", help="Audit tests at production strength."),
]
AllowLocalPlugins = Annotated[
    bool,
    typer.Option("-a", "--allow-local-plugins", help="Load .auditor/plugins/*.py."),
]
Exclude = Annotated[
    list[str] | None,
    typer.Option(
        "-x", "--exclude", help="Glob to ignore (repeatable), on top of config."
    ),
]
NoNoqa = Annotated[
    bool,
    typer.Option(
        "--no-noqa", help="Ignore in-file noqa directives (un-silenceable sweep)."
    ),
]
Serve = Annotated[
    bool,
    typer.Option(
        "--serve", help="Render HTML and open it in a browser on a local port."
    ),
]
SeverityFilter = Annotated[
    list[str] | None,
    typer.Option(
        "-s",
        "--severity",
        help="Only show these severities (repeatable): blocking|high|medium|low|suggestion.",
    ),
]
MinSeverity = Annotated[
    str | None,
    typer.Option(
        "-m", "--min-severity", help="Only show findings at or above this severity."
    ),
]
Since = Annotated[
    str | None,
    typer.Option(
        "--since",
        help="Scope output to files changed vs a git ref. The whole repo is still scanned so cross-file rules stay correct.",
    ),
]
Changed = Annotated[
    bool,
    typer.Option("--changed", help="Scope output to working-tree changes (vs HEAD)."),
]
VsBase = Annotated[
    bool,
    typer.Option(
        "--vs-base", help="Scope output to changes vs the configured diff_base."
    ),
]
FailOn = Annotated[
    str | None,
    typer.Option(
        "--fail-on", help="Exit non-zero if any finding is at or above this severity."
    ),
]
BaselineFile = Annotated[
    Path | None,
    typer.Option(
        "--baseline", help="Hide findings in this baseline; report/gate only new ones."
    ),
]
WriteBaseline = Annotated[
    Path | None,
    typer.Option(
        "--write-baseline", help="Write current findings to a baseline file and exit."
    ),
]
PinRoot = Annotated[
    Path | None,
    typer.Option(
        "--root",
        help="Pin the project root (default: nearest .git/pyproject.toml/.auditor).",
    ),
]
Verbose = Annotated[
    int,
    typer.Option(
        "-v",
        "--verbose",
        count=True,
        help="Log to stderr: -v files, -vv detail, -vvv findings.",
    ),
]
ShowIgnored = Annotated[
    bool,
    typer.Option(
        "--show-ignored", help="Include findings hidden by persistent ignores."
    ),
]
# --- `ignore` sub-app options ---
IgnoreRuleId = Annotated[
    str, typer.Argument(help="Rule id to ignore (e.g. PY-SEC-WEAK-HASH).")
]
IgnoreSelector = Annotated[
    str, typer.Argument(help="An ignore id (from `ignore list`) or a rule_id.")
]
IgnoreFile = Annotated[
    str | None,
    typer.Option("--file", help="Limit the ignore to this file (relative to root)."),
]
IgnoreLine = Annotated[
    int | None,
    typer.Option("--line", help="Limit the ignore to this line (requires --file)."),
]
IgnoreReason = Annotated[
    str | None, typer.Option("--reason", help="Optional note stored with the ignore.")
]
