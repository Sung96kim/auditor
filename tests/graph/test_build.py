import pytest

from auditor.config import AuditorSettings, GraphConfig
from auditor.graph.build import (
    _GRAPH_RULE_IDS,
    GraphBuilder,
    GraphWrite,
    _concept_nodes,
    _quality_rows,
    compute_abstractness,
)
from auditor.graph.extract import extract_file_facts
from auditor.graph.model import (
    EdgeKind,
    FactKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    Provenance,
    UnresolvedReason,
)
from auditor.graph.refine.models import (
    Anchor,
    Refinement,
    RefinementKind,
    RefinementStatus,
    RefinementTarget,
    Run,
)
from auditor.languages.python.detectors.graph_rules import GOD_CONCEPT_RULE
from auditor.models import Category, Finding, Severity, VerdictKind

STUB_CLASS = "class Base:\n    def run(self): ...\n"


def test_compute_abstractness_stub_method():
    facts = extract_file_facts("base.py", STUB_CLASS, "production")
    run = next(n for n in facts.nodes if n.id == "base.py::Base.run")
    assert compute_abstractness(run, proto_method_ids=set()) >= 0.4  # stub body


async def test_build_reports_stage_progress(facts_store):
    settings = AuditorSettings(
        graph=GraphConfig(enabled=True, name_similarity_threshold=0.2)
    )
    seen: list[str] = []
    await GraphBuilder().run(facts_store, settings, progress=seen.append)
    for label in (
        "resolving structural edges",
        "computing naming similarity",
        "ranking (PageRank)",
        "clustering concepts",
        "persisting graph",
    ):
        assert label in seen
    assert seen.index("resolving structural edges") < seen.index("clustering concepts")
    assert seen.index("clustering concepts") < seen.index("persisting graph")


async def test_build_writes_nodes_edges_clusters(facts_store):
    settings = AuditorSettings(
        graph=GraphConfig(enabled=True, name_similarity_threshold=0.2)
    )
    summary = await GraphBuilder().run(facts_store, settings)
    assert summary["nodes"] >= 4
    # override edge survived the repo pass
    over = await facts_store.graph.edges_of("impl.py::Impl.run", ["overrides"])
    assert any(e["dst"] == "base.py::Base.run" for e in over)
    # same-module call resolved by name
    calls = await facts_store.graph.edges_of("impl.py::_local", ["calls"])
    assert any(
        e["dst"] == "impl.py::_local" or e["src"] == "impl.py::_local" for e in calls
    )
    # every node got a cluster id + a rank
    nodes = await facts_store.graph.nodes()
    assert all(n["cluster_id"] is not None for n in nodes if n["kind"] != "module")


PROP = (
    "class Box:\n"
    "    @property\n"
    "    def config(self):\n"
    "        return self._c\n"
    "    @config.setter\n"
    "    def config(self, v):\n"
    "        self._c = v\n"
)


def test_test_and_module_nodes_excluded_from_clusters():
    prod = [
        GraphNode(
            id=f"p.py::f{i}",
            kind=NodeKind.FUNCTION,
            name=f"f{i}",
            module="p.py",
            qualname=f"f{i}",
            doc_tokens=("user", "fetch"),
            role="production",
        )
        for i in range(3)
    ]
    tests = [
        GraphNode(
            id=f"t.py::tf{i}",
            kind=NodeKind.FUNCTION,
            name=f"tf{i}",
            module="t.py",
            qualname=f"tf{i}",
            doc_tokens=("user", "fetch"),
            role="test",
        )
        for i in range(3)
    ]
    mod = GraphNode(
        id="p.py",
        kind=NodeKind.MODULE,
        name="p.py",
        module="p.py",
        qualname="p",
        doc_tokens=("user",),
        role="production",
    )
    nodes = [*prod, *tests, mod]
    assert {n.id for n in _concept_nodes(nodes)} == {
        n.id for n in prod
    }  # only prod symbols are clustered


async def test_build_personalizes_rank_against_tests(graph_store):
    prod_src = "def helper():\n    return shared()\n\ndef shared():\n    return 1\n"
    test_src = "from prod import shared\n\ndef test_thing():\n    return shared()\n"
    await graph_store.graph.set_facts(
        "prod.py",
        extract_file_facts("prod.py", prod_src, "production").model_dump_json(),
        "h1",
    )
    await graph_store.graph.set_facts(
        "test_x.py",
        extract_file_facts("test_x.py", test_src, "test").model_dump_json(),
        "h2",
    )
    settings = AuditorSettings(
        graph=GraphConfig(enabled=True, name_similarity_threshold=0.2)
    )
    await GraphBuilder().run(graph_store, settings)
    nodes = {n["node_id"]: n for n in await graph_store.graph.nodes()}
    assert nodes["prod.py::helper"]["rank"] > nodes["test_x.py::test_thing"]["rank"]


async def test_build_runs_detectors_and_persists(graph_store):
    src_hub = "def hub():\n    return 1\n"
    callers = "from hub import hub\n" + "".join(
        f"def c{i}():\n    return hub()\n" for i in range(12)
    )
    await graph_store.graph.set_facts(
        "hub.py",
        extract_file_facts("hub.py", src_hub, "production").model_dump_json(),
        "h1",
    )
    await graph_store.graph.set_facts(
        "callers.py",
        extract_file_facts("callers.py", callers, "production").model_dump_json(),
        "h2",
    )
    settings = AuditorSettings(
        graph=GraphConfig(enabled=True, name_similarity_threshold=0.2, detect=True)
    )
    summary = await GraphBuilder().run(graph_store, settings)
    assert "findings" in summary
    assert summary["findings"] >= 0
    # detect=False clears graph findings and adds none
    settings_off = AuditorSettings(
        graph=GraphConfig(enabled=True, name_similarity_threshold=0.2, detect=False)
    )
    summary_off = await GraphBuilder().run(graph_store, settings_off)
    assert summary_off["findings"] == 0


async def test_dedup_property_getter_setter(graph_store):
    facts = extract_file_facts("prop.py", PROP, "production")
    # getter + setter share an id — the extractor now merges them into ONE node (Finding A),
    # unioning their facts, rather than emitting two and lossily dropping one at build dedup.
    dup_nodes = [n for n in facts.nodes if n.id == "prop.py::Box.config"]
    assert len(dup_nodes) == 1, "extractor merges getter+setter into one node"

    await graph_store.graph.set_facts("prop.py", facts.model_dump_json(), "h1")
    settings = AuditorSettings(
        graph=GraphConfig(enabled=True, name_similarity_threshold=0.2)
    )
    await GraphBuilder().run(graph_store, settings)
    nodes = await graph_store.graph.nodes()
    matching = [n for n in nodes if n["node_id"] == "prop.py::Box.config"]
    assert len(matching) == 1


async def test_build_reports_and_persists_the_unresolved_count(facts_store):
    """`svc.py::load_user` calls `get_user_record()`, which nothing in the fixture defines, so it
    earns no row; `impl.py::Impl.run` calls `load_user()`, which `svc.py` defines and `impl.py`
    never imports, so it does."""
    settings = AuditorSettings(
        graph=GraphConfig(enabled=True, name_similarity_threshold=0.2)
    )
    summary = await GraphBuilder().run(facts_store, settings)
    rows = await facts_store.graph.unresolved()
    assert summary["unresolved"] == len(rows)
    assert ("impl.py::Impl.run", "load_user") in {
        (r["node_id"], r["name"]) for r in rows
    }
    assert "get_user_record" not in {r["name"] for r in rows}


async def test_build_records_text_sparse_symbols(facts_store):
    settings = AuditorSettings(
        graph=GraphConfig(enabled=True, name_similarity_threshold=0.2)
    )
    await GraphBuilder().run(facts_store, settings)
    sparse = await facts_store.graph.unresolved(reasons=["text_sparse"])
    assert sparse, (
        "the tiny base/impl fixture has no symbol with 4 distinct concept tokens"
    )
    assert all(r["fact_kind"] == "node" and r["priority"] == 4 for r in sparse)


async def test_a_text_sparse_test_symbol_does_not_reach_the_queue(graph_store):
    """The resolver gates test callers out; the build pass must gate the same set out, or the
    priority-4 band fills with `tests/`."""
    for path, src, role in (
        ("svc.py", "def load_user():\n    return 1\n", "production"),
        ("tests/test_x.py", "def test_zz():\n    return 1\n", "test"),
    ):
        await graph_store.graph.set_facts(
            path, extract_file_facts(path, src, role).model_dump_json(), path
        )
    settings = AuditorSettings(
        graph=GraphConfig(enabled=True, name_similarity_threshold=0.2)
    )
    await GraphBuilder().run(graph_store, settings)
    rows = await graph_store.graph.unresolved()
    assert rows, "the production symbol must still earn its text_sparse row"
    assert not [r for r in rows if r["node_id"].startswith("tests/")]


async def test_build_replaces_the_queue_rather_than_appending(facts_store):
    settings = AuditorSettings(
        graph=GraphConfig(enabled=True, name_similarity_threshold=0.2)
    )
    first = await GraphBuilder().run(facts_store, settings)
    second = await GraphBuilder().run(facts_store, settings)
    assert first["unresolved"] == second["unresolved"]
    assert len(await facts_store.graph.unresolved()) == second["unresolved"]


async def test_build_with_no_facts_reports_an_empty_queue(graph_store):
    settings = AuditorSettings(graph=GraphConfig(enabled=True))
    assert await GraphBuilder().run(graph_store, settings) == {
        "nodes": 0,
        "edges": 0,
        "clusters": 0,
        "unresolved": 0,
        "findings": 0,
    }
    assert await graph_store.graph.unresolved() == []


def test_quality_rows_flag_fallback_labels_and_singletons():
    """Unit test for the two cluster branches: this repo currently produces no `generic_label`
    row at all, so a build-level test would assert nothing."""
    nodes = [
        GraphNode(
            id=f"m.py::{name}",
            kind=NodeKind.FUNCTION,
            name=name,
            module="m.py",
            qualname=name,
            rank=rank,
        )
        for name, rank in (("a", 0.9), ("b", 0.1), ("c", 0.5))
    ]
    rows = _quality_rows(
        nodes,
        {"m.py::b"},
        {"m.py::a": 1, "m.py::b": 1, "m.py::c": 2},
        {1: "user"},  # cluster 2 contributed no token, so its label falls back
        {1: 2, 2: 1},
    )
    seen = {(r.node_id, r.reason) for r in rows}
    assert ("m.py::b", UnresolvedReason.TEXT_SPARSE) in seen
    assert ("m.py::c", UnresolvedReason.GENERIC_LABEL) in seen
    assert ("m.py::c", UnresolvedReason.SINGLETON_CLUSTER) in seen
    assert (
        "m.py::a",
        UnresolvedReason.GENERIC_LABEL,
    ) not in seen  # cluster 1 has a real label
    assert [r.name for r in rows if r.reason is UnresolvedReason.GENERIC_LABEL] == [
        "cluster-2"
    ]
    assert all(r.fact_kind is FactKind.NODE and r.priority == 4 for r in rows)


async def test_a_failed_persist_leaves_the_previous_graph(facts_store, monkeypatch):
    """One transaction, so a crash between the node swap and the queue swap cannot half-land."""
    settings = AuditorSettings()
    settings.graph.enabled = True
    await GraphBuilder().run(facts_store, settings)
    before_nodes = await facts_store.graph.nodes()
    before_queue = await facts_store.graph.unresolved()
    assert before_nodes and before_queue

    def explode(*args, **kwargs):
        raise RuntimeError("queue write failed")

    monkeypatch.setattr(type(facts_store.graph), "write_unresolved", explode)
    with pytest.raises(RuntimeError, match="queue write failed"):
        await GraphBuilder().run(facts_store, settings)

    assert await facts_store.graph.nodes() == before_nodes
    assert await facts_store.graph.unresolved() == before_queue


async def test_an_emptied_repo_clears_the_previous_graph_findings(facts_store):
    """The empty-graph path is the same transaction as any other build, so it cannot leave the
    last build's GRAPH-* rows behind while reporting `findings: 0`."""
    settings = AuditorSettings(
        graph=GraphConfig(enabled=True, name_similarity_threshold=0.2, detect=True)
    )
    await GraphBuilder().run(facts_store, settings)
    await facts_store.findings.add("m.py", [_graph_finding()])
    assert await facts_store.findings.all()

    await facts_store.graph.clear_facts()
    summary = await GraphBuilder().run(facts_store, settings)

    assert summary == {
        "nodes": 0,
        "edges": 0,
        "clusters": 0,
        "unresolved": 0,
        "findings": 0,
    }
    stored = await facts_store.findings.all()
    assert [f for f in stored if f.rule_id in _GRAPH_RULE_IDS] == []
    assert await facts_store.graph.nodes() == []
    assert await facts_store.graph.unresolved() == []


async def test_an_empty_build_leaves_the_findings_alone_when_detectors_are_off(
    facts_store,
):
    """`detect=False` means "do not touch the findings", on the empty path as on any other."""
    await facts_store.findings.add("m.py", [_graph_finding()])
    settings = AuditorSettings(graph=GraphConfig(enabled=True, detect=False))
    await facts_store.graph.clear_facts()
    await GraphBuilder().run(facts_store, settings)
    assert [f.rule_id for f in await facts_store.findings.all()] == [GOD_CONCEPT_RULE]


def test_the_summary_counts_what_the_write_carries():
    """The one place the build's counts are named, so the empty path and the full path cannot
    report different shapes."""
    write = GraphWrite(
        nodes=(_node("m.py::a"),),
        edges=(GraphEdge(src="m.py::a", dst="m.py::b", kind=EdgeKind.CALLS),),
        findings={"m.py": [_graph_finding(), _graph_finding()]},
    )
    assert write.summary() == {
        "nodes": 1,
        "edges": 1,
        "clusters": 0,
        "unresolved": 0,
        "findings": 2,
    }


def _node(node_id: str) -> GraphNode:
    return GraphNode(
        id=node_id,
        kind=NodeKind.FUNCTION,
        name=node_id.split("::")[-1],
        module=node_id.split("::")[0],
        qualname=node_id.split("::")[-1],
    )


def _graph_finding() -> Finding:
    return Finding(
        rule_id=GOD_CONCEPT_RULE,
        category=Category.OOP_COMPOSITION,
        severity=Severity.LOW,
        verdict_kind=VerdictKind.CANDIDATE,
        line=1,
        message="stale",
    )


async def test_an_active_refinement_adds_a_refined_edge(refined_facts_store):
    settings = AuditorSettings()
    settings.graph.enabled = True
    await GraphBuilder().run(refined_facts_store, settings)
    edges = await refined_facts_store.graph.all_edges()
    refined = [e for e in edges if e["provenance"] == "refined"]
    assert [(e["src"], e["dst"]) for e in refined] == [
        ("impl.py::Impl.run", "svc.py::load_user")
    ]


async def test_the_deterministic_edge_set_is_unchanged_by_a_refinement(
    refined_facts_store,
):
    """Invariant 1: the overlay adds, it never rewrites what the resolver produced."""
    settings = AuditorSettings()
    settings.graph.enabled = True
    await GraphBuilder().run(refined_facts_store, settings)
    with_refinement = {
        (e["src"], e["dst"], e["kind"])
        for e in await refined_facts_store.graph.all_edges()
        if e["provenance"] == "deterministic"
    }
    await refined_facts_store.refinements.set_status(1, RefinementStatus.REVERTED)
    await GraphBuilder().run(refined_facts_store, settings)
    plain = {
        (e["src"], e["dst"], e["kind"])
        for e in await refined_facts_store.graph.all_edges()
    }
    assert with_refinement == plain


async def test_a_reverted_refinement_stops_being_applied(refined_facts_store):
    settings = AuditorSettings()
    settings.graph.enabled = True
    await GraphBuilder().run(refined_facts_store, settings)
    await refined_facts_store.refinements.set_status(1, RefinementStatus.REVERTED)
    await GraphBuilder().run(refined_facts_store, settings)
    assert not [
        e
        for e in await refined_facts_store.graph.all_edges()
        if e["provenance"] == "refined"
    ]


async def test_an_active_refinement_retires_the_queue_row_it_answers(
    refined_facts_store,
):
    """Spec 5.7: an accepted refinement removes its own `(node_id, name)` row from the queue."""
    settings = AuditorSettings()
    settings.graph.enabled = True
    await refined_facts_store.refinements.set_status(1, RefinementStatus.REVERTED)
    await GraphBuilder().run(refined_facts_store, settings)
    before = {
        (r["node_id"], r["name"]) for r in await refined_facts_store.graph.unresolved()
    }
    assert ("impl.py::Impl.run", "load_user") in before

    await refined_facts_store.refinements.set_status(1, RefinementStatus.ACTIVE)
    await GraphBuilder().run(refined_facts_store, settings)
    after = {
        (r["node_id"], r["name"]) for r in await refined_facts_store.graph.unresolved()
    }
    assert ("impl.py::Impl.run", "load_user") not in after


async def test_a_refinement_anchored_to_a_changed_node_goes_stale(facts_store):
    """Invariant 3: the anchor is what makes a correction expire on its own."""
    settings = AuditorSettings()
    settings.graph.enabled = True
    run_id = await facts_store.runs.add_run(
        Run(repo_identity=facts_store.partition.identity, started_at=1.0)
    )
    rid = await facts_store.refinements.add_refinement(
        Refinement(
            run_id=run_id,
            repo_identity=facts_store.partition.identity,
            kind=RefinementKind.ADD_EDGE,
            target=RefinementTarget(
                src="impl.py::Impl.run",
                dst="svc.py::load_user",
                edge_kind=EdgeKind.CALLS,
                name="load_user",
            ),
            status=RefinementStatus.ACTIVE,
        ),
        (
            Anchor(
                node_id="impl.py::Impl.run",
                path="impl.py",
                truth_sha="a-sha-from-a-different-body",
            ),
        ),
    )
    await GraphBuilder().run(facts_store, settings)
    (stored,) = await facts_store.refinements.refinements()
    assert stored.refinement_id == rid
    assert stored.status is RefinementStatus.STALE
    assert not [
        e for e in await facts_store.graph.all_edges() if e["provenance"] == "refined"
    ]


async def test_the_detectors_never_see_a_refined_edge(refined_facts_store, monkeypatch):
    seen: list[list] = []

    def spy(nodes, edges, clusters, settings):
        seen.append(list(edges))
        return {}

    monkeypatch.setattr("auditor.graph.build.run_graph_detectors", spy)
    settings = AuditorSettings()
    settings.graph.enabled = True
    await GraphBuilder().run(refined_facts_store, settings)
    assert seen and all(e.provenance is Provenance.DETERMINISTIC for e in seen[0])


async def test_a_retarget_leaves_the_detector_edge_list_byte_identical(
    facts_store, monkeypatch
):
    """`retarget_edge` deletes a deterministic edge from the merged list, so the detectors have to
    read the list captured before the overlay, not a filter over the merged one."""
    seen: list[tuple[tuple[str, str, str], ...]] = []

    def spy(nodes, edges, clusters, settings):
        seen.append(tuple((e.src, e.dst, e.kind.value) for e in edges))
        return {}

    monkeypatch.setattr("auditor.graph.build.run_graph_detectors", spy)
    settings = AuditorSettings()
    settings.graph.enabled = True
    await GraphBuilder().run(facts_store, settings)
    run_id = await facts_store.runs.add_run(
        Run(repo_identity=facts_store.partition.identity, started_at=1.0)
    )
    await facts_store.refinements.add_refinement(
        Refinement(
            run_id=run_id,
            repo_identity=facts_store.partition.identity,
            kind=RefinementKind.RETARGET_EDGE,
            target=RefinementTarget(
                src="impl.py::Impl.run",
                edge_kind=EdgeKind.CALLS,
                from_dst="impl.py::_local",
                to_dst="svc.py::load_user",
                name="_local",
            ),
            status=RefinementStatus.ACTIVE,
        )
    )
    await GraphBuilder().run(facts_store, settings)
    assert seen[0] == seen[1]  # byte-identical, ordering included
    merged = {
        (e["src"], e["dst"], e["kind"]) for e in await facts_store.graph.all_edges()
    }
    assert ("impl.py::Impl.run", "impl.py::_local", "calls") not in merged
    assert ("impl.py::Impl.run", "svc.py::load_user", "calls") in merged


async def test_the_detectors_see_a_graph_no_refinement_touched(
    facts_store, monkeypatch
):
    """Spec section 2. `GraphContext.by_cluster` is built from each node's `cluster_id`, so a
    `move_node` would change `GRAPH-SCATTERED-CONCEPT` unless the detectors get a re-clustered
    node list as well as the pre-overlay edge list."""
    seen: list[tuple[tuple[tuple[str, int | None], ...], int]] = []

    def spy(nodes, edges, clusters, settings):
        seen.append((tuple(sorted((n.id, n.cluster_id) for n in nodes)), len(edges)))
        return {}

    monkeypatch.setattr("auditor.graph.build.run_graph_detectors", spy)
    settings = AuditorSettings()
    settings.graph.enabled = True
    await GraphBuilder().run(facts_store, settings)
    run_id = await facts_store.runs.add_run(
        Run(repo_identity=facts_store.partition.identity, started_at=1.0)
    )
    await facts_store.refinements.add_refinement(
        Refinement(
            run_id=run_id,
            repo_identity=facts_store.partition.identity,
            kind=RefinementKind.MOVE_NODE,
            target=RefinementTarget(
                node_id="impl.py::Impl.run", members=("svc.py::load_user",)
            ),
            status=RefinementStatus.ACTIVE,
        )
    )
    await GraphBuilder().run(facts_store, settings)
    assert seen[0] == seen[1]
    served = {n["node_id"]: n["cluster_id"] for n in await facts_store.graph.nodes()}
    assert served["impl.py::Impl.run"] != dict(seen[1][0])["impl.py::Impl.run"]
