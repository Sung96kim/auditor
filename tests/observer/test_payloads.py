"""Spec 21's pinned shapes: the route table, its handlers' payloads, and their committed schemas."""

import json
from pathlib import Path

import pytest

from auditor.graph.refine.models import TuningMetrics, TuningRow
from auditor.observer.budget import BudgetState
from auditor.observer.payloads import ROUTES, BudgetPayload
from auditor.payload import WirePayload

SCHEMAS = Path(__file__).parent / "schemas"

#: spec 12.1's API line, transcribed. The table is compared against it, never derived from it.
SPEC_ROUTES = frozenset(
    {
        ("GET", "/health"),
        ("GET", "/api/status"),
        ("GET", "/api/repos"),
        ("GET", "/api/graph"),
        ("GET", "/api/runs"),
        ("GET", "/api/runs/<id>"),
        ("GET", "/api/refinements"),
        ("GET", "/api/evals"),
        ("GET", "/api/flow"),
        ("POST", "/events"),
        ("POST", "/sessions/attach"),
        ("POST", "/sessions/heartbeat"),
        ("POST", "/sessions/detach"),
        ("POST", "/admin/restart"),
    }
)


def _route_id(route: tuple[str, str]) -> str:
    return f"{route[0]} {route[1]}"


def test_the_route_table_is_exactly_the_spec_s_api_line():
    """A route the page polls and the daemon does not serve is the failure this catches."""
    assert set(ROUTES) == SPEC_ROUTES


@pytest.mark.parametrize("route", sorted(ROUTES), ids=_route_id)
def test_every_route_answers_with_a_frozen_wire_payload(route):
    payload = ROUTES[route].payload
    assert issubclass(payload, WirePayload)
    assert payload.model_config["frozen"] is True


def test_only_the_two_polled_routes_carry_an_etag():
    """Spec 12.1 polls these two at 3 s; an ETag anywhere else is a shape nobody asked for."""
    assert {route for route, spec in ROUTES.items() if spec.etag} == {
        ("GET", "/api/runs"),
        ("GET", "/api/status"),
    }


@pytest.mark.parametrize("route", sorted(ROUTES), ids=_route_id)
def test_the_committed_schema_still_matches_the_model(route):
    """Spec 21's pinned shapes: a field rename is a failing diff, not a silent break of S10."""
    model = ROUTES[route].payload
    committed = json.loads((SCHEMAS / f"{model.__name__}.json").read_text())
    assert committed == model.model_json_schema()


def test_every_route_payload_has_a_committed_schema_and_no_others():
    """A snapshot nobody serves, or a route whose shape was never pinned, both fail here."""
    assert {path.stem for path in SCHEMAS.glob("*.json")} == {
        spec.payload.__name__ for spec in ROUTES.values()
    }


def test_the_budget_meter_carries_the_three_numbers_pydantic_drops():
    """`BudgetState`'s derived numbers are properties, so the page would draw an empty meter."""
    state = BudgetState(
        spent_usd=1.5,
        runs=3,
        max_cost_usd_per_day=2.0,
        max_runs_per_day=40,
        low_budget_fraction=0.25,
    )
    wire = BudgetPayload.of(state).model_dump()
    assert wire["remaining_fraction"] == pytest.approx(0.25)
    assert wire["low"] is False
    assert wire["exhausted"] is False
    assert wire["spent_usd"] == 1.5


def test_a_tuning_row_carries_trial_metrics_not_eval_accuracy():
    """Spec 11's trial metrics are modularity and cohesion, never spec 10.2's precision (S11 DQ4)."""
    row = TuningRow(repo_identity="i", key="graph.knn_k", value_json="9", run_id="r")
    assert isinstance(row.metrics, TuningMetrics)
    assert set(TuningMetrics.model_fields) == {
        "modularity",
        "cohesion_intra",
        "cohesion_inter",
        "label_specificity",
        "clusters",
        "singletons",
        "top_cluster_share",
        "stranded_pins",
        "name_edge_churn",
        "label_churn",
        "measured_at",
        "refused",
        "baseline",
    }
    assert not hasattr(row.metrics, "lower_bound_95")
