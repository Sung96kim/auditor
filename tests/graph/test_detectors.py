from auditor.config import AuditorSettings
from auditor.graph.detectors import (
    GodConcept,
    GraphContext,
    ScatteredConcept,
    run_graph_detectors,
)
from auditor.graph.model import GraphCluster, GraphNode, NodeKind


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
    # one hub with huge rank, nine ordinary nodes
    nodes = [_fn("m.py::hub", "hub", rank=1.0)] + [
        _fn(f"m.py::f{i}", f"f{i}", rank=0.01) for i in range(9)
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
        _fn(f"m.py::f{i}", f"f{i}", rank=0.01) for i in range(9)
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
        _fn(f"m.py::f{i}", f"f{i}", rank=0.01) for i in range(9)
    ]
    per_file = run_graph_detectors(nodes, [], [], AuditorSettings())
    assert "m.py" in per_file and len(per_file["m.py"]) == 1
