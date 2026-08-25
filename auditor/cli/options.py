"""Reusable typer option/argument types — keeps each command signature a flat, readable list."""

from pathlib import Path
from typing import Annotated

import typer

from auditor.graph.model import (
    DEFAULT_FLOW_DEPTH,
    MAX_FLOW_DEPTH,
    MAX_FLOW_LIMIT,
    QUEUE_ROW_LIMIT,
    CallForm,
    UnresolvedReason,
)
from auditor.models import RuleId

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
RootArg = Annotated[
    Path,
    typer.Option(
        "-r",
        "--root",
        help="Repo whose config and plugins load (default: walk up from here).",
    ),
]
AggregateOut = Annotated[Path, typer.Option("-o", "--out", help="Write AUDIT.md here.")]

Incremental = Annotated[
    bool, typer.Option("-i", "--incremental", help="Use/update the on-disk cache.")
]
NoIndex = Annotated[
    bool, typer.Option("-n", "--no-index", help="Force stateless (no cache).")
]
Isolated = Annotated[
    bool,
    typer.Option(
        "--isolated",
        help="Single file only: skip the index + cross-file pass (faster standalone check).",
    ),
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
NoSkips = Annotated[
    bool,
    typer.Option(
        "--no-skips",
        help="Ignore in-file `auditor: skip` directives (un-silenceable sweep).",
    ),
]
IncludeGitignored = Annotated[
    bool,
    typer.Option(
        "--include-gitignored", help="Audit git-ignored files too (default: skip them)."
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
RuleFilter = Annotated[
    list[RuleId]
    | None,  # RuleId is `str`: the valid set is the runtime registry, not a frozen enum
    typer.Option(
        "--rule",
        help="Only show findings for these rule ids (repeatable), e.g. --rule SA-RAW-SQL.",
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
ConfigJson = Annotated[
    str | None,
    typer.Option(
        "--config-json",
        help="JSON object of config overrides, merged as the highest layer, "
        'e.g. \'{"sqlalchemy":{"expire_on_commit":true}}\'.',
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
IgnoreForce = Annotated[
    bool,
    typer.Option(
        "--force",
        help="Allow a rule_id not in the registry (e.g. a not-yet-loaded plugin rule).",
    ),
]
Malware = Annotated[
    bool | None,
    typer.Option(
        "--malware/--no-malware",
        help="Run the opt-in malware scan (ClamAV content + osv-scanner dependency "
        "passes) for this run, overriding [tool.auditor.malware_scan] enabled.",
    ),
]


# --- `config` / `init` options ---
UserConfig = Annotated[
    bool,
    typer.Option("--user", help="Show the resolved user settings ($AUDITOR_HOME)."),
]
InitRepo = Annotated[
    bool,
    typer.Option(
        "--repo", help="Also write the per-repo settings file and breadcrumb."
    ),
]
InitCheck = Annotated[
    bool,
    typer.Option("--check", help="Report only: write nothing, list unknown keys."),
]
InitMigrate = Annotated[
    bool,
    typer.Option("--migrate", help="Point a moved repo's breadcrumb at its new root."),
]
CleanStatus = Annotated[
    bool,
    typer.Option(
        "--clean-status", help="Delete a leftover <repo>/.auditor/.status.json."
    ),
]
InitForce = Annotated[
    bool,
    typer.Option(
        "--force", help="Stamp the current version on a settings file that predates it."
    ),
]


# --- `graph` sub-app options ---
GraphTarget = Annotated[Path, typer.Argument(help="Repo root (default: .)")]
QueueReason = Annotated[
    list[UnresolvedReason] | None,
    typer.Option("--reason", help="Only these queue reasons (repeatable)."),
]
QueueCallForm = Annotated[
    list[CallForm] | None,
    typer.Option("--call-form", help="Only these call forms (repeatable)."),
]
QueueLimit = Annotated[
    int,
    typer.Option(
        "--limit", min=1, help=f"Cap the rows shown (default {QUEUE_ROW_LIMIT})."
    ),
]
QueueExternal = Annotated[
    bool,
    typer.Option(
        "--external/--no-external",
        help="Show rows bound to a non-repo import (dimmed, sorted last).",
    ),
]


# --- `graph flow` options ---
FlowIn = Annotated[
    bool,
    typer.Option(
        "--in", help="Reverse the walk: what reaches the symbol, not what it reaches."
    ),
]
FlowDepth = Annotated[
    int,
    typer.Option(
        "--depth",
        min=0,
        max=MAX_FLOW_DEPTH,
        help="Hops to follow from the symbol.",
    ),
]
FlowLimit = Annotated[
    int,
    typer.Option(
        "--limit",
        min=1,
        max=MAX_FLOW_LIMIT,
        help="Cap on nodes emitted; shallow levels complete first.",
    ),
]
FlowKinds = Annotated[
    str | None,
    typer.Option(
        "--kinds",
        help="Extra edge kinds to follow, comma separated, e.g. --kinds inherits,references_type.",
    ),
]
FlowIncludeTests = Annotated[
    bool,
    typer.Option(
        "--include-tests", help="Keep test and test-support symbols in the tree."
    ),
]
FlowExpandHubs = Annotated[
    bool, typer.Option("--expand-hubs", help="Expand hubs instead of eliding them.")
]
FlowStopAt = Annotated[
    list[str] | None,
    typer.Option("--stop-at", help="Module glob to stop expanding at (repeatable)."),
]
FlowSymbol = Annotated[
    str | None,
    typer.Option("--flow", help="Export the flow tree from this symbol instead."),
]
ExportDepth = Annotated[
    int | None,
    typer.Option(
        "--depth",
        min=0,
        max=MAX_FLOW_DEPTH,
        help=f"Hops for --symbol (default 1) or --flow (default {DEFAULT_FLOW_DEPTH}).",
    ),
]
