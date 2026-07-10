import json

import pytest

from auditor.database import IndexStore
from auditor.graph.model import EdgeKind, GraphCluster, GraphEdge, GraphNode, NodeKind
from auditor.graph.viz import build_payload, render_app


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
    edges = [
        GraphEdge(
            src="m.py::Foo", dst="m.py::Foo.bar", kind=EdgeKind.CONTAINS, weight=1.0
        )
    ]
    clusters = [GraphCluster(cluster_id=0, label="foo", member_count=2)]
    await s.repos.register(0.0)
    await s.graph.replace(nodes, edges, clusters)
    yield s
    await s.aclose()


async def test_render_app_injects_payload(store):
    payload = await build_payload(store)
    html = render_app(payload)
    assert "__AUDITOR_GRAPH__" in html
    assert '"m.py::Foo"' in html  # the data is embedded
    assert html.strip().lower().startswith("<!doctype html") or "<html" in html.lower()
    # the injected JSON round-trips
    assert html.index("__AUDITOR_GRAPH__") > 0
    assert json.dumps(payload["meta"]["accent"]) in html
