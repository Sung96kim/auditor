"""Pretty-print render functions for CLI commands.

Each function takes a ``Console`` and the command's payload and renders a human-friendly
rich table or panel.  They are independently callable so tests can exercise the pretty
path directly without a TTY.

Accent colour ``#7C7CFF`` matches the rest of the auditor CLI (see self_update.py).
"""

from collections.abc import Callable
from datetime import datetime

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from auditor.cli.payloads import (
    ConfigCheckReport,
    CrossfileReport,
    DiscoverReport,
    IgnoreAddReport,
    IgnoreClearReport,
    IgnoreListReport,
    IgnoreRmReport,
    IndexAddReport,
    IndexForgetReport,
    IndexListReport,
    IndexReposReport,
    InitReport,
    ManifestReport,
    PluginsReport,
    RulesListReport,
)
from auditor.config import AuditorSettings
from auditor.graph.flow import FlowNode, FlowPayload
from auditor.graph.model import LOG_NOTE_FILES, MAX_LOG_ROWS
from auditor.graph.payloads import (
    ClustersReport,
    ConceptPayload,
    GraphBuildReport,
    LogReport,
    LogView,
    NeighborsReport,
    QueueReport,
    RefinementRowPayload,
    RefinementsReport,
    RelatedReport,
    RunRowPayload,
    SearchReport,
    UsagesPayload,
)
from auditor.graph.refine.models import (
    PruneOutcome,
    RefinementStatus,
    RunStatus,
    SuiteTally,
    Verdict,
    flawless_floor,
    key_of,
    wilson_lower,
)
from auditor.graph.refine.payloads import BriefPayload, EvalReport, RefinePayload
from auditor.observer.payloads import DaemonStatus
from auditor.user_settings import UserSettings

_ACCENT = "#7C7CFF"
_BORDER = "dim"


# ---------------------------------------------------------------------------
# graph
# ---------------------------------------------------------------------------


def render_graph_build(out: Console, payload: GraphBuildReport) -> None:
    t = Table.grid(padding=(0, 3))
    t.add_column(style="bold")
    t.add_column(justify="right", style=_ACCENT)
    for label, value in (
        ("nodes", payload.nodes),
        ("edges", payload.edges),
        ("clusters", payload.clusters),
        ("unresolved", payload.unresolved),
        ("findings", payload.findings),
        ("refined", payload.refined),
        ("expired", payload.expired),
    ):
        t.add_row(label, str(value))
    out.print(Panel(t, title="graph built", border_style=_BORDER))


def render_graph_related(out: Console, payload: RelatedReport) -> None:
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("symbol")
    t.add_column("kind")
    t.add_column("weight", justify="right")
    t.add_column("rank", justify="right")
    for row in payload.root:
        t.add_row(row.id, row.kind, str(row.weight), str(row.rank))
    out.print(t)


def render_graph_neighbors(out: Console, payload: NeighborsReport) -> None:
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("dir")
    t.add_column("edge")
    t.add_column("symbol")
    t.add_column("kind")
    t.add_column("hops", justify="right")
    for row in sorted(payload.root, key=lambda r: (r.direction, r.hops)):
        t.add_row(row.direction, row.edge, row.id, row.kind, str(row.hops))
    out.print(t)


def render_graph_concept(out: Console, payload: ConceptPayload | None) -> None:
    if payload is None:
        out.print("[dim]no such concept[/]")
        return
    out.print(
        f"[bold {_ACCENT}]{payload.label}[/] [dim]({len(payload.members)} members)[/]"
    )
    if payload.members:
        t = Table(border_style=_BORDER, show_header=False)
        t.add_column("symbol")
        for member in payload.members:
            t.add_row(member.id)
        out.print(t)


def render_graph_clusters(out: Console, payload: ClustersReport) -> None:
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("label")
    t.add_column("size", justify="right")
    for row in sorted(payload.root, key=lambda r: r.member_count, reverse=True):
        t.add_row(row.label, str(row.member_count))
    out.print(t)


def render_graph_search(out: Console, payload: SearchReport) -> None:
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("symbol")
    t.add_column("kind")
    t.add_column("rank", justify="right")
    t.add_column("score", justify="right")
    for row in payload.root:
        t.add_row(row.id, row.kind, str(row.rank), str(row.score))
    out.print(t)


def render_graph_usages(out: Console, payload: UsagesPayload | None) -> None:
    if payload is None:
        out.print("[dim]no such symbol[/]")
        return
    out.print(
        f"[bold {_ACCENT}]{payload.resolved}[/] "
        f"[dim]({payload.kind or ''})[/]  "
        f"used_by [bold]{payload.total_in}[/] · "
        f"depends_on [bold]{payload.total_out}[/]"
    )
    if payload.ambiguous:
        out.print("[yellow]ambiguous[/], also matched: " + ", ".join(payload.ambiguous))
    for title, groups in (
        ("USED BY", payload.used_by),
        ("DEPENDS ON", payload.depends_on),
    ):
        if not groups:
            continue
        t = Table(
            title=title,
            title_justify="left",
            border_style=_BORDER,
            show_header=True,
            header_style="bold",
        )
        t.add_column("edge")
        t.add_column("count", justify="right")
        t.add_column("e.g.")
        for edge, info in sorted(groups.items(), key=lambda kv: -kv[1].count):
            sample = ", ".join(sym.split("::")[-1] for sym in info.sample)
            t.add_row(edge, str(info.count), sample)
        out.print(t)


_FLOW_GLYPH = {
    "calls": "→",
    "callback_arg": "⇢",
    "dispatches_to": "↳",
    "registered_in": "↳",
}


def _flow_label(node: FlowNode, *, root: bool = False) -> str:
    glyph = "" if root else f"{_FLOW_GLYPH.get(node.edge or '', '→')} "
    marks = []
    if node.cycle:
        marks.append("[yellow]↺ cycle[/]")
    elif node.seen_ref:
        marks.append("[dim]↺ seen[/]")
    if node.stopped:
        marks.append("[cyan]⊣ stop[/]")
    if node.hub is not None:
        kept = "elided" if node.hub.collapsed else "hub"
        marks.append(f"[magenta]⊕ {node.hub.count} {kept}[/]")
    marks += [
        f"[{'dim' if leaf.external else 'yellow'}]? {leaf.name}[/]"
        for leaf in node.unresolved
    ]
    tail = ("  " + " ".join(marks)) if marks else ""
    return f"{glyph}[bold]{node.id.split('::')[-1]}[/] [dim]{node.id.split('::')[0]}[/]{tail}"


def _flow_branch(parent: Tree, node: FlowNode) -> None:
    for child in node.children:
        _flow_branch(parent.add(_flow_label(child)), child)


def render_graph_flow(out: Console, payload: FlowPayload | None) -> None:
    if payload is None:
        out.print("[dim]no such symbol[/]")
        return
    out.print(
        f"[bold {_ACCENT}]{payload.resolved}[/] "
        f"[dim]flow ({payload.direction.value})[/]"
    )
    if payload.modules:
        out.print(f"[bold {_ACCENT}]modules[/]  " + " · ".join(payload.modules))
    if payload.ambiguous:
        out.print("[yellow]ambiguous[/], also matched: " + ", ".join(payload.ambiguous))
    tree = Tree(_flow_label(payload.root, root=True))
    _flow_branch(tree, payload.root)
    out.print(tree)
    if payload.truncated:
        out.print(f"[yellow]truncated[/] at --limit {payload.limit}")


def render_graph_unresolved(
    out: Console, payload: QueueReport, *, filtered: bool = False
) -> None:
    if not payload.root:
        empty = (
            "(no rows matched the filter)"
            if filtered
            else "(queue is empty; run `auditr graph build` first)"
        )
        out.print(f"[dim]{empty}[/]")
        return
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    for col in ("node", "form", "name", "reason"):
        t.add_column(col)
    t.add_column("definers", justify="right")
    t.add_column("candidates", justify="right")
    t.add_column("ext-bound", justify="center")
    for row in payload.root:
        t.add_row(
            row.node_id,
            row.call_form.value,
            row.display_name,
            row.reason.value,
            str(row.definers_count),
            str(row.candidates_count),
            "yes" if row.externally_bound else "",
        )
    out.print(t)


def _proposed_values(payload: RefinementRowPayload) -> list[tuple[str, str]]:
    """What the proposal carries beyond its target, so a `pending` row shows what accepting it
    would change: the label, the annotation, the candidate, the reason code, the call form.

    Escaped: these are node ids and model-written text, and a square bracket in one would either
    be eaten as a style tag or raise `MarkupError` and take the whole table down.
    """
    return [
        (name, escape(str(value)))
        for name, value in payload.payload.model_dump(mode="json").items()
        if value
    ]


#: shared so `graph refinements list` and `graph log --refinements` cannot show one row two ways;
#: the log prepends its own `when`
_REFINEMENT_COLUMNS = ("id", "kind", "tier", "status", "target")

#: the decisions view, which only the log has
_RUN_COLUMNS = ("when", "producer", "runner", "trigger", "status", "n", "summary")

#: one measured suite stratum, in the order the go/no-go reads them (spec 10.2)
_EVAL_COLUMNS = (
    "suite/stratum",
    "n",
    "correct",
    "wrong",
    "false adds",
    "off target",
    "precision",
    "recall",
    "lower 95",
    "cost",
    "turns",
    "runs",
)


def _headed_table(columns: tuple[str, ...]) -> Table:
    """An empty table carrying these headers, in this file's border and header style."""
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    for column in columns:
        t.add_column(column)
    return t


def _note_row(columns: tuple[str, ...], note: str) -> tuple[str, ...]:
    """A continuation row: every column blank but the last, whatever the header count is."""
    return ("",) * (len(columns) - 1) + (note,)


def _truncated_note(shown: int, total: int) -> str:
    """What a capped page left behind, and whether the cap can be raised.

    At ``MAX_LOG_ROWS`` the CLI refuses a larger ``--limit``, so telling a reader to raise it
    would be advice to a usage error.
    """
    room = (
        "raise --limit for more"
        if shown < MAX_LOG_ROWS
        else f"{MAX_LOG_ROWS} is the cap"
    )
    return f"showing {shown} of {total}, newest first; {room}"


def _refinement_cells(row: RefinementRowPayload) -> tuple[str, ...]:
    """One correction in `_REFINEMENT_COLUMNS` order."""
    return (
        str(row.refinement_id),
        row.kind.value,
        row.tier.value,
        row.status.value,
        row.summary,
    )


def _refinement_note(row: RefinementRowPayload) -> str:
    """The line under a correction: whether its anchors drifted, what the proposal carries and
    why, which is the whole of what a human accepting it has to judge."""
    drift = "[yellow]drifted[/] " if row.drifted else ""
    proposed = " ".join(f"{k}={v}" for k, v in _proposed_values(row))
    note = f"{proposed} {escape(row.reason)}".strip()
    return f"{drift}[dim]{note}[/]"


def render_graph_refinements(out: Console, payload: RefinementsReport) -> None:
    if not payload.rows:
        empty = (
            "(no refinements matched the filter)"
            if payload.filtered
            else "(none recorded; propose one with the graph_refine_* MCP tools)"
        )
        out.print(f"[dim]{empty}[/]")
        return
    t = _headed_table(_REFINEMENT_COLUMNS)
    for row in payload.rows:
        t.add_row(*_refinement_cells(row))
        t.add_row(*_note_row(_REFINEMENT_COLUMNS, _refinement_note(row)))
    out.print(t)
    if payload.truncated:
        out.print(
            f"[dim]{_truncated_note(len(payload.rows), payload.refinement_count)}[/]"
        )


def render_graph_refinement(out: Console, payload: RefinementRowPayload) -> None:
    t = Table.grid(padding=(0, 3))
    t.add_column(style="bold")
    t.add_column(style=_ACCENT)
    for label, value in (
        ("id", str(payload.refinement_id)),
        ("kind", payload.kind.value),
        ("tier", payload.tier.value),
        ("status", payload.status.value),
        ("target", payload.summary),
        *_proposed_values(payload),
        ("reason", payload.reason),
    ):
        t.add_row(label, value)
    out.print(Panel(t, title="refinement", border_style=_BORDER))
    if payload.status is RefinementStatus.ACTIVE:
        out.print("[dim]run `auditr graph build` to apply it[/dim]")


def render_graph_refine(out: Console, payload: RefinePayload) -> None:
    t = Table.grid(padding=(0, 3))
    t.add_column(style="bold")
    t.add_column(style=_ACCENT)
    run = payload.run
    colour = "green" if run.status is RunStatus.SUCCEEDED else "red"
    ids = ", ".join(str(v.refinement_id) for v in payload.committed) or "none"
    for label, value in (
        ("run", run.run_id),
        ("runner", f"{payload.choice.value} {run.model or ''}".strip()),
        ("status", f"[{colour}]{run.status.value}[/]"),
        ("summary", run.error or run.summary or ""),
        ("briefed", f"{payload.targets} of {payload.queue_total} queue rows"),
        ("committed", f"{len(payload.committed)} ({ids})"),
        ("rejected", str(len(payload.rejected))),
        ("cost", f"${run.cost_usd:.4f} over {run.num_turns} turns"),
    ):
        t.add_row(label, value)
    out.print(Panel(t, title="graph refined", border_style=_BORDER))
    if payload.build is not None:
        render_graph_build(out, payload.build)
    _landed_note(out, payload.committed)


def _landed_note(out: Console, landed: tuple[Verdict, ...]) -> None:
    """What a human still has to do about what this run landed, and what it cannot undo by not
    acting: the two go to different kinds, so a run that landed both says both."""
    active = tuple(v for v in landed if v.status is RefinementStatus.ACTIVE)
    pending = tuple(v for v in landed if v.status is RefinementStatus.PENDING)
    if active:
        kinds = ", ".join(sorted({v.kind.value for v in active}))
        out.print(
            f"[dim]{len(active)} active already, applied by the next build: {kinds}[/dim]"
        )
    if pending:
        out.print(
            f"[dim]{len(pending)} pending until "
            "`auditr graph refinements accept <id>`[/dim]"
        )


def render_graph_eval(out: Console, payload: EvalReport) -> None:
    plan = payload.plan
    out.print(f"[dim]{plan.budget_line(payload.model)}[/dim]", highlight=False)
    for line in plan.strata:
        out.print(f"[dim]{line}[/dim]")
    if not payload.suites:
        out.print("[dim](nothing measured)[/dim]")
    else:
        t = _headed_table(_EVAL_COLUMNS)
        for got in payload.suites:
            t.add_row(*_eval_cells(got, proven=payload.activation.proven))
        out.print(t)
    _eval_notes(out, payload)
    out.print(
        f"[dim]{payload.runs} of {plan.runs_planned} runs measured, "
        f"${payload.cost_usd:.4f} spent[/dim]",
        highlight=False,
    )
    _activation_note(out, payload)


def _eval_notes(out: Console, payload: EvalReport) -> None:
    """Every list the report carries, each said as the question it answers."""
    notes = payload.notes
    for line in (*notes.short, *notes.empty, *notes.stopped, *notes.off_target):
        out.print(f"[dim]{line}[/dim]")
    for line in notes.unprovable_drawn:
        out.print(f"[dim]unprovable as drawn, {line}[/dim]")
    for line in notes.unprovable_judged:
        out.print(f"[dim]unprovable as judged, {line}[/dim]")


def _eval_cells(got: SuiteTally, *, proven: tuple[str, ...]) -> tuple[str, ...]:
    """One measured stratum's row; a proven key is marked where the gate reads it."""
    key = key_of(got.suite, got.stratum)
    metrics = got.metrics
    return (
        f"{key} [green]OK[/]" if key in proven else key,
        str(metrics.n),
        str(got.correct),
        str(got.wrong),
        str(got.false_adds),
        str(got.off_target),
        f"{metrics.precision:.3f}",
        f"{metrics.recall:.3f}",
        f"{metrics.lower_bound_95:.3f}",
        f"${got.spend.cost_usd:.4f}",
        str(got.spend.num_turns),
        str(got.spend.runs),
    )


def _activation_note(out: Console, payload: EvalReport) -> None:
    """What this eval made activatable, read off the gate, and what it would take when nothing is.

    Never re-derived from `proven`: tier B needs its add stratum and the collision control, and a
    report that read only the first half would say active where the ledger stores pending.
    """
    activation = payload.activation
    ambiguous = "yes" if activation.resolve_ambiguous else "no"
    tier_b = ", ".join(activation.tier_b) if activation.tier_b else "no stratum"
    out.print(f"[dim]resolve_ambiguous: {ambiguous}; tier B active for {tier_b}[/dim]")
    if not activation.proven:
        _floor_note(out, payload)


def _floor_note(out: Console, payload: EvalReport) -> None:
    """The smallest flawless run that could clear this bar, or that none can."""
    floor = flawless_floor(payload.min_precision)
    if floor is None:
        out.print(
            f"[dim]no run of any size clears {payload.min_precision}[/dim]",
            highlight=False,
        )
        return
    drew = payload.plan.sample
    out.print(
        f"[dim]{floor} flawless trials are the smallest run that clears "
        f"{payload.min_precision} ({drew} give {wilson_lower(drew, drew):.3f})[/dim]",
        highlight=False,
    )


def render_graph_brief(out: Console, payload: BriefPayload) -> None:
    where = f"run {payload.run_id}" if payload.run_id else "no run opened"
    scope = payload.brief.scope or "the whole repo"
    out.print(f"[dim]brief for {scope} ({where})[/dim]")
    out.print(payload.prompt, highlight=False, markup=False)


def render_graph_prune(out: Console, payload: PruneOutcome) -> None:
    out.print(
        f"[{_ACCENT}]{payload.removed_runs}[/] assessment-only runs removed, with "
        f"[{_ACCENT}]{payload.removed_refinements}[/] rejected refinements they owned"
    )
    out.print(
        f"[{_ACCENT}]{payload.stranded_runs}[/] runs left open by a dead process finished"
    )


def _hidden_note(payload: LogReport) -> str:
    """What the default runs view left out, counted and named, with both ways to see it."""
    hidden = ", ".join(s.value for s in payload.hidden_statuses)
    plural = "" if payload.hidden_count == 1 else "s"
    return (
        f"{payload.hidden_count} {hidden} run{plural} hidden; "
        "--skipped or --status skipped shows them"
    )


def _log_empty(payload: LogReport) -> str:
    """Why this page has no rows, in the order the page can prove: the caller's own filter, then
    the view's hiding, then nothing recorded at all."""
    if payload.narrowed_by:
        narrowed = ", ".join(f"--{n.value}" for n in payload.narrowed_by)
        return f"(nothing matched {narrowed})"
    if payload.hidden_count:
        return f"({_hidden_note(payload)})"
    return "(none recorded; the observer and the graph_refine_* tools write here)"


def _stamp(epoch: float) -> str:
    """A log timestamp as local `MM-DD HH:MM`, which is what a reader scanning a log needs."""
    return datetime.fromtimestamp(epoch).strftime("%m-%d %H:%M") if epoch else ""


def _assessment_note(row: RunRowPayload) -> str:
    """Spec 8.6's log line: what the batch looked at, what the gate found, what it did.

    Composed rather than stored: every part is already a column, and a stored sentence would go
    stale the moment the status it names is what a reader should trust. Only a row with no
    assessment has no line: a batch whose paths stage 0 all dropped still has a reason, and the
    reason is the whole payload of the feature.
    """
    detail = row.trigger_detail
    if detail.assessment is None:
        return ""
    named = ", ".join(escape(f) for f in detail.files[:LOG_NOTE_FILES])
    extra = detail.file_count - LOG_NOTE_FILES
    more = f" +{extra} more" if extra > 0 else ""
    looked = f"looked at {named}{more}" if detail.files else "looked at nothing"
    reason = escape(detail.assessment.verdict.reason)
    return f"[dim]{looked}: {reason}, {row.status.value}[/]"


def _runs_table(payload: LogReport) -> Table:
    """The decisions view: one row per run, with ``n`` the refinement rows that run owns."""
    t = _headed_table(_RUN_COLUMNS)
    for row in payload.runs:
        t.add_row(
            _stamp(row.started_at),
            row.producer.value,
            row.runner.value,
            row.trigger_kind.value,
            row.status.value,
            str(row.refinements.total),
            f"[red]{row.error}[/]" if row.error else row.summary or "",
        )
        note = _assessment_note(row)
        if note:
            t.add_row(*_note_row(_RUN_COLUMNS, note))
    return t


def _refinements_table(payload: LogReport) -> Table:
    """The corrections view: what `graph refinements list` shows, plus when each was recorded."""
    columns = ("when", *_REFINEMENT_COLUMNS)
    t = _headed_table(columns)
    for row in payload.refinements:
        t.add_row(_stamp(row.created_at), *_refinement_cells(row))
        t.add_row(*_note_row(columns, _refinement_note(row)))
    return t


#: one table per view, so a third view registers here instead of adding a branch
_LOG_TABLES: dict[LogView, Callable[[LogReport], Table]] = {
    LogView.RUNS: _runs_table,
    LogView.REFINEMENTS: _refinements_table,
}


def render_graph_log(out: Console, payload: LogReport) -> None:
    if not payload.rows:
        out.print(f"[dim]{_log_empty(payload)}[/]")
        return
    out.print(_LOG_TABLES[payload.view](payload))
    if payload.truncated:
        out.print(f"[dim]{_truncated_note(len(payload.rows), payload.total)}[/]")
    if payload.hidden_count:
        out.print(f"[dim]{_hidden_note(payload)}[/]")


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------


def render_rules_list(out: Console, payload: RulesListReport) -> None:
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("rule_id")
    t.add_column("category")
    t.add_column("severity")
    t.add_column("framework")
    t.add_column("refs")
    for row in payload.root:
        t.add_row(
            row.rule_id,
            row.category,
            row.default_severity,
            row.framework or "",
            ", ".join(row.standard_refs),
        )
    out.print(t)


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------


def render_index_add(out: Console, payload: IndexAddReport) -> None:
    out.print(f"[{_ACCENT}]added {len(payload.added)} file(s)[/]")
    for path in payload.added:
        out.print(f"  [dim]{path}[/]")


def render_index_list(out: Console, payload: IndexListReport) -> None:
    if not payload.root:
        out.print("[dim](scope is empty)[/]")
        return
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("file")
    t.add_column("findings", justify="right")
    for entry in payload.root:
        t.add_row(entry.path, str(sum(entry.counts.values())))
    out.print(t)


def render_index_repos(out: Console, payload: IndexReposReport) -> None:
    if not payload.root:
        out.print("[dim](no repos registered)[/]")
        return
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("repo")
    for row in payload.root:
        t.add_row(row.repo)
    out.print(t)


def render_index_forget(out: Console, payload: IndexForgetReport) -> None:
    if payload.removed:
        out.print(f"[{_ACCENT}]removed[/] {payload.repo}")
    else:
        out.print(f"[dim]nothing to remove for {payload.repo}[/]")


# ---------------------------------------------------------------------------
# ignore
# ---------------------------------------------------------------------------


def render_ignore_add(out: Console, payload: IgnoreAddReport) -> None:
    scope = payload.file or "repo-wide"
    out.print(f"[{_ACCENT}]ignore added[/]  [bold]{payload.rule_id}[/]  ({scope})")
    if payload.note:
        out.print(f"[yellow]note:[/] {payload.note}")


def render_ignore_list(out: Console, payload: IgnoreListReport) -> None:
    if not payload.root:
        out.print("[dim](no ignores)[/]")
        return
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("id", justify="right")
    t.add_column("rule_id")
    t.add_column("file")
    t.add_column("line", justify="right")
    t.add_column("reason")
    for row in payload.root:
        t.add_row(
            str(row.id),
            row.rule_id,
            row.file or "",
            str(row.line) if row.line is not None else "",
            row.reason or "",
        )
    out.print(t)


def render_ignore_rm(out: Console, payload: IgnoreRmReport) -> None:
    out.print(f"[{_ACCENT}]removed[/] ignore {payload.selector}")


def render_ignore_clear(out: Console, payload: IgnoreClearReport) -> None:
    out.print(f"[{_ACCENT}]cleared[/] {payload.cleared} ignore(s)")


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


def render_manifest_list(out: Console, payload: ManifestReport) -> None:
    if not payload.root:
        out.print("[dim](no entries)[/]")
        return
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("line", justify="right")
    t.add_column("kind")
    t.add_column("symbol")
    for entry in payload.root:
        t.add_row(str(entry.line), entry.kind.value, entry.symbol)
    out.print(t)


# ---------------------------------------------------------------------------
# plugins
# ---------------------------------------------------------------------------


def render_plugins_list(out: Console, payload: PluginsReport) -> None:
    sections = (
        ("detectors", payload.detectors),
        ("languages", payload.languages),
        ("reporters", payload.reporters),
    )
    for title, items in sections:
        if not items:
            continue
        t = Table(
            border_style=_BORDER, show_header=True, header_style="bold", title=title
        )
        t.add_column("name")
        t.add_column("source")
        for name, info in items.items():
            t.add_row(name, info.source)
        out.print(t)
    for warning in payload.warnings:
        out.print(f"[yellow]warning:[/] {warning}")


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


def render_discover(out: Console, payload: DiscoverReport) -> None:
    if not payload.root:
        out.print("[dim](no files found)[/]")
        return
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("file")
    t.add_column("role")
    for row in payload.root:
        t.add_row(row.file, row.role.value)
    out.print(t)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def render_config_show(out: Console, payload: AuditorSettings | UserSettings) -> None:
    """Either settings model, printed as the same JSON ``--json`` would emit."""
    out.print_json(data=payload.model_dump(mode="json", by_alias=True))


def render_config_check(out: Console, payload: ConfigCheckReport) -> None:
    unknown = [
        (kind, key)
        for kind, keys in (
            ("repo policy", payload.policy_unknown),
            ("user settings", payload.user_unknown),
        )
        for key in keys
    ]
    if not unknown:
        out.print(f"[{_ACCENT}]config ok:[/] no unknown keys ({payload.root})")
        return
    out.print(f"[dim]{payload.root}[/dim]")
    t = Table(border_style=_BORDER, show_header=True, header_style="bold")
    t.add_column("where")
    t.add_column("unknown key")
    for kind, key in unknown:
        t.add_row(kind, key)
    out.print(t)
    out.print("[dim]unknown keys are ignored; remove them or upgrade auditr[/dim]")


# ---------------------------------------------------------------------------
# crossfile
# ---------------------------------------------------------------------------


def render_crossfile(out: Console, payload: CrossfileReport) -> None:
    out.print(f"[{_ACCENT}]cross-file findings:[/] {payload.cross_file_findings}")


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def render_init(out: Console, payload: InitReport) -> None:
    t = Table.grid(padding=(0, 3))
    t.add_column(style="bold")
    t.add_column(style=_ACCENT)
    for label, value in (
        ("home", payload.home),
        ("config", payload.config),
        ("schema", payload.schema_path),
        ("repo", payload.repo_dir),
    ):
        if value:
            t.add_row(label, value)
    if payload.checked:
        state = "not written (--check)"
    else:
        state = (
            ", ".join(payload.written) if payload.written else "nothing (up to date)"
        )
    t.add_row("written", state)
    out.print(Panel(t, title="auditor init", border_style=_BORDER))
    for key in payload.unknown_keys:
        out.print(f"[yellow]unknown key[/yellow] {key}")
    if payload.migrated:
        out.print(
            f"[{_ACCENT}]moved repo:[/] settings were created for "
            f"{payload.moved_from}; the breadcrumb now points here"
        )
    elif payload.moved_from:
        out.print(
            f"[yellow]moved repo:[/yellow] settings were created for {payload.moved_from}; "
            "re-run with --migrate to point them here"
        )
    if payload.legacy_status:
        out.print(
            f"[yellow]leftover status file:[/yellow] {payload.legacy_status}; "
            "remove it with --clean-status"
        )


def render_observer(out: Console, payload: DaemonStatus) -> None:
    """One line for every observer verb: what changed, and where the daemon is."""
    where = (
        f"{payload.home} on port {payload.port}" if payload.running else payload.home
    )
    out.print(f"[{_ACCENT}]{escape(payload.action)}[/] {escape(where)}")
    if payload.page_url:
        out.print(f"[dim]{payload.page_url}[/dim]")
