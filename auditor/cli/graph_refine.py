"""The queue and the corrections against it: ``auditr graph unresolved``, ``graph refinements``,
``graph refine`` and ``graph log``.

``cli/graph.py`` calls :func:`register` at the bottom of its module; this module never imports it
back, so the ``graph`` sub-app stays a one-way dependency.
"""

from collections.abc import Awaitable, Callable, Sequence
from functools import partial
from pathlib import Path
from typing import get_args

import typer
from pydantic import ValidationError

from auditor.cli.console import err_console
from auditor.cli.helpers import (
    cli_root,
    fail,
    load_settings,
    load_user,
    open_index,
    present,
    run,
)
from auditor.cli.options import (
    EvalSample,
    EvalSeed,
    EvalSuiteOption,
    GraphTarget,
    LogRefinements,
    LogSince,
    LogSkipped,
    LogStatus,
    QueueCallForm,
    QueueExternal,
    QueueLimit,
    QueueReason,
    RefinementId,
    RefinementStatusFilter,
    RefineModel,
    RefineRunner,
    RowLimit,
)
from auditor.cli.render import (
    render_graph_brief,
    render_graph_eval,
    render_graph_log,
    render_graph_prune,
    render_graph_refine,
    render_graph_refinement,
    render_graph_refinements,
    render_graph_unresolved,
)
from auditor.config import AuditorSettings
from auditor.graph.model import (
    LOG_ROW_LIMIT,
    QUEUE_ROW_LIMIT,
    CallForm,
    UnresolvedReason,
)
from auditor.graph.payloads import (
    LogFilter,
    LogReport,
    LogView,
    QueueReport,
    QueueRowPayload,
    RefinementRowPayload,
    RefinementsReport,
)
from auditor.graph.query import LogQuery
from auditor.graph.refine import drive
from auditor.graph.refine.models import (
    ALL_SUITES,
    EvalSuite,
    PruneOutcome,
    Refinement,
    RefinementStatus,
    RunStatus,
)
from auditor.graph.refine.payloads import (
    BriefPayload,
    EvalPlan,
    EvalReport,
    RefinePayload,
)
from auditor.graph.refine.runner import RefinementJob
from auditor.graph.refine.service import (
    RefinementLedger,
    RefinementRefused,
    RefinementService,
)
from auditor.user_settings import UserSettings


async def _unresolved_rows(
    root: Path,
    *,
    reasons: list[UnresolvedReason] | None,
    call_forms: list[CallForm] | None,
    limit: int,
    external: bool,
) -> QueueReport:
    """Read the queue in drain order. Both filters and the limit are pushed into the query, so
    the limit always counts rows the caller actually sees and a big queue never lands whole."""
    async with await open_index(root) as index:
        rows = await index.graph.unresolved(
            reasons=[r.value for r in reasons] if reasons else None,
            call_forms=[c.value for c in call_forms] if call_forms else None,
            limit=limit,
            external=external,
        )
    return QueueReport(tuple(QueueRowPayload.of(r) for r in rows))


def graph_unresolved(
    target: GraphTarget = Path("."),
    reason: QueueReason = None,
    call_form: QueueCallForm = None,
    limit: QueueLimit = QUEUE_ROW_LIMIT,
    external: QueueExternal = True,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Facts the deterministic resolver could not place, worst-first."""
    root = cli_root(target)
    present(
        run(
            _unresolved_rows(
                root,
                reasons=reason,
                call_forms=call_form,
                limit=limit,
                external=external,
            ),
            "reading queue…",
        ),
        partial(
            render_graph_unresolved,
            filtered=bool(reason or call_form or not external),
        ),
        as_json=json_,
    )


refinements_app = typer.Typer(
    no_args_is_help=True, help="Inspect and steer the recorded graph corrections."
)


async def _refinements(
    root: Path, statuses: list[RefinementStatus] | None, limit: int
) -> RefinementsReport:
    """One page of the recorded corrections, through the reader the MCP tool also calls."""
    async with await open_index(root) as index:
        return await LogQuery(index).refinements(statuses, limit)


async def _move(
    root: Path, act: Callable[[RefinementLedger], Awaitable[Refinement]]
) -> RefinementRowPayload:
    """One hand transition through the ledger, so the CLI and the daemon share the rules.

    The ledger, not the service: a status change needs neither a checkout root nor a run registry
    nor a git guard, and `RefinementService` has no `accept`, `revert` or `pin` at all. The
    anchors are read the way the listing reads them, so one row does not describe itself two ways.
    """
    async with await open_index(root) as index:
        moved = await act(RefinementLedger(index=index))
        anchors = await index.refinements.anchors([moved.refinement_id])
        return RefinementRowPayload.of(moved, anchors.get(moved.refinement_id, ()))


def _transition(
    target: Path,
    json_: bool,
    act: Callable[[RefinementLedger], Awaitable[Refinement]],
) -> None:
    """Run one transition and print the row it produced, or the reason it was refused."""
    root = cli_root(target)
    try:
        payload = run(_move(root, act), "updating…")
    except RefinementRefused as exc:
        fail(str(exc))
    present(payload, render_graph_refinement, as_json=json_)


async def _prune(
    root: Path, settings: AuditorSettings, user: UserSettings
) -> PruneOutcome:
    """The retention sweep at this user's configured windows.

    The one command here that does build a `RefinementService`: `prune()` reads
    `user.observer.skipped_retention_days` and `limits.stranded_run_seconds`. Both settings are
    read at the command edge, where a broken file is one line, and handed in.
    """
    async with await open_index(root) as index:
        return await RefinementService(index, root, settings, user).prune()


@refinements_app.command("list")
def refinements_list(
    target: GraphTarget = Path("."),
    status: RefinementStatusFilter = None,
    limit: RowLimit = LOG_ROW_LIMIT,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """The graph corrections recorded for this checkout, newest first. A page at `--limit` says
    how many rows match in all, so a full page is never mistaken for the whole list."""
    root = cli_root(target)
    present(
        run(_refinements(root, status, limit), "reading refinements…"),
        render_graph_refinements,
        as_json=json_,
    )


@refinements_app.command("accept")
def refinements_accept(
    refinement_id: RefinementId,
    target: GraphTarget = Path("."),
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Activate a pending correction. The next `graph build` applies it."""
    _transition(target, json_, lambda ledger: ledger.accept(refinement_id))


@refinements_app.command("revert")
def refinements_revert(
    refinement_id: RefinementId,
    target: GraphTarget = Path("."),
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Take a correction back out of the graph. The row stays, with its reason."""
    _transition(target, json_, lambda ledger: ledger.revert(refinement_id))


@refinements_app.command("pin")
def refinements_pin(
    refinement_id: RefinementId,
    target: GraphTarget = Path("."),
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Keep a correction through anchor drift and dead builds; it is never auto-staled."""
    _transition(target, json_, lambda ledger: ledger.pin(refinement_id))


@refinements_app.command("prune")
def refinements_prune(
    target: GraphTarget = Path("."),
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Finish runs a dead process left open, and drop assessment-only runs older than the
    retention window together with the rejected refinements they own. Nothing live is deleted."""
    root = cli_root(target)
    try:
        payload = run(_prune(root, load_settings(root), load_user(root)), "pruning…")
    except RefinementRefused as exc:
        fail(str(exc))
    present(payload, render_graph_prune, as_json=json_)


async def _log(root: Path, spec: LogFilter) -> LogReport:
    """One page of the provenance log, through the reader the `graph_log` MCP tool also calls."""
    async with await open_index(root) as index:
        return await LogQuery(index).page(spec)


def graph_log(
    target: GraphTarget = Path("."),
    refinements: LogRefinements = False,
    status: LogStatus = None,
    since: LogSince = None,
    skipped: LogSkipped = False,
    limit: RowLimit = LOG_ROW_LIMIT,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Who changed the graph, and what they changed. Newest first in both views. Assessment-only
    runs are hidden by default; `--skipped` or `--status skipped` shows them."""
    root = cli_root(target)
    try:
        spec = LogFilter.of(
            view=LogView.REFINEMENTS if refinements else LogView.RUNS,
            status=status,
            since=since,
            skipped=skipped,
            limit=limit,
        )
    except ValueError as exc:
        fail(str(exc))
    present(run(_log(root, spec), "reading log…"), render_graph_log, as_json=json_)


async def _refine(root: Path, job: RefinementJob) -> RefinePayload:
    """One model-driven run, through the same call the `graph_refine` tool makes."""
    settings = load_settings(root)
    user = load_user(root)
    async with await open_index(root) as index:
        return await drive.refine(index, root, settings, user, job=job)


async def _eval(
    root: Path,
    job: RefinementJob,
    *,
    suites: Sequence[EvalSuite],
    size: int,
    seed: int,
    dry_run: bool,
    on_plan: Callable[[EvalPlan], None] | None,
) -> EvalReport:
    """One measured eval over this checkout, through the same selection `refine` uses."""
    settings = load_settings(root)
    user = load_user(root)
    async with await open_index(root) as index:
        return await drive.evaluate(
            index,
            root,
            settings,
            user,
            job=job,
            suites=suites,
            size=size,
            seed=seed,
            dry_run=dry_run,
            on_plan=on_plan,
        )


async def _brief(root: Path, scope: str) -> BriefPayload:
    """The brief a run over this scope would be given, with no run opened."""
    settings = load_settings(root)
    user = load_user(root)
    async with await open_index(root) as index:
        return await drive.brief(index, root, settings, user, scope)


def _job(scope: str, runner: str | None, model: str | None) -> RefinementJob:
    """The job these flags ask for, with a value its `Literal` refuses raised as a bad flag.

    The check lives on `RefinementJob`, which the MCP tool builds too; this only turns pydantic's
    refusal into the exit 2 and the wording a CLI flag earns.
    """
    try:
        return RefinementJob(scope=scope, runner=runner, model=model)
    except ValidationError as exc:
        raise _bad_option(exc) from exc


def _bad_option(exc: ValidationError) -> typer.BadParameter:
    """One refused job field as the flag that carried it, naming the values it accepts.

    The valid set is read off the field's own annotation, so this message and the check that
    produced it cannot drift apart.
    """
    error = exc.errors()[0]
    field = str(error["loc"][0])
    optional = get_args(RefinementJob.model_fields[field].annotation)
    allowed = ", ".join(
        str(value)
        for member in optional
        if member is not type(None)
        for value in get_args(member)
    )
    return typer.BadParameter(f"unknown --{field}: {error['input']}. Valid: {allowed}")


def graph_refine(
    scope: str = typer.Argument(
        "", help="Path prefix under the repo; empty means the whole repo."
    ),
    target: GraphTarget = Path("."),
    runner: RefineRunner = None,
    model: RefineModel = None,
    brief: bool = typer.Option(
        False, "--brief", help="Render the brief for the scope and stop; opens no run."
    ),
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Let a model work the unresolved queue under a path. Edge corrections land pending for a
    human to accept; `annotate_node` and `unresolvable` go active at once. Exits 1 when no runner
    can run or the run did not succeed, 2 on a bad option."""
    root = cli_root(target)
    job = _job(scope, runner, model)
    if brief:
        if runner is not None or model is not None:
            raise typer.BadParameter(
                "--brief opens no run, so --runner and --model would do nothing"
            )
        try:
            payload = run(_brief(root, scope), "reading queue…")
        except (RefinementRefused, ValueError) as exc:
            fail(str(exc))
        present(payload, render_graph_brief, as_json=json_)
        return
    try:
        report = run(_refine(root, job), "refining…")
    except drive.REFINE_ERRORS as exc:
        fail(str(exc))
    present(report, render_graph_refine, as_json=json_)
    if report.run.status is not RunStatus.SUCCEEDED:
        raise typer.Exit(1)


def graph_eval(
    target: GraphTarget = Path("."),
    runner: RefineRunner = None,
    model: RefineModel = None,
    sample: EvalSample = 80,
    seed: EvalSeed = 1,
    suite: EvalSuiteOption = "all",
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the plan and its ceilings, open no run."
    ),
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Measure what this runner and model get right on this repo, and store the numbers the
    activation gate reads. Every run costs money: the plan and its ceilings print before the first
    run opens, and `--dry-run` stops there.
    Exits 1 when no runner can run or a run did not close, 2 on a bad option."""
    root = cli_root(target)
    job = _job("", runner, model)
    suites = _suites(suite)
    try:
        report = run(
            _eval(
                root,
                job,
                suites=suites,
                size=sample,
                seed=seed,
                dry_run=dry_run,
                on_plan=None if json_ or dry_run else _print_plan,
            ),
            "evaluating…",
        )
    except drive.REFINE_ERRORS as exc:
        fail(str(exc))
    present(report, render_graph_eval, as_json=json_)
    if not dry_run and report.runs < report.plan.runs_planned:
        raise typer.Exit(1)


def _print_plan(plan: EvalPlan) -> None:
    """What this eval may spend, on stderr, while there is still time to stop it.

    `--json` and `--dry-run` both leave this out: their one document already is the plan.
    """
    err_console.print(
        f"[dim]{plan.runs_planned} runs planned, up to "
        f"${plan.runs_planned * plan.max_budget_usd_per_run:.2f} against the "
        f"${plan.max_budget_usd_per_eval:.2f} eval ceiling[/dim]",
        highlight=False,
    )


def _suites(requested: str) -> tuple[EvalSuite, ...]:
    """The suites one `--suite` value asks for, with the follow-up named when it is asked for."""
    if requested == "all":
        return ALL_SUITES
    if requested == EvalSuite.FIXTURES.value:
        raise typer.BadParameter(
            "the fixtures suite is a follow-up: it needs tests/fixtures/graph_eval/ "
            "with expected answers, which is its own slice"
        )
    if requested not in {suite.value for suite in ALL_SUITES}:
        allowed = ", ".join(suite.value for suite in ALL_SUITES)
        raise typer.BadParameter(f"unknown --suite: {requested}. Valid: {allowed}, all")
    return (EvalSuite(requested),)


def register(app: typer.Typer) -> None:
    """Mount this module's commands onto the ``graph`` sub-app."""
    app.command("unresolved")(graph_unresolved)
    app.command("refine")(graph_refine)
    app.command("eval")(graph_eval)
    app.command("log")(graph_log)
    app.add_typer(refinements_app, name="refinements")
