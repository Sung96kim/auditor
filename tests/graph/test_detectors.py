from auditor.config import AuditorSettings
from auditor.graph.detectors import (
    GodConcept,
    GraphContext,
    NamingInconsistency,
    ScatteredConcept,
    run_graph_detectors,
)
from auditor.graph.model import EdgeKind, GraphCluster, GraphEdge, GraphNode, NodeKind


def _fn(nid, name, module="m.py", role="production", rank=0.0):
    return GraphNode(
        id=nid,
        kind=NodeKind.FUNCTION,
        name=name,
        module=module,
        qualname=name,
        role=role,
        rank=rank,
        line=1,
    )


def test_god_concept_flags_fan_out_as_decompose():
    # a node that CALLS many others = god object (high out-degree)
    god = _fn("m.py::orchestrator", "orchestrator")
    targets = [_fn(f"m.py::t{i}", f"t{i}") for i in range(40)]
    edges = [GraphEdge(src=god.id, dst=t.id, kind=EdgeKind.CALLS) for t in targets]
    res = GodConcept(GraphContext([god, *targets], edges, [], AuditorSettings())).detect()
    msgs = {f.evidence: f.message for _, f in res}
    assert "m.py::orchestrator" in msgs
    assert "fan-out" in msgs["m.py::orchestrator"] and "decompos" in msgs["m.py::orchestrator"]


def test_god_concept_flags_fan_in_as_bottleneck_not_decompose():
    # a node CALLED by many = bottleneck (high in-degree, zero fan-out) — must NOT say decompose
    sink = _fn("m.py::popular", "popular")
    callers = [_fn(f"m.py::c{i}", f"c{i}") for i in range(40)]
    edges = [GraphEdge(src=c.id, dst=sink.id, kind=EdgeKind.CALLS) for c in callers]
    res = GodConcept(GraphContext([sink, *callers], edges, [], AuditorSettings())).detect()
    msg = next(f.message for _, f in res if f.evidence == "m.py::popular")
    assert "bottleneck" in msg and "blast-radius" in msg
    assert "decompos" not in msg


def test_god_concept_ignores_tests():
    # test-role nodes excluded even when central
    sink = _fn("t.py::tsink", "tsink", role="test")
    callers = [_fn(f"t.py::c{i}", f"c{i}", role="test") for i in range(40)]
    edges = [GraphEdge(src=c.id, dst=sink.id, kind=EdgeKind.CALLS) for c in callers]
    assert GodConcept(GraphContext([sink, *callers], edges, [], AuditorSettings())).detect() == []


def _cn(nid, name, module, cid):
    return GraphNode(
        id=nid,
        kind=NodeKind.FUNCTION,
        name=name,
        module=module,
        qualname=name,
        role="production",
        cluster_id=cid,
        rank=0.1,
        line=1,
    )


def test_scattered_concept_flags_fragmented_cluster():
    # 6 members across 6 modules => modules=6>=5, ratio=1.0>=0.5 -> flagged
    nodes = [_cn(f"m{i}.py::f{i}", f"f{i}", f"m{i}.py", 0) for i in range(6)]
    clusters = [GraphCluster(cluster_id=0, label="widget", member_count=6)]
    ctx = GraphContext(nodes, [], clusters, AuditorSettings())
    found = ScatteredConcept(ctx).detect()
    assert len(found) == 1
    assert "widget" in found[0][1].message


def test_scattered_concept_ignores_concentrated_cluster():
    # 6 members in 2 modules => ratio 0.33 < 0.5 -> not flagged
    nodes = [_cn(f"a.py::f{i}", f"f{i}", "a.py", 0) for i in range(3)] + [
        _cn(f"b.py::g{i}", f"g{i}", "b.py", 0) for i in range(3)
    ]
    clusters = [GraphCluster(cluster_id=0, label="widget", member_count=6)]
    ctx = GraphContext(nodes, [], clusters, AuditorSettings())
    assert ScatteredConcept(ctx).detect() == []


def test_run_graph_detectors_groups_by_path():
    hub = _fn("m.py::hub", "hub")
    targets = [_fn(f"m.py::t{i}", f"t{i}") for i in range(40)]
    edges = [GraphEdge(src=hub.id, dst=t.id, kind=EdgeKind.CALLS) for t in targets]
    per_file = run_graph_detectors([hub, *targets], edges, [], AuditorSettings())
    assert "m.py" in per_file and len(per_file["m.py"]) >= 1


def _pf(nid, name, profile, module="m.py", cid=0):
    return GraphNode(
        id=nid,
        kind=NodeKind.FUNCTION,
        name=name,
        module=module,
        qualname=name,
        role="production",
        cluster_id=cid,
        semantic_profile=tuple(profile),
        rank=0.1,
        line=1,
    )


def test_naming_inconsistency_flags_synonym_verbs():
    # get_* and fetch_* behave identically (same profile) -> synonyms; same object 'user'
    read = ("returns_value", "no_params")
    nodes = [_pf(f"m.py::get_user{i}", "get_user", read) for i in range(20)] + [
        _pf(f"m.py::fetch_user{i}", "fetch_user", read) for i in range(20)
    ]
    # make the specific same-object pair share object tokens
    nodes.append(_pf("m.py::get_user", "get_user", read))
    nodes.append(_pf("m.py::fetch_user", "fetch_user", read))
    found = NamingInconsistency(GraphContext(nodes, [], [], AuditorSettings())).detect()
    assert any("get" in f.message and "fetch" in f.message for _, f in found)


def test_naming_inconsistency_ignores_antonym_verbs():
    # get_* (returns) vs delete_* (mutates, no return) -> far apart -> not flagged
    read = ("returns_value", "no_params")
    mutate = ("returns_none", "writes_self")
    nodes = (
        [_pf(f"m.py::get_user{i}", "get_user", read) for i in range(20)]
        + [_pf(f"m.py::delete_user{i}", "delete_user", mutate) for i in range(20)]
        + [
            _pf("m.py::get_user", "get_user", read),
            _pf("m.py::delete_user", "delete_user", mutate),
        ]
    )
    assert (
        NamingInconsistency(GraphContext(nodes, [], [], AuditorSettings())).detect()
        == []
    )


def test_god_concept_log_space_not_suppressed_by_mega_outlier():
    # mega bottleneck (in~80) must not suppress a moderate bottleneck (in~25) via inflated σ
    mega = _fn("m.py::mega", "mega")
    mod = _fn("m.py::mod", "mod")
    leaves = [_fn(f"m.py::l{i}", f"l{i}") for i in range(90)]
    edges = [GraphEdge(src=leaves[i % 90].id, dst=mega.id, kind=EdgeKind.CALLS) for i in range(80)]
    edges += [GraphEdge(src=leaves[(i + 3) % 90].id, dst=mod.id, kind=EdgeKind.CALLS) for i in range(25)]
    res = GodConcept(GraphContext([mega, mod, *leaves], edges, [], AuditorSettings())).detect()
    flagged = {f.evidence for _, f in res}
    assert "m.py::mega" in flagged and "m.py::mod" in flagged
