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


def test_god_concept_flags_rank_outlier():
    # one hub with huge rank, nineteen ordinary nodes (20 total so the outlier is
    # comfortably above mean+3σ in log space)
    nodes = [_fn("m.py::hub", "hub", rank=1.0)] + [
        _fn(f"m.py::f{i}", f"f{i}", rank=0.01) for i in range(19)
    ]
    results = GodConcept(GraphContext(nodes, [], [], AuditorSettings())).detect()
    assert len(results) == 1
    path, finding = results[0]
    assert path == "m.py"
    assert "hub" in finding.evidence
    assert finding.rule_id == "GRAPH-GOD-CONCEPT"
    assert finding.verdict_kind.value == "candidate"


def test_god_concept_ignores_tests():
    nodes = [_fn("t.py::big", "big", role="test", rank=1.0)] + [
        _fn(f"m.py::f{i}", f"f{i}", rank=0.01) for i in range(19)
    ]
    # a test-role hub must not be flagged
    assert GodConcept(GraphContext(nodes, [], [], AuditorSettings())).detect() == []


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
    nodes = [_fn("m.py::hub", "hub", rank=1.0)] + [
        _fn(f"m.py::f{i}", f"f{i}", rank=0.01) for i in range(19)
    ]
    per_file = run_graph_detectors(nodes, [], [], AuditorSettings())
    assert "m.py" in per_file and len(per_file["m.py"]) == 1


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
    # one degree-100 mega-hub + one moderate degree-~20 hub + many degree-1 leaves.
    # Under raw mean+3σ, the mega-hub inflates σ so much that the moderate hub falls
    # below the floor (false negative). Log-space floors catch both.
    mega = _fn("m.py::mega", "mega")
    mod = _fn("m.py::mod", "mod")
    leaves = [_fn(f"m.py::l{i}", f"l{i}") for i in range(60)]
    edges = []
    for i in range(50):  # mega: 50 incoming
        edges.append(GraphEdge(src=leaves[i % 60].id, dst=mega.id, kind=EdgeKind.CALLS))
    for i in range(20):  # mod: 20 incoming
        edges.append(GraphEdge(src=leaves[(i + 5) % 60].id, dst=mod.id, kind=EdgeKind.CALLS))
    nodes = [mega, mod, *leaves]
    res = GodConcept(GraphContext(nodes, edges, [], AuditorSettings())).detect()
    flagged = {f.evidence for _, f in res}
    assert "m.py::mega" in flagged  # the mega hub
    assert "m.py::mod" in flagged  # the moderate hub must NOT be suppressed by mega's σ


def test_god_concept_message_splits_by_signal():
    # degree hub: many callers -> "hub"/"decompos" wording
    hub = _fn("m.py::hub", "hub")
    callers = [_fn(f"m.py::c{i}", f"c{i}") for i in range(12)]
    edges = [GraphEdge(src=c.id, dst=hub.id, kind=EdgeKind.CALLS) for c in callers]
    res = GodConcept(GraphContext([hub, *callers], edges, [], AuditorSettings())).detect()
    hub_msg = next(f.message for _, f in res if f.evidence == "m.py::hub")
    assert "hub" in hub_msg and "decompos" in hub_msg

    # rank-central, low degree (no edges) -> "central"/"blast-radius" wording
    # (19 peers so the outlier clears mean+3σ in log space)
    nodes = [_fn("m.py::central", "central", rank=1.0)] + [
        _fn(f"m.py::f{i}", f"f{i}", rank=0.01) for i in range(19)
    ]
    res2 = GodConcept(GraphContext(nodes, [], [], AuditorSettings())).detect()
    msg = next(f.message for _, f in res2 if f.evidence == "m.py::central")
    assert "central" in msg and "blast-radius" in msg
