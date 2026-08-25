import inspect
import io
import json
from pathlib import Path
from typing import Any

import pytest
from _support import cli_json, invoke
from rich.console import Console
from typer.testing import CliRunner

from auditor.cli import app
from auditor.cli.graph import _split_kinds, graph_flow
from auditor.cli.render import render_graph_flow
from auditor.graph.flow import FlowNode, FlowPayload
from auditor.graph.model import DEFAULT_FLOW_LIMIT, Provenance
from auditor.graph.refine.lock import RebuildLockTimeout, rebuild_lock
from auditor.paths import partition_for

runner = CliRunner()


def test_cli_scan_build_related(graph_repo: Path):
    assert runner.invoke(app, ["scan", str(graph_repo), "-i"]).exit_code == 0
    built = runner.invoke(app, ["graph", "build", str(graph_repo)])
    assert built.exit_code == 0 and json.loads(built.stdout)["nodes"] >= 2
    rel = runner.invoke(app, ["graph", "related", "get_user", str(graph_repo)])
    assert rel.exit_code == 0 and "fetch_user" in rel.stdout


def _built(repo: Path) -> None:
    assert runner.invoke(app, ["scan", str(repo), "-i"]).exit_code == 0
    assert runner.invoke(app, ["graph", "build", str(repo)]).exit_code == 0


def test_cli_graph_flow_walks_the_call_chain(graph_repo_flow: Path):
    _built(graph_repo_flow)
    result = runner.invoke(
        app, ["graph", "flow", "entry", str(graph_repo_flow), "--depth", "2"]
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["resolved"] == "m.py::entry"
    assert payload["direction"] == "out"
    assert payload["modules"] == ["m.py"]
    middle = payload["root"]["children"][0]
    assert middle["id"] == "m.py::middle" and middle["edge"] == "calls"
    assert middle["children"][0]["id"] == "m.py::leaf"


def test_cli_graph_flow_in_reverses_direction(graph_repo_flow: Path):
    _built(graph_repo_flow)
    result = runner.invoke(
        app, ["graph", "flow", "leaf", str(graph_repo_flow), "--in", "--depth", "1"]
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["direction"] == "in"
    assert [c["id"] for c in payload["root"]["children"]] == ["m.py::middle"]


def test_cli_graph_flow_unknown_symbol_is_empty(graph_repo_flow: Path):
    _built(graph_repo_flow)
    result = runner.invoke(app, ["graph", "flow", "nope", str(graph_repo_flow)])
    assert result.exit_code == 0 and json.loads(result.stdout) == {}


def _flow(repo: Path, *flags: str) -> dict:
    result = runner.invoke(app, ["graph", "flow", "entry", str(repo), *flags])
    assert result.exit_code == 0, result.stdout
    return json.loads(result.stdout)


def _hub_leaf(payload: dict) -> dict:
    """``svc.py::leaf`` as reached under ``middle``, the first parent the walk expands."""
    middle = next(c for c in payload["root"]["children"] if c["id"] == "m.py::middle")
    return middle["children"][0]


def _rendered(payload: dict[str, Any]) -> str:
    buf = io.StringIO()
    render_graph_flow(Console(file=buf, width=140), FlowPayload.model_validate(payload))
    return buf.getvalue()


def test_cli_graph_flow_marks_only_the_hub_the_walk_collapsed(
    graph_repo_flow_hub: Path,
):
    """`elided` used to mean "no children", so a root hub, a depth-boundary hub and a stopped
    node all read as elided and --expand-hubs looked broken."""
    _built(graph_repo_flow_hub)
    collapsed = _flow(graph_repo_flow_hub, "--depth", "3")
    expanded = _flow(graph_repo_flow_hub, "--depth", "3", "--expand-hubs")
    boundary = _flow(graph_repo_flow_hub, "--depth", "2")
    stopped = _flow(graph_repo_flow_hub, "--depth", "3", "--stop-at", "svc.py")

    assert collapsed["root"]["hub"] == {
        "count": 2,
        "kind": "expansion",
        "collapsed": False,
    }
    assert _hub_leaf(collapsed)["hub"]["collapsed"] is True
    assert _hub_leaf(expanded)["hub"]["collapsed"] is False
    assert _hub_leaf(boundary)["hub"]["collapsed"] is False
    assert _hub_leaf(stopped)["stopped"] is True
    assert _hub_leaf(stopped)["hub"]["collapsed"] is False

    assert "⊕ 2 elided" in _rendered(collapsed)
    for payload in (expanded, boundary, stopped):
        assert "⊕ 2 hub" in _rendered(payload)
        assert "elided" not in _rendered(payload)
    assert "⊣ stop" in _rendered(stopped)


def test_the_render_fixture_covers_every_flow_node_field():
    """_flow_dict() is a hand-written mirror; a new field must reach the render tests."""
    assert set(_flow_dict()["root"]) == set(FlowNode.model_fields)


def test_the_flow_node_cap_has_one_home():
    """The constant, the CLI signature and the MCP ceiling were three copies of one policy."""
    assert (
        inspect.signature(graph_flow).parameters["limit"].default == DEFAULT_FLOW_LIMIT
    )


@pytest.mark.parametrize(
    "raw, want",
    [(None, []), ("", []), (" calls , inherits ,,", ["calls", "inherits"])],
)
def test_split_kinds_trims_and_drops_empties(raw: str | None, want: list[str]):
    assert _split_kinds(raw) == want


@pytest.mark.parametrize("bad", ["inherit", "NOT_A_KIND", "calls,references_typ"])
def test_cli_graph_flow_rejects_an_unknown_kind(graph_repo_flow: Path, bad: str):
    """An unfollowed kind reads as 'no such edges', which is the wrong answer to a typo."""
    _built(graph_repo_flow)
    result = runner.invoke(
        app, ["graph", "flow", "entry", str(graph_repo_flow), "--kinds", bad]
    )
    assert result.exit_code != 0
    assert "unknown --kinds" in result.output


@pytest.mark.parametrize(
    "flag, value", [("--depth", "5000"), ("--limit", "100000"), ("--depth", "-1")]
)
def test_cli_graph_flow_bounds_its_traversal_flags(
    graph_repo_flow: Path, flag: str, value: str
):
    """Four recursive walks over the tree; an unbounded depth is a traceback, not an error."""
    _built(graph_repo_flow)
    result = runner.invoke(
        app, ["graph", "flow", "entry", str(graph_repo_flow), flag, value]
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    "flags, check",
    [
        (
            ("--depth", "3"),
            lambda p: (
                [c["id"] for c in p["root"]["children"]]
                == ["m.py::middle", "m.py::other"]
            ),
        ),
        (("--depth", "1"), lambda p: _no_ids(p, "svc.py::leaf")),
        (("--depth", "3", "--limit", "2"), lambda p: p["truncated"] is True),
        (
            ("--depth", "3", "--stop-at", "svc.py"),
            lambda p: _hub_leaf(p)["stopped"] is True,
        ),
        (
            ("--depth", "3", "--expand-hubs"),
            lambda p: _hub_leaf(p)["hub"]["collapsed"] is False,
        ),
        (("--in", "--depth", "1"), lambda p: p["root"]["children"] == []),
        (
            ("--in", "--depth", "1", "--include-tests"),
            lambda p: (
                [c["id"] for c in p["root"]["children"]]
                == ["tests/test_entry.py::test_entry"]
            ),
        ),
        (
            ("--depth", "1", "--kinds", "imports"),
            lambda p: (
                [c["id"] for c in p["root"]["children"]]
                == ["m.py::middle", "m.py::other"]
            ),
        ),
    ],
)
def test_cli_graph_flow_flag_matrix(graph_repo_flow_hub: Path, flags, check):
    """Every flow flag reaches FlowOptions; a transposed keyword used to ship green."""
    _built(graph_repo_flow_hub)
    assert check(_flow(graph_repo_flow_hub, *flags))


def _no_ids(payload: dict, missing: str) -> bool:
    return all(c["id"] != missing for c in payload["root"]["children"])


def test_cli_graph_flow_reads_the_hub_floor_from_repo_config(graph_repo_flow_hub: Path):
    """`flow_hub_fan_in = 2` in the fixture's pyproject is what makes `leaf` a hub at all."""
    _built(graph_repo_flow_hub)
    assert _hub_leaf(_flow(graph_repo_flow_hub, "--depth", "3"))["hub"]["count"] == 2


def _flow_dict() -> dict[str, Any]:
    """The hand-written mirror of one flow response, as raw JSON keys.

    Kept as a dict so `test_the_render_fixture_covers_every_flow_node_field` still compares real
    hand-written keys against `FlowNode.model_fields`; comparing a validated model's dump would
    be tautological.
    """
    return {
        "symbol": "main",
        "resolved": "app/cli.py::main",
        "ambiguous": ["app/other.py::main"],
        "direction": "out",
        "modules": ["app/cli.py", "app/engine.py"],
        "truncated": True,
        "limit": 200,
        "root": {
            "id": "app/cli.py::main",
            "kind": "function",
            "edge": None,
            "source": "deterministic",
            "depth": 0,
            "seen_ref": False,
            "cycle": False,
            "stopped": False,
            "hub": None,
            "unresolved": [],
            "children": [
                {
                    "id": "app/engine.py::run",
                    "kind": "function",
                    "edge": "calls",
                    "source": "deterministic",
                    "depth": 1,
                    "seen_ref": False,
                    "cycle": False,
                    "stopped": False,
                    "hub": {"count": 41, "kind": "fan_in", "collapsed": True},
                    "children": [],
                    "unresolved": [
                        {
                            "name": "dispatch",
                            "fact_kind": "attr_callee",
                            "reason": "unimportable_name",
                            "external": False,
                        },
                        {
                            "name": "search",
                            "fact_kind": "attr_callee",
                            "reason": "unimportable_name",
                            "external": True,
                        },
                    ],
                },
                {
                    "id": "app/cb.py::on_done",
                    "kind": "function",
                    "edge": "callback_arg",
                    "source": "deterministic",
                    "depth": 1,
                    "seen_ref": True,
                    "cycle": False,
                    "stopped": False,
                    "hub": None,
                    "children": [],
                    "unresolved": [],
                },
                {
                    "id": "app/impl.py::Alpha.handle",
                    "kind": "method",
                    "edge": "dispatches_to",
                    "source": "deterministic",
                    "depth": 1,
                    "seen_ref": False,
                    "cycle": True,
                    "stopped": False,
                    "hub": None,
                    "children": [],
                    "unresolved": [],
                },
                {
                    "id": "app/db.py::query",
                    "kind": "function",
                    "edge": "calls",
                    "source": "deterministic",
                    "depth": 1,
                    "seen_ref": False,
                    "cycle": False,
                    "stopped": True,
                    "hub": None,
                    "children": [],
                    "unresolved": [],
                },
            ],
        },
    }


def _flow_payload(**over: Any) -> FlowPayload:
    """`_flow_dict()` validated, with top-level overrides applied before validation."""
    return FlowPayload.model_validate(_flow_dict() | over)


def test_render_graph_flow_shows_the_direction_modules_glyphs_and_truncation():
    """Plain Console, no force_terminal: ANSI codes would split "→ run" into three pieces."""
    buf = io.StringIO()
    render_graph_flow(Console(file=buf, width=140), _flow_payload())
    text = buf.getvalue()
    assert "app/cli.py::main flow (out)" in text
    assert "modules  app/cli.py · app/engine.py" in text
    assert "→ run app/engine.py  ⊕ 41 elided ? dispatch ? search" in text
    assert "⇢ on_done app/cb.py  ↺ seen" in text
    assert "↳ Alpha.handle app/impl.py  ↺ cycle" in text
    assert "→ query app/db.py  ⊣ stop" in text
    assert "truncated at --limit 200" in text
    assert "app/other.py::main" in text


def test_render_graph_flow_says_hub_when_the_walk_did_not_collapse_it():
    """The label reads ``hub.collapsed``, not the child count: a childless hub can be a `hub`."""
    raw = _flow_dict()
    raw["root"]["children"][0]["hub"]["collapsed"] = False
    buf = io.StringIO()
    render_graph_flow(Console(file=buf, width=140), FlowPayload.model_validate(raw))
    assert "⊕ 41 hub" in buf.getvalue() and "⊕ 41 elided" not in buf.getvalue()


def test_render_graph_flow_shows_the_in_direction():
    buf = io.StringIO()
    render_graph_flow(Console(file=buf, width=140), _flow_payload(direction="in"))
    assert "app/cli.py::main flow (in)" in buf.getvalue()


def test_render_graph_flow_styles_at_a_terminal():
    """The styled path is the one a human sees; it must render without raising."""
    buf = io.StringIO()
    render_graph_flow(
        Console(file=buf, force_terminal=True, width=140), _flow_payload()
    )
    text = buf.getvalue()
    assert "\x1b[" in text
    for glyph in ("→", "⇢", "↳", "⊕", "↺", "⊣", "?"):
        assert glyph in text


def test_render_graph_flow_on_an_empty_payload():
    buf = io.StringIO()
    render_graph_flow(Console(file=buf, width=140), None)
    assert "no such symbol" in buf.getvalue()


def test_rebuild_with_no_scan_is_refused(graph_repo: Path):
    """F8: the pair has no meaning and its result is a wiped graph and an emptied queue."""
    result = runner.invoke(
        app, ["graph", "build", str(graph_repo), "--rebuild", "--no-scan"]
    )
    assert result.exit_code != 0
    assert "--no-scan" in result.output


def test_rebuild_holds_the_lock_across_the_clear_and_the_scan(
    graph_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    """F1: another build landing between `--rebuild`'s clear and the rescan would see a partial
    node set and stale every refinement into a file still being re-extracted."""
    seen: list[str] = []

    async def probing_scan(root: Path) -> None:
        try:
            async with rebuild_lock(partition_for(root).identity, timeout=1.0):
                seen.append("acquired")
        except RebuildLockTimeout:
            seen.append("blocked")

    monkeypatch.setattr("auditor.cli.graph._autoscan", probing_scan)
    result = runner.invoke(app, ["graph", "build", str(graph_repo), "--rebuild"])
    assert result.exit_code == 0, result.stdout
    assert seen == ["blocked"]


GRAPH_JSON_KEYS: dict[str, list[str]] = {
    "build": [
        "clusters",
        "edges",
        "expired",
        "findings",
        "nodes",
        "refined",
        "unresolved",
    ],
    "usages": [
        "ambiguous",
        "depends_on",
        "kind",
        "resolved",
        "symbol",
        "total_in",
        "total_out",
        "used_by",
    ],
    "concept": ["cluster_id", "label", "members"],
    "flow": [
        "ambiguous",
        "direction",
        "limit",
        "modules",
        "resolved",
        "root",
        "symbol",
        "truncated",
    ],
}
GRAPH_ROW_KEYS: dict[str, list[str]] = {
    "clusters": ["cluster_id", "label", "label_provenance", "member_count"],
    "search": ["id", "kind", "rank"],
    "neighbors": ["direction", "edge", "hops", "id", "kind"],
    "related": ["id", "kind", "rank", "weight"],
}
QUEUE_ROW_KEYS: list[str] = [
    "call_form",
    "candidates",
    "candidates_count",
    "definers",
    "definers_count",
    "externally_bound",
    "fact_kind",
    "name",
    "node_id",
    "priority",
    "reason",
    "receiver_root",
    "resolution_path",
]


@pytest.mark.parametrize("name", sorted(GRAPH_JSON_KEYS))
def test_graph_object_payload_keys_are_unchanged(graph_repo: Path, name):
    argv = {
        "build": ["graph", "build", str(graph_repo)],
        "usages": ["graph", "usages", "get_user", str(graph_repo)],
        "concept": ["graph", "concept", "user", str(graph_repo)],
        "flow": ["graph", "flow", "get_user", str(graph_repo)],
    }[name]
    if name != "build":
        _built(graph_repo)
    payload = cli_json(invoke(*argv, "--json"))
    assert payload, (
        f"{name} produced an empty payload; pick a symbol the fixture graph holds"
    )
    assert sorted(payload) == GRAPH_JSON_KEYS[name]


@pytest.mark.parametrize("name", sorted(GRAPH_ROW_KEYS))
def test_graph_row_payload_keys_are_unchanged(graph_repo: Path, name):
    _built(graph_repo)
    argv = {
        "clusters": ["graph", "clusters", str(graph_repo)],
        "search": ["graph", "search", "user", str(graph_repo)],
        "neighbors": ["graph", "neighbors", "get_user", str(graph_repo)],
        "related": ["graph", "related", "get_user", str(graph_repo)],
    }[name]
    payload = cli_json(invoke(*argv, "--json"))
    assert payload, f"{name} produced no rows; pick a symbol the fixture graph holds"
    assert sorted(payload[0]) == GRAPH_ROW_KEYS[name]


def test_graph_clusters_reports_the_provenance_as_its_stored_string(graph_repo: Path):
    """The row model is `GraphCluster` now, so its `Provenance` enum has to reach the wire as the
    value the column stores, not as the member name."""
    _built(graph_repo)
    payload = cli_json(invoke("graph", "clusters", str(graph_repo), "--json"))
    assert payload, "the fixture repo produced no clusters"
    assert {r["label_provenance"] for r in payload} == {Provenance.DETERMINISTIC.value}


def test_graph_unresolved_row_keys_are_unchanged(graph_repo: Path):
    """The only payload re-derived from a model rather than passed through, so its 13 keys and its
    derived `priority` both need a pin."""
    (graph_repo / "helper.py").write_text("def handle():\n    return 1\n")
    (graph_repo / "caller.py").write_text("def use():\n    return handle()\n")
    _built(graph_repo)
    payload = cli_json(invoke("graph", "unresolved", str(graph_repo), "--json"))
    assert payload, "the fixture repo produced no unresolved rows"
    assert sorted(payload[0]) == QUEUE_ROW_KEYS
    # `priority` is a stored column that `QueueRowPayload.of` now re-validates through
    # `UnresolvedRow._derive_priority`; it must survive that round trip, not be recomputed away.
    assert isinstance(payload[0]["priority"], int)
