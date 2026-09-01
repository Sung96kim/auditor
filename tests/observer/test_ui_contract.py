"""The page's hand-written types against S8b's pinned schemas, so a rename breaks a test not a page.

No codegen and no Node: the UI declares what it reads and this walks the committed snapshots to
prove the daemon still serves it (recon Q3, the middle path).
"""

import json
import re
from pathlib import Path

import pytest

_UI = Path(__file__).resolve().parents[2] / "auditor" / "graph" / "ui" / "src"
TYPES_TS = _UI / "api" / "types.ts"
RUNS_TS = _UI / "panels" / "runs.ts"
RUNNER_MARK_TS = _UI / "panels" / "RunnerMark.tsx"
SCHEMAS = Path(__file__).parent / "schemas"

_RUN_ROW = frozenset(
    {
        "run_id",
        "status",
        "producer",
        "client",
        "runner",
        "trigger_kind",
        "trigger_detail",
        "model",
        "summary",
        "error",
        "session_id",
        "branch",
        "commit_sha",
        "cost_usd",
        "cost_estimated",
        "started_at",
        "finished_at",
    }
)
_REFINEMENT_ROW = frozenset(
    {
        "refinement_id",
        "run_id",
        "kind",
        "tier",
        "status",
        "src",
        "dst",
        "edge_kind",
        "node_id",
        "from_dst",
        "members",
        "payload",
        "reason",
        "confidence",
        "drifted",
    }
)

#: every field `auditor/graph/ui/src/api/types.ts` declares, keyed by the committed schema root,
#: then by `(the definition that has to serve it, the interface the page declares it as)`.
READS: dict[str, dict[tuple[str, str], frozenset[str]]] = {
    "StatusPayload": {
        ("StatusPayload", "Status"): frozenset(
            {
                "home",
                "version",
                "compat",
                "state",
                "started_at",
                "uptime_seconds",
                "idle_seconds",
                "repos",
                "queued_repos",
                "drained_events",
                "evals",
                "vectors",
            }
        ),
        ("RepoPayload", "Repo"): frozenset(
            {
                "repo",
                "identity",
                "repo_dir_key",
                "attached",
                "sessions",
                "queued",
                "state",
                "budget",
                "limits",
            }
        ),
        ("BudgetPayload", "Budget"): frozenset(
            {
                "spent_usd",
                "runs",
                "max_cost_usd_per_day",
                "max_runs_per_day",
                "priced",
                "remaining_fraction",
                "low",
                "exhausted",
            }
        ),
        ("RateLimitPayload", "RateLimit"): frozenset(
            {"max_utilization", "paused", "resumes_at"}
        ),
        ("RunnerEvalPayload", "RunnerEval"): frozenset(
            {"runner", "model", "measured", "proven", "strata"}
        ),
        ("EvalStratumPayload", "EvalStratum"): frozenset(
            {"suite", "stratum", "n", "precision", "lower_bound_95", "proven"}
        ),
        ("VectorStatusPayload", "VectorStatus"): frozenset(
            {"enabled", "model", "ready"}
        ),
    },
    # the roster rides on `/api/status`; the measurements are their own route (P7)
    "EvalsView": {("EvalsView", "EvalsView"): frozenset({"runners"})},
    "RunsView": {
        ("RunsView", "RunsView"): frozenset({"log"}),
        ("LogReport", "LogReport"): frozenset(
            {"runs", "hidden_count", "run_count", "truncated"}
        ),
        ("RunRowPayload", "RunRow"): _RUN_ROW,
    },
    "RefinementsView": {
        ("RefinementsView", "RefinementsView"): frozenset({"refinements"}),
        ("RefinementsReport", "RefinementsReport"): frozenset(
            {"rows", "refinement_count", "truncated"}
        ),
        ("RefinementRowPayload", "RefinementRow"): _REFINEMENT_ROW,
        # only the label: it is the one field a `relabel_cluster` row names nothing without
        ("RefinementPayload", "RefinementPayload"): frozenset({"label"}),
    },
    "RunDetailView": {
        ("RunDetailView", "RunDetailView"): frozenset(
            {"run", "prompt", "tool_trace", "refinements", "trials", "assessment"}
        ),
        ("ToolCall", "ToolCall"): frozenset({"tool", "ts", "detail"}),
        ("TuningRow", "TuningRow"): frozenset(
            {"tuning_id", "key", "status", "created_at"}
        ),
        # `Assessment`, not `AssessmentPayload`: both carry a `verdict`, and they are not the
        # same model. `RunDetailView.assessment` is the one the run's own row holds.
        ("Assessment", "Assessment"): frozenset({"verdict"}),
        ("Decision", "Decision"): frozenset({"decision", "reason"}),
    },
    "FlowView": {
        ("FlowView", "FlowView"): frozenset({"symbol", "flow"}),
        ("FlowPayload", "FlowPayload"): frozenset({"root", "direction", "truncated"}),
        ("FlowNode", "FlowNode"): frozenset(
            {
                "id",
                "kind",
                "edge",
                "source",
                "depth",
                "seen_ref",
                "cycle",
                "stopped",
                "hub",
                "unresolved",
                "children",
            }
        ),
        ("HubMark", "HubMark"): frozenset({"count", "kind", "collapsed"}),
        ("UnresolvedLeaf", "UnresolvedLeaf"): frozenset(
            {"name", "fact_kind", "reason", "external"}
        ),
    },
}

#: every status the refinement list names a group for, which is every one the wire serves
_REFINEMENT_STATUSES = frozenset(
    {
        "pending",
        "active",
        "stale",
        "redundant",
        "reverted",
        "pinned",
        "superseded",
        "rejected",
    }
)

#: every enum value the page branches on, by the definition that owns the vocabulary. A field
#: map cannot see a filter on a status the wire never serves, and one shipped as `accepted`.
BRANCHES: dict[str, dict[str, frozenset[str]]] = {
    "RunsView": {"RunStatus": frozenset({"skipped"})},
    "RunDetailView": {
        "RefinementStatus": frozenset({"active", "pinned", "rejected", "reverted"})
    },
    # the refinement list draws a heading per status, which is a filter on all eight
    "RefinementsView": {"RefinementStatus": _REFINEMENT_STATUSES},
    "FlowView": {"FlowDirection": frozenset({"out", "in"})},
}

_INTERFACE = re.compile(r"^export interface (\w+) \{\n(.*?)^\}", re.M | re.S)
_FIELD = re.compile(r"^  (\w+)(\??): (.+?);$", re.M)


def _declared() -> dict[str, dict[str, str]]:
    """`auditor/graph/ui/src/api/types.ts` as `{interface: {field: its declared type}}`.

    Parsed per interface rather than searched as one blob: a whole-file substring says `model:`
    is declared without saying which of the three shapes that share the name declares it.
    """
    source = TYPES_TS.read_text()
    return {
        found.group(1): {
            field.group(1): field.group(3) for field in _FIELD.finditer(found.group(2))
        }
        for found in _INTERFACE.finditer(source)
    }


def _definitions(model: str) -> dict[str, dict]:
    """One committed schema flattened to `{definition name: its properties}`."""
    schema = json.loads((SCHEMAS / f"{model}.json").read_text())
    found = {schema["title"]: schema.get("properties", {})}
    for name, body in schema.get("$defs", {}).items():
        found[name] = body.get("properties", {})
    return found


def _nullable(served: dict) -> bool:
    """Whether the wire can send null for this property, which is what `.slice` on it needs."""
    return any(arm.get("type") == "null" for arm in served.get("anyOf", []))


def _cases() -> list[tuple[str, str, str, str]]:
    return [
        (root, holder, interface, field)
        for root, holders in READS.items()
        for (holder, interface), fields in holders.items()
        for field in sorted(fields)
    ]


def _shape_cases() -> list[tuple[str, str, str]]:
    return [
        (root, holder, interface)
        for root, holders in READS.items()
        for holder, interface in holders
    ]


def _branch_cases() -> list[tuple[str, str, str]]:
    return [
        (root, holder, value)
        for root, holders in BRANCHES.items()
        for holder, values in holders.items()
        for value in sorted(values)
    ]


def test_the_map_this_file_walks_is_not_empty():
    """Emptying `READS` turned three parametrized tests into skips and the suite stayed green.

    A skip is not a pass: the page's whole wire contract could be deleted with nothing to say so.
    """
    assert len(_cases()) > 100
    assert len(_shape_cases()) == len(_declared())
    assert len(_branch_cases()) > 10


@pytest.mark.parametrize(
    ("root", "holder", "interface", "field"),
    _cases(),
    ids=lambda v: str(v).replace(".", "_"),
)
def test_every_field_the_page_reads_is_on_the_pinned_shape(
    root: str, holder: str, interface: str, field: str
):
    """A server-side rename is a failing test here, not a blank panel nobody noticed."""
    definitions = _definitions(root)
    assert holder in definitions, f"{holder} is not in {root}.json"
    assert field in definitions[holder], f"{root}.{holder} no longer serves {field}"


@pytest.mark.parametrize(
    ("root", "holder", "interface", "field"),
    _cases(),
    ids=lambda v: str(v).replace(".", "_"),
)
def test_every_field_agrees_with_the_wire_about_null(
    root: str, holder: str, interface: str, field: str
):
    """Membership alone let four columns ship declared `string` while the wire serves `null`.

    A run produced outside a checkout carries no branch and no commit, and `.slice` on one threw
    inside the render, which the root error boundary answered by replacing the whole page.
    """
    served = _nullable(_definitions(root)[holder][field])
    declared = "null" in _declared()[interface][field]
    assert served == declared, (
        f"{root}.{holder}.{field} is {'nullable' if served else 'not nullable'} on the wire, "
        f"and types.ts declares {_declared()[interface][field]!r}"
    )


@pytest.mark.parametrize(("root", "holder", "interface"), _shape_cases(), ids=str)
def test_the_read_set_is_exactly_what_the_page_declares(
    root: str, holder: str, interface: str
):
    """Both directions, per interface: the map is the page's own declaration or it is fiction.

    Scoped to the interface rather than to the file: `model:` appears in three of them, so a
    whole-file substring check kept passing after the field was deleted from the one that matters.
    """
    assert interface in _declared(), f"types.ts declares no {interface}"
    assert _declared()[interface].keys() == READS[root][(holder, interface)]


@pytest.mark.parametrize(("root", "holder", "value"), _branch_cases(), ids=str)
def test_every_status_the_page_branches_on_is_in_the_pinned_enum(
    root: str, holder: str, value: str
):
    """`accepted()` filtered a status no payload can produce, and a field map cannot see that."""
    schema = json.loads((SCHEMAS / f"{root}.json").read_text())
    served = schema["$defs"][holder]["enum"]
    assert value in served, f"{root}.{holder} has no {value!r}; it serves {served}"


def test_the_pages_status_map_is_exactly_the_enum_the_wire_serves():
    """`STATUS_GROUPS` is the page's one list of refinement statuses, so it is read, not restated.

    A ninth member added to the map is a heading for a status no payload can carry; a member
    dropped from it is a group of rows the run detail silently files under `other`.
    """
    served = set(
        json.loads((SCHEMAS / "RunDetailView.json").read_text())["$defs"][
            "RefinementStatus"
        ]["enum"]
    )
    block = RUNS_TS.read_text().split("export const STATUS_GROUPS", 1)[1].split("};", 1)
    declared = set(re.findall(r"^  (\w+): ", block[0], re.M))
    assert declared == served
    assert declared == set(_REFINEMENT_STATUSES)


def test_the_runner_marks_are_exactly_the_enum_the_wire_serves():
    """`MARKS` and `UNMARKED` are the page's only two words for a runner; together they must be
    exactly `RunnerKind`, or a runner value falls through to "unknown runner X" unnoticed.
    """
    served = set(
        json.loads((SCHEMAS / "RunDetailView.json").read_text())["$defs"]["RunnerKind"][
            "enum"
        ]
    )
    text = RUNNER_MARK_TS.read_text()
    marks_block = text.split("export const MARKS", 1)[1].split("\n};\n", 1)[0]
    marks = set(re.findall(r"^  (\w+): \{", marks_block, re.M))
    unmarked_block = text.split("const UNMARKED", 1)[1].split(";", 1)[0]
    unmarked = set(re.findall(r"(\w+):\s*\"", unmarked_block))
    assert marks | unmarked == served


@pytest.mark.parametrize("root", sorted(set(READS) | set(BRANCHES)))
def test_the_page_never_reads_a_shape_no_committed_schema_declares(root: str):
    """A model that lost its snapshot fails here rather than being skipped over in silence."""
    assert (SCHEMAS / f"{root}.json").is_file()
