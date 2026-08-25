"""Tests for the --json flag and pretty render functions.

JSON path: invoke via CliRunner (non-TTY) → same JSON as before (byte-identical contract).
Pretty path: call render functions directly with a StringIO Console (force_terminal=True).
"""

import io
import json

import pytest
from rich.console import Console
from typer.testing import CliRunner

from auditor.cli import app
from auditor.cli.payloads import (
    ConfigCheckReport,
    CrossfileReport,
    DetectorInfo,
    DiscoveredFile,
    DiscoverReport,
    GraphBuildReport,
    IgnoreAddReport,
    IgnoreClearReport,
    IgnoreListReport,
    IgnoreRmReport,
    IgnoreRow,
    IndexAddReport,
    IndexForgetReport,
    IndexListReport,
    IndexReposReport,
    InitReport,
    ManifestReport,
    PluginsReport,
    RepoRow,
    RulesListReport,
)
from auditor.cli.render import (
    render_config_check,
    render_crossfile,
    render_discover,
    render_graph_build,
    render_graph_clusters,
    render_graph_concept,
    render_graph_neighbors,
    render_graph_related,
    render_graph_search,
    render_graph_unresolved,
    render_graph_usages,
    render_ignore_add,
    render_ignore_clear,
    render_ignore_list,
    render_ignore_rm,
    render_index_add,
    render_index_forget,
    render_index_list,
    render_index_repos,
    render_init,
    render_manifest_list,
    render_plugins_list,
    render_rules_list,
)
from auditor.graph.model import CallForm, FactKind, UnresolvedReason
from auditor.graph.payloads import (
    ClusterMember,
    ClusterRow,
    ClustersReport,
    ConceptPayload,
    NeighborRow,
    NeighborsReport,
    QueueReport,
    QueueRowPayload,
    RelatedReport,
    RelatedRow,
    SearchReport,
    SearchRow,
    UsageGroup,
    UsagesPayload,
)
from auditor.models import (
    FileRole,
    IndexEntry,
    ManifestEntry,
    ManifestEntryKind,
    Severity,
    VerdictKind,
)
from auditor.registry import REGISTRY, RuleRow

runner = CliRunner()


def _console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    con = Console(file=buf, force_terminal=True, width=100)
    return con, buf


def _plain_console() -> tuple[Console, io.StringIO]:
    """No colour and no highlighter, for assertions on the text rather than the styling."""
    buf = io.StringIO()
    return Console(file=buf, width=100, no_color=True, highlight=False), buf


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
    render_graph_build(
        con,
        GraphBuildReport(
            nodes=42,
            edges=99,
            clusters=5,
            unresolved=7,
            findings=3,
            refined=0,
            expired=0,
        ),
    )
    out = buf.getvalue()
    assert "42" in out
    assert "99" in out
    assert "unresolved" in out and "7" in out
    assert "graph built" in out


def test_render_graph_unresolved_shows_the_row():
    con, buf = _console()
    render_graph_unresolved(
        con,
        QueueReport(
            (
                QueueRowPayload(
                    node_id="m.py::use",
                    fact_kind=FactKind.ATTR_CALLEE,
                    call_form=CallForm.ATTR,
                    name="handle",
                    reason=UnresolvedReason.UNIMPORTABLE_NAME,
                    definers=("helper.py::handle",),
                    candidates=(),
                    definers_count=1,
                    candidates_count=0,
                    externally_bound=True,
                ),
            )
        ),
    )
    out = buf.getvalue()
    assert "m.py::use" in out
    assert "unimportable_name" in out
    assert "attr" in out


def test_render_graph_unresolved_empty_queue():
    con, buf = _console()
    render_graph_unresolved(con, QueueReport(()))
    assert "empty" in buf.getvalue()


def test_render_graph_related_shows_symbol():
    con, buf = _console()
    render_graph_related(
        con,
        RelatedReport(
            (RelatedRow(id="fetch_user", kind="function", weight=0.9, rank=1.0),)
        ),
    )
    out = buf.getvalue()
    assert "fetch_user" in out
    assert "function" in out


def test_render_graph_neighbors_shows_direction():
    con, buf = _console()
    render_graph_neighbors(
        con,
        NeighborsReport(
            (
                NeighborRow(
                    id="helper", kind="function", edge="calls", direction="out", hops=1
                ),
            )
        ),
    )
    out = buf.getvalue()
    assert "helper" in out
    assert "calls" in out
    assert "out" in out


def test_render_graph_concept_counts_and_names_its_members():
    """Regression: it read `member_count`, which the query never returns, so every concept read
    `0 members`, and it printed each member row's whole dict instead of the symbol id."""
    con, buf = _plain_console()
    render_graph_concept(
        con,
        ConceptPayload(
            cluster_id=1,
            label="authentication",
            members=(
                ClusterMember(
                    id="a.py::login", name="login", module="a.py", rank=0.5, refined=0
                ),
                ClusterMember(
                    id="a.py::logout", name="logout", module="a.py", rank=0.4, refined=0
                ),
            ),
        ),
    )
    out = " ".join(buf.getvalue().split())
    assert "authentication" in out
    assert "2 members" in out
    assert "a.py::login" in out
    assert "'name'" not in out  # not a dict repr


def test_render_graph_clusters_sorted_by_size():
    con, buf = _console()
    render_graph_clusters(
        con,
        ClustersReport(
            (
                ClusterRow(cluster_id=1, label="small", member_count=2),
                ClusterRow(cluster_id=2, label="large", member_count=50),
            )
        ),
    )
    out = buf.getvalue()
    assert "large" in out
    assert out.index("large") < out.index("small")


def test_render_graph_search_shows_symbol():
    con, buf = _console()
    render_graph_search(
        con, SearchReport((SearchRow(id="m.py::Foo", kind="class", rank=0.5),))
    )
    assert "m.py::Foo" in buf.getvalue()


def test_render_graph_usages_groups_and_counts():
    con, buf = _console()
    render_graph_usages(
        con,
        UsagesPayload(
            symbol="Foo",
            resolved="m.py::Foo",
            kind="class",
            ambiguous=("other.py::Foo",),
            used_by={"inherits": UsageGroup(count=3, sample=("a.py::Sub",))},
            total_in=3,
        ),
    )
    out = buf.getvalue()
    assert "m.py::Foo" in out and "USED BY" in out
    assert "inherits" in out and "3" in out
    assert "ambiguous" in out and "other.py::Foo" in out


def test_render_graph_usages_empty():
    con, buf = _console()
    render_graph_usages(con, None)
    assert "no such symbol" in buf.getvalue()


def test_render_rules_list_shows_rule_id():
    con, buf = _console()
    render_rules_list(
        con,
        RulesListReport(
            (
                RuleRow(
                    rule_id="PY-TEST-RULE",
                    category="security",
                    framework=None,
                    default_severity=Severity.HIGH.value,
                    verdict_kind=VerdictKind.AUTO.value,
                    standard_refs=["bandit:B001"],
                    source="built-in",
                ),
            )
        ),
    )
    out = buf.getvalue()
    assert "PY-TEST-RULE" in out
    assert "security" in out


def test_render_index_add_shows_count():
    con, buf = _console()
    render_index_add(con, IndexAddReport(added=("src/a.py", "src/b.py")))
    out = buf.getvalue()
    assert "2" in out
    assert "src/a.py" in out


def test_render_index_list_shows_the_finding_count():
    """Regression: the column read `finding_count`, which IndexEntry has never carried, so it was
    blank for every file."""
    con, buf = _console()
    render_index_list(
        con,
        IndexListReport(
            (
                IndexEntry(
                    path="a.py",
                    sha256="x",
                    lines=3,
                    language="python",
                    role=FileRole.PRODUCTION,
                    last_scanned=0.0,
                    counts={Severity.HIGH: 2, Severity.LOW: 1},
                ),
            )
        ),
    )
    out = buf.getvalue()
    assert "a.py" in out
    assert "3" in out  # 2 high + 1 low


def test_render_index_list_empty():
    con, buf = _console()
    render_index_list(con, IndexListReport(()))
    assert "empty" in buf.getvalue()


def test_render_index_repos_shows_repo():
    con, buf = _console()
    render_index_repos(
        con,
        IndexReposReport(
            (RepoRow(repo="myproject", name="myproject", last_scanned=0.0),)
        ),
    )
    assert "myproject" in buf.getvalue()


@pytest.mark.parametrize(
    ("removed", "expected"), [(True, "removed"), (False, "nothing")]
)
def test_render_index_forget(removed, expected):
    con, buf = _console()
    render_index_forget(con, IndexForgetReport(repo="myproject", removed=removed))
    out = buf.getvalue()
    assert expected in out
    assert "myproject" in out


def test_render_ignore_add_shows_rule():
    con, buf = _console()
    render_ignore_add(con, IgnoreAddReport(id=1, rule_id="PY-SEC-EVAL"))
    assert "PY-SEC-EVAL" in buf.getvalue()


def test_render_ignore_add_shows_note():
    con, buf = _console()
    render_ignore_add(
        con,
        IgnoreAddReport(
            id=1,
            rule_id="PY-SEC-EVAL",
            file="a.py",
            line=99,
            note="no current finding at that line; stored with literal-line fallback",
        ),
    )
    out = buf.getvalue()
    assert "note" in out
    assert "literal-line" in out


def test_render_ignore_list_empty():
    con, buf = _console()
    render_ignore_list(con, IgnoreListReport(()))
    assert "no ignores" in buf.getvalue()


def test_render_ignore_list_shows_rows():
    con, buf = _console()
    render_ignore_list(
        con,
        IgnoreListReport(
            (
                IgnoreRow(
                    id=7,
                    rule_id="PY-X",
                    file="mod.py",
                    line=5,
                    reason="ok",
                    created_at=0.0,
                ),
            )
        ),
    )
    out = buf.getvalue()
    assert "PY-X" in out
    assert "mod.py" in out


def test_render_ignore_rm_shows_selector():
    con, buf = _console()
    render_ignore_rm(con, IgnoreRmReport(removed=True, selector="7"))
    assert "7" in buf.getvalue()


def test_render_ignore_clear_shows_count():
    con, buf = _console()
    render_ignore_clear(con, IgnoreClearReport(cleared=3))
    assert "3" in buf.getvalue()


def test_render_manifest_list_shows_symbol():
    con, buf = _console()
    render_manifest_list(
        con,
        ManifestReport(
            (ManifestEntry(line=1, kind=ManifestEntryKind.CLASS, symbol="MyClass"),)
        ),
    )
    out = buf.getvalue()
    assert "MyClass" in out
    assert "class" in out


def test_render_manifest_list_empty():
    con, buf = _console()
    render_manifest_list(con, ManifestReport(()))
    assert "no entries" in buf.getvalue()


def test_render_plugins_list_renders_the_registry_snapshot():
    """Fed the real snapshot, so `extra="forbid"` catches a registry key the models have not
    declared instead of dropping it on the way to the wire."""
    con, buf = _console()
    render_plugins_list(con, PluginsReport.of(REGISTRY.snapshot(), warnings=()))
    out = buf.getvalue()
    assert "PY-SEC-DANGEROUS-EVAL" in out
    assert "built-in" in out


def test_render_plugins_list_shows_the_source():
    """Regression: the source column read a top-level `_sources` map the snapshot never emits."""
    con, buf = _console()
    render_plugins_list(
        con,
        PluginsReport(
            detectors={
                "PY-SEC-EVAL": DetectorInfo(category="security", source="built-in"),
                "HOUSE-NO-PRINT": DetectorInfo(
                    category="house", source="house_rules.py"
                ),
            },
            warnings=("a plugin failed to load",),
        ),
    )
    out = buf.getvalue()
    assert "PY-SEC-EVAL" in out and "built-in" in out
    assert "HOUSE-NO-PRINT" in out and "house_rules.py" in out
    assert "a plugin failed to load" in out


def test_render_discover_shows_file_and_role():
    con, buf = _console()
    render_discover(
        con,
        DiscoverReport((DiscoveredFile(file="src/main.py", role=FileRole.PRODUCTION),)),
    )
    out = buf.getvalue()
    assert "src/main.py" in out
    assert "production" in out


def test_render_discover_empty():
    con, buf = _console()
    render_discover(con, DiscoverReport(()))
    assert "no files" in buf.getvalue()


def test_render_crossfile_shows_count():
    con, buf = _console()
    render_crossfile(con, CrossfileReport(cross_file_findings=7))
    assert "7" in buf.getvalue()


def _init_payload(**over) -> InitReport:
    fields: dict[str, object] = {
        "home": "/home/u/.auditor",
        "config": "/home/u/.auditor/config.json",
        "schema_path": "/home/u/.auditor/config.schema.json",
    }
    return InitReport(**(fields | over))


def test_render_init_marks_a_check_run_as_not_written():
    """--check attempts nothing, so reusing the up-to-date wording claimed files exist that
    a fresh machine has never had."""
    con, buf = _console()
    render_init(con, _init_payload(checked=True))
    assert "not written (--check)" in buf.getvalue()
    assert "up to date" not in buf.getvalue()


def test_render_init_reports_a_completed_migration():
    con, buf = _console()
    render_init(con, _init_payload(moved_from="/old/root", migrated=True))
    out = " ".join(buf.getvalue().split())
    assert "the breadcrumb now points here" in out
    assert "re-run with --migrate" not in out


def test_render_init_still_asks_for_migrate_when_not_migrated():
    con, buf = _console()
    render_init(con, _init_payload(moved_from="/old/root"))
    assert "re-run with --migrate" in " ".join(buf.getvalue().split())


@pytest.mark.parametrize("unknown", [(), ("malware_scan.bogus",)])
def test_render_config_check_names_the_root(unknown):
    """Which config was checked is the command's whole point, and only --json callers saw it."""
    con, buf = _plain_console()
    render_config_check(con, ConfigCheckReport(root="/w/repo", policy_unknown=unknown))
    assert "/w/repo" in " ".join(buf.getvalue().split())
