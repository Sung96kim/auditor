from auditor.config import AuditorSettings
from auditor.graph.detectors import GodConcept, GraphContext, run_graph_detectors
from auditor.graph.model import GraphNode, NodeKind


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


def test_run_graph_detectors_groups_by_path():
    nodes = [_fn("m.py::hub", "hub", rank=1.0)] + [
        _fn(f"m.py::f{i}", f"f{i}", rank=0.01) for i in range(9)
    ]
    per_file = run_graph_detectors(nodes, [], [], AuditorSettings())
    assert "m.py" in per_file and len(per_file["m.py"]) == 1
