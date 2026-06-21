import pytest

from auditor.database import IndexStore
from auditor.graph.model import EdgeKind, GraphCluster, GraphEdge, GraphNode, NodeKind
from auditor.graph.viz import build_payload, to_dot


@pytest.fixture
async def store(tmp_path):
    s = await IndexStore.connect(tmp_path / "i.db", repo="r")
    nodes = [
        GraphNode(
            id="m.py::Foo",
            kind=NodeKind.CLASS,
            name="Foo",
            module="m.py",
            qualname="Foo",
            role="production",
            rank=0.4,
            cluster_id=0,
            line=1,
        ),
        GraphNode(
            id="m.py::Foo.bar",
            kind=NodeKind.METHOD,
            name="bar",
            module="m.py",
            qualname="Foo.bar",
            role="production",
            rank=0.1,
            cluster_id=0,
            line=3,
        ),
        GraphNode(
            id="m.py",
            kind=NodeKind.MODULE,
            name="m.py",
            module="m.py",
            qualname="m",
            role="production",
            rank=0.0,
            cluster_id=None,
            line=1,
        ),
    ]
    edges = [GraphEdge(src="m.py::Foo", dst="m.py::Foo.bar", kind=EdgeKind.CONTAINS, weight=1.0)]
    clusters = [GraphCluster(cluster_id=0, label="foo", member_count=2)]
    await s.repos.register(0.0)
    await s.graph.replace(nodes, edges, clusters)
    yield s
    await s.aclose()


async def test_payload_shape_and_mapping(store):
    p = await build_payload(store)
    assert set(p) == {"meta", "clusters", "nodes", "edges"}
    by_id = {n["id"]: n for n in p["nodes"]}
    assert by_id["m.py::Foo"]["type"] == "class"
    assert by_id["m.py::Foo.bar"]["type"] == "method"
    assert by_id["m.py"]["type"] == "module"
    foo = by_id["m.py::Foo"]
    assert {"id", "label", "type", "lang", "module", "line", "rank", "cluster", "role", "findings"} <= set(foo)
    assert p["edges"][0] == {"source": "m.py::Foo", "target": "m.py::Foo.bar", "kind": "contains", "weight": 1.0}
    assert p["clusters"][0]["label"] == "foo"


async def test_payload_deterministic_sorted(store):
    a = await build_payload(store)
    b = await build_payload(store)
    assert a == b
    assert [n["id"] for n in a["nodes"]] == sorted(n["id"] for n in a["nodes"])


async def test_payload_node_cap(store):
    p = await build_payload(store, node_cap=2)
    assert len(p["nodes"]) <= 2
    assert p["meta"]["node_cap"] == 2


async def test_to_dot_deterministic(store):
    p = await build_payload(store)
    d1 = to_dot(p)
    d2 = to_dot(p)
    assert d1 == d2
    assert d1.startswith("digraph") and "m.py::Foo" in d1
    assert '"m.py::Foo" -> "m.py::Foo.bar"' in d1


async def test_to_dot_symbol_ego(store):
    p = await build_payload(store)
    d = to_dot(p, symbol="Foo", depth=1)
    assert "Foo" in d


async def test_to_dot_cluster_filter(store):
    p = await build_payload(store)
    d = to_dot(p, cluster="foo")
    assert "m.py::Foo" in d
    assert "m.py::Foo.bar" in d


async def test_to_dot_overview_sorted(store):
    p = await build_payload(store)
    d = to_dot(p)
    lines = d.splitlines()
    node_lines = [ln.strip() for ln in lines if ln.strip().startswith('"') and "->" not in ln]
    node_ids = [ln.split('"')[1] for ln in node_lines]
    assert node_ids == sorted(node_ids)
