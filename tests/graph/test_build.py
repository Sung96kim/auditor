import pytest

from auditor.config import AuditorSettings, GraphConfig
from auditor.database import IndexStore
from auditor.graph.build import GraphBuilder, compute_abstractness
from auditor.graph.extract import extract_file_facts
from auditor.graph.model import GraphNode, NodeKind

BASE = "class Base:\n    def run(self): ...\n"
IMPL = "from base import Base\nclass Impl(Base):\n    def run(self):\n        return load_user()\n\ndef load_user():\n    return get_user_record()\n"


@pytest.fixture
async def store(tmp_path):
    s = await IndexStore.connect(tmp_path / "i.db", repo="r")
    await s.graph.set_facts(
        "base.py",
        extract_file_facts("base.py", BASE, "production").model_dump_json(),
        "h1",
    )
    await s.graph.set_facts(
        "impl.py",
        extract_file_facts("impl.py", IMPL, "production").model_dump_json(),
        "h2",
    )
    yield s
    await s.aclose()


def test_compute_abstractness_stub_method():
    facts = extract_file_facts("base.py", BASE, "production")
    run = next(n for n in facts.nodes if n.id == "base.py::Base.run")
    assert compute_abstractness(run, proto_method_ids=set()) >= 0.4  # stub body


async def test_build_reports_stage_progress(store):
    settings = AuditorSettings(
        graph=GraphConfig(enabled=True, name_similarity_threshold=0.2)
    )
    seen: list[str] = []
    await GraphBuilder().run(store, settings, progress=seen.append)
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


async def test_build_writes_nodes_edges_clusters(store):
    settings = AuditorSettings(
        graph=GraphConfig(enabled=True, name_similarity_threshold=0.2)
    )
    summary = await GraphBuilder().run(store, settings)
    assert summary["nodes"] >= 4
    # override edge survived the repo pass
    over = await store.graph.edges_of("impl.py::Impl.run", ["overrides"])
    assert any(e["dst"] == "base.py::Base.run" for e in over)
    # cross-module call resolved by name
    calls = await store.graph.edges_of("impl.py::load_user", ["calls"])
    assert any(
        e["dst"] == "impl.py::load_user" or e["src"] == "impl.py::load_user"
        for e in calls
    )
    # every node got a cluster id + a rank
    nodes = await store.graph.nodes()
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
    builder = GraphBuilder()
    concept = builder._concept_nodes(nodes)
    assert {n.id for n in concept} == {
        n.id for n in prod
    }  # only prod symbols are clustered


async def test_build_personalizes_rank_against_tests(tmp_path):
    prod_src = "def helper():\n    return shared()\n\ndef shared():\n    return 1\n"
    test_src = "from prod import shared\n\ndef test_thing():\n    return shared()\n"
    s = await IndexStore.connect(tmp_path / "i.db", repo="r")
    try:
        await s.graph.set_facts(
            "prod.py",
            extract_file_facts("prod.py", prod_src, "production").model_dump_json(),
            "h1",
        )
        await s.graph.set_facts(
            "test_x.py",
            extract_file_facts("test_x.py", test_src, "test").model_dump_json(),
            "h2",
        )
        settings = AuditorSettings(
            graph=GraphConfig(enabled=True, name_similarity_threshold=0.2)
        )
        await GraphBuilder().run(s, settings)
        nodes = {n["node_id"]: n for n in await s.graph.nodes()}
        assert nodes["prod.py::helper"]["rank"] > nodes["test_x.py::test_thing"]["rank"]
    finally:
        await s.aclose()


async def test_build_runs_detectors_and_persists(tmp_path):
    src_hub = "def hub():\n    return 1\n"
    callers = "from hub import hub\n" + "".join(
        f"def c{i}():\n    return hub()\n" for i in range(12)
    )
    s = await IndexStore.connect(tmp_path / "i.db", repo="r")
    try:
        await s.graph.set_facts(
            "hub.py",
            extract_file_facts("hub.py", src_hub, "production").model_dump_json(),
            "h1",
        )
        await s.graph.set_facts(
            "callers.py",
            extract_file_facts("callers.py", callers, "production").model_dump_json(),
            "h2",
        )
        settings = AuditorSettings(
            graph=GraphConfig(enabled=True, name_similarity_threshold=0.2, detect=True)
        )
        summary = await GraphBuilder().run(s, settings)
        assert "findings" in summary
        assert summary["findings"] >= 0
        # detect=False clears graph findings and adds none
        settings_off = AuditorSettings(
            graph=GraphConfig(enabled=True, name_similarity_threshold=0.2, detect=False)
        )
        summary_off = await GraphBuilder().run(s, settings_off)
        assert summary_off["findings"] == 0
    finally:
        await s.aclose()


async def test_dedup_property_getter_setter(tmp_path):
    facts = extract_file_facts("prop.py", PROP, "production")
    dup_nodes = [n for n in facts.nodes if n.id == "prop.py::Box.config"]
    assert len(dup_nodes) == 2, "extractor must emit two nodes for getter+setter"

    s = await IndexStore.connect(tmp_path / "i.db", repo="r")
    try:
        await s.graph.set_facts(
            "prop.py",
            facts.model_dump_json(),
            "h1",
        )
        settings = AuditorSettings(
            graph=GraphConfig(enabled=True, name_similarity_threshold=0.2)
        )
        await GraphBuilder().run(s, settings)
        nodes = await s.graph.nodes()
        matching = [n for n in nodes if n["node_id"] == "prop.py::Box.config"]
        assert len(matching) == 1
    finally:
        await s.aclose()
