"""The page's hand-written types against S8b's pinned schemas, so a rename breaks a test not a page.

No codegen and no Node: the UI declares what it reads and this walks the committed snapshots to
prove the daemon still serves it (recon Q3, the middle path).
"""

import json
from pathlib import Path

import pytest

TYPES_TS = (
    Path(__file__).resolve().parents[2]
    / "auditor"
    / "graph"
    / "ui"
    / "src"
    / "api"
    / "types.ts"
)
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

#: every field `auditor/graph/ui/src/api/types.ts` declares, by the model that has to serve it.
#: A `$def` shared by two roots is listed once, under the root whose panel reads it.
READS: dict[str, dict[str, frozenset[str]]] = {
    "StatusPayload": {
        "StatusPayload": frozenset(
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
        "RepoPayload": frozenset(
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
        "BudgetPayload": frozenset(
            {
                "spent_usd",
                "runs",
                "max_cost_usd_per_day",
                "max_runs_per_day",
                "remaining_fraction",
                "low",
                "exhausted",
            }
        ),
        "RateLimitPayload": frozenset({"max_utilization", "paused", "resumes_at"}),
        "RunnerEvalPayload": frozenset(
            {"runner", "model", "measured", "proven", "strata"}
        ),
        "EvalStratumPayload": frozenset(
            {"suite", "stratum", "n", "precision", "lower_bound_95", "proven"}
        ),
        "VectorStatusPayload": frozenset({"enabled", "model", "ready"}),
    },
    "RunsView": {"RunRowPayload": _RUN_ROW},
    "RefinementsView": {
        "RefinementRowPayload": frozenset(
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
                "reason",
                "confidence",
                "drifted",
            }
        ),
    },
    "RunDetailView": {
        "RunDetailView": frozenset(
            {"run", "prompt", "tool_trace", "refinements", "trials", "assessment"}
        ),
        "ToolCall": frozenset({"tool", "ts", "detail"}),
        "TuningRow": frozenset({"tuning_id", "key", "status", "created_at"}),
        "AssessmentPayload": frozenset({"verdict"}),
        "Decision": frozenset({"decision", "reason"}),
    },
    "FlowView": {
        "FlowView": frozenset({"symbol", "flow"}),
        "FlowPayload": frozenset({"root", "direction", "truncated"}),
        "FlowNode": frozenset(
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
        "HubMark": frozenset({"count", "kind", "collapsed"}),
        "UnresolvedLeaf": frozenset({"name", "fact_kind", "reason", "external"}),
    },
}

#: every enum value the page branches on, by the definition that owns the vocabulary. A field
#: map cannot see a filter on a status the wire never serves, and one shipped as `accepted`.
BRANCHES: dict[str, dict[str, frozenset[str]]] = {
    "RunsView": {"RunStatus": frozenset({"skipped"})},
    "RunDetailView": {
        "RefinementStatus": frozenset({"active", "pinned", "rejected", "reverted"})
    },
    "FlowView": {"FlowDirection": frozenset({"out", "in"})},
}


def _definitions(model: str) -> dict[str, dict]:
    """One committed schema flattened to `{definition name: its properties}`."""
    schema = json.loads((SCHEMAS / f"{model}.json").read_text())
    found = {schema["title"]: schema.get("properties", {})}
    for name, body in schema.get("$defs", {}).items():
        found[name] = body.get("properties", {})
    return found


def _cases() -> list[tuple[str, str, str]]:
    return [
        (root, holder, field)
        for root, holders in READS.items()
        for holder, fields in holders.items()
        for field in sorted(fields)
    ]


def _branch_cases() -> list[tuple[str, str, str]]:
    return [
        (root, holder, value)
        for root, holders in BRANCHES.items()
        for holder, values in holders.items()
        for value in sorted(values)
    ]


@pytest.mark.parametrize(
    ("root", "holder", "field"), _cases(), ids=lambda v: str(v).replace(".", "_")
)
def test_every_field_the_page_reads_is_on_the_pinned_shape(
    root: str, holder: str, field: str
):
    """A server-side rename is a failing test here, not a blank panel nobody noticed."""
    definitions = _definitions(root)
    assert holder in definitions, f"{holder} is not in {root}.json"
    assert field in definitions[holder], f"{root}.{holder} no longer serves {field}"


@pytest.mark.parametrize(
    ("root", "holder", "value"), _branch_cases(), ids=lambda v: str(v)
)
def test_every_status_the_page_branches_on_is_in_the_pinned_enum(
    root: str, holder: str, value: str
):
    """`accepted()` filtered a status no payload can produce, and a field map cannot see that."""
    schema = json.loads((SCHEMAS / f"{root}.json").read_text())
    served = schema["$defs"][holder]["enum"]
    assert value in served, f"{root}.{holder} has no {value!r}; it serves {served}"


@pytest.mark.parametrize(
    ("root", "holder"), sorted({(r, h) for r, h, _ in _cases()}), ids=lambda v: str(v)
)
def test_the_read_set_is_what_the_page_actually_declares(root: str, holder: str):
    """A name in the map that no longer appears in `types.ts` makes this a check of nothing."""
    source = TYPES_TS.read_text()
    for field in READS[root][holder]:
        assert f"{field}:" in source, f"{field} is in the map but not in types.ts"


def test_the_page_never_reads_a_field_no_committed_schema_declares():
    """The whole map is walked, so a model that lost a snapshot fails rather than being skipped."""
    for root in set(READS) | set(BRANCHES):
        assert (SCHEMAS / f"{root}.json").is_file()
