import io
import json
from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner

from auditor.cli import app
from auditor.cli.render import render_graph_flow

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


def _flow_payload() -> dict:
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
            "hub_kind": None,
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
                    "hub": 41,
                    "hub_kind": "fan_in",
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
                    "hub_kind": None,
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
                    "hub_kind": None,
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
                    "hub_kind": None,
                    "children": [],
                    "unresolved": [],
                },
            ],
        },
    }


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


def test_render_graph_flow_says_hub_when_the_node_still_expanded():
    """--expand-hubs keeps the count; only a childless hub reads as elided."""
    payload = _flow_payload()
    payload["root"]["children"][0]["children"] = [
        {
            "id": "app/db.py::save",
            "kind": "function",
            "edge": "calls",
            "source": "deterministic",
            "depth": 2,
            "seen_ref": False,
            "cycle": False,
            "stopped": False,
            "hub": None,
            "hub_kind": None,
            "children": [],
            "unresolved": [],
        }
    ]
    buf = io.StringIO()
    render_graph_flow(Console(file=buf, width=140), payload)
    assert "⊕ 41 hub" in buf.getvalue()


def test_render_graph_flow_shows_the_in_direction():
    payload = _flow_payload() | {"direction": "in"}
    buf = io.StringIO()
    render_graph_flow(Console(file=buf, width=140), payload)
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
    render_graph_flow(Console(file=buf, width=140), {})
    assert "no such symbol" in buf.getvalue()
