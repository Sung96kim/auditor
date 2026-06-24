"""Tests for the --json flag and pretty render functions.

JSON path: invoke via CliRunner (non-TTY) → same JSON as before (byte-identical contract).
Pretty path: call render functions directly with a StringIO Console (force_terminal=True).
"""

import io
import json

from rich.console import Console
from typer.testing import CliRunner

from auditor.cli import app
from auditor.cli.render import (
    render_crossfile,
    render_discover,
    render_graph_build,
    render_graph_clusters,
    render_graph_concept,
    render_graph_neighbors,
    render_graph_related,
    render_graph_search,
    render_graph_usages,
    render_ignore_add,
    render_ignore_clear,
    render_ignore_list,
    render_ignore_rm,
    render_index_add,
    render_index_forget,
    render_index_list,
    render_index_repos,
    render_manifest_list,
    render_plugins_list,
    render_rules_list,
)

runner = CliRunner()


def _console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    con = Console(file=buf, force_terminal=True, width=100)
    return con, buf


# ---------------------------------------------------------------------------
# --json flag: CliRunner is non-TTY so these must produce parseable JSON
# regardless of whether --json is passed.
# ---------------------------------------------------------------------------


def test_rules_list_json_flag():
    result = runner.invoke(app, ["rules", "list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert any(r["rule_id"] == "PY-SEC-DANGEROUS-EVAL" for r in payload)


def test_rules_list_non_tty_gives_json_without_flag():
    result = runner.invoke(app, ["rules", "list"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert isinstance(payload, list)


def test_discover_json_flag(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "a.py").write_text("x = 1\n")
    result = runner.invoke(app, ["discover", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert any(f["file"] == "a.py" for f in payload)


def test_manifest_json_flag(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text("class Foo:\n    def bar(self): pass\n")
    result = runner.invoke(app, ["manifest", str(f), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert any(e["symbol"] == "Foo" for e in payload)


def test_config_show_json_flag(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    result = runner.invoke(app, ["config", "show", "--root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "extends" in payload


# ---------------------------------------------------------------------------
# Render functions: pretty path exercises (force_terminal=True Console)
# ---------------------------------------------------------------------------


def test_render_graph_build_shows_counts():
    con, buf = _console()
    render_graph_build(con, {"nodes": 42, "edges": 99, "clusters": 5, "findings": 3})
    out = buf.getvalue()
    assert "42" in out
    assert "99" in out
    assert "graph built" in out


def test_render_graph_related_shows_symbol():
    con, buf = _console()
    render_graph_related(
        con, [{"id": "fetch_user", "kind": "function", "weight": 0.9, "rank": 1}]
    )
    out = buf.getvalue()
    assert "fetch_user" in out
    assert "function" in out


def test_render_graph_neighbors_shows_direction():
    con, buf = _console()
    render_graph_neighbors(
        con,
        [
            {
                "id": "helper",
                "kind": "function",
                "edge": "calls",
                "direction": "out",
                "hops": 1,
            }
        ],
    )
    out = buf.getvalue()
    assert "helper" in out
    assert "calls" in out
    assert "out" in out


def test_render_graph_concept_shows_label_and_members():
    con, buf = _console()
    render_graph_concept(
        con,
        {
            "cluster_id": 1,
            "label": "authentication",
            "member_count": 3,
            "members": ["login", "logout", "verify"],
            "shown": 3,
        },
    )
    out = buf.getvalue()
    assert "authentication" in out
    assert "login" in out


def test_render_graph_clusters_sorted_by_size():
    con, buf = _console()
    render_graph_clusters(
        con,
        [
            {"cluster_id": 1, "label": "small", "member_count": 2},
            {"cluster_id": 2, "label": "large", "member_count": 50},
        ],
    )
    out = buf.getvalue()
    assert "large" in out
    assert out.index("large") < out.index("small")


def test_render_graph_search_shows_symbol():
    con, buf = _console()
    render_graph_search(
        con,
        [{"id": "m.py::Foo", "kind": "class", "rank": 0.5}],
    )
    assert "m.py::Foo" in buf.getvalue()


def test_render_graph_usages_groups_and_counts():
    con, buf = _console()
    render_graph_usages(
        con,
        {
            "symbol": "Foo",
            "resolved": "m.py::Foo",
            "kind": "class",
            "ambiguous": ["other.py::Foo"],
            "used_by": {"inherits": {"count": 3, "sample": ["a.py::Sub"]}},
            "depends_on": {},
            "total_in": 3,
            "total_out": 0,
        },
    )
    out = buf.getvalue()
    assert "m.py::Foo" in out and "USED BY" in out
    assert "inherits" in out and "3" in out
    assert "ambiguous" in out and "other.py::Foo" in out


def test_render_graph_usages_empty():
    con, buf = _console()
    render_graph_usages(con, {})
    assert "no such symbol" in buf.getvalue()


def test_render_rules_list_shows_rule_id():
    con, buf = _console()
    render_rules_list(
        con,
        [
            {
                "rule_id": "PY-TEST-RULE",
                "category": "security",
                "default_severity": "high",
                "framework": None,
                "standard_refs": ["bandit:B001"],
            }
        ],
    )
    out = buf.getvalue()
    assert "PY-TEST-RULE" in out
    assert "security" in out


def test_render_index_add_shows_count():
    con, buf = _console()
    render_index_add(con, {"added": ["src/a.py", "src/b.py"]})
    out = buf.getvalue()
    assert "2" in out
    assert "src/a.py" in out


def test_render_index_list_empty():
    con, buf = _console()
    render_index_list(con, [])
    assert "empty" in buf.getvalue()


def test_render_index_repos_shows_repo():
    con, buf = _console()
    render_index_repos(con, [{"repo": "myproject"}])
    assert "myproject" in buf.getvalue()


def test_render_index_forget_removed():
    con, buf = _console()
    render_index_forget(con, {"repo": "myproject", "removed": True})
    out = buf.getvalue()
    assert "removed" in out
    assert "myproject" in out


def test_render_index_forget_noop():
    con, buf = _console()
    render_index_forget(con, {"repo": "myproject", "removed": False})
    assert "nothing" in buf.getvalue()


def test_render_ignore_add_shows_rule():
    con, buf = _console()
    render_ignore_add(
        con,
        {
            "id": 1,
            "rule_id": "PY-SEC-EVAL",
            "file": None,
            "line": None,
            "reason": None,
            "note": None,
        },
    )
    assert "PY-SEC-EVAL" in buf.getvalue()


def test_render_ignore_add_shows_note():
    con, buf = _console()
    render_ignore_add(
        con,
        {
            "id": 1,
            "rule_id": "PY-SEC-EVAL",
            "file": "a.py",
            "line": 99,
            "reason": None,
            "note": "no current finding at that line — stored with literal-line fallback",
        },
    )
    out = buf.getvalue()
    assert "note" in out
    assert "literal-line" in out


def test_render_ignore_list_empty():
    con, buf = _console()
    render_ignore_list(con, [])
    assert "no ignores" in buf.getvalue()


def test_render_ignore_list_shows_rows():
    con, buf = _console()
    render_ignore_list(
        con,
        [{"id": 7, "rule_id": "PY-X", "file": "mod.py", "line": 5, "reason": "ok"}],
    )
    out = buf.getvalue()
    assert "PY-X" in out
    assert "mod.py" in out


def test_render_ignore_rm_shows_selector():
    con, buf = _console()
    render_ignore_rm(con, {"removed": True, "selector": "7"})
    assert "7" in buf.getvalue()


def test_render_ignore_clear_shows_count():
    con, buf = _console()
    render_ignore_clear(con, {"cleared": 3})
    assert "3" in buf.getvalue()


def test_render_manifest_list_shows_symbol():
    con, buf = _console()
    render_manifest_list(con, [{"line": 1, "kind": "class", "symbol": "MyClass"}])
    out = buf.getvalue()
    assert "MyClass" in out
    assert "class" in out


def test_render_manifest_list_empty():
    con, buf = _console()
    render_manifest_list(con, [])
    assert "no entries" in buf.getvalue()


def test_render_plugins_list_shows_detector():
    con, buf = _console()
    render_plugins_list(
        con,
        {
            "detectors": {"PY-SEC-EVAL": "builtin"},
            "languages": [],
            "reporters": [],
            "profiles": [],
            "warnings": [],
        },
    )
    assert "PY-SEC-EVAL" in buf.getvalue()


def test_render_discover_shows_file_and_role():
    con, buf = _console()
    render_discover(con, [{"file": "src/main.py", "role": "production"}])
    out = buf.getvalue()
    assert "src/main.py" in out
    assert "production" in out


def test_render_discover_empty():
    con, buf = _console()
    render_discover(con, [])
    assert "no files" in buf.getvalue()


def test_render_crossfile_shows_count():
    con, buf = _console()
    render_crossfile(con, {"cross_file_findings": 7})
    assert "7" in buf.getvalue()
