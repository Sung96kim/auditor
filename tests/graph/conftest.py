"""Graph test scaffolding: one connected ``IndexStore`` per data shape, and the one-module repo
the CLI and MCP tests scan. Fixture-only by design: a sibling ``conftest`` cannot be imported by
name without colliding with the root ``tests/conftest.py``."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from auditor.database import IndexStore
from auditor.graph.extract import extract_file_facts
from auditor.graph.model import EdgeKind, GraphCluster, GraphEdge, GraphNode, NodeKind

GRAPH_CONFIG = "[tool.auditor.graph]\nenabled=true\nname_similarity_threshold=0.2\n"
SIMILAR_NAMES = (
    "def get_user(uid):\n    return db.fetch(uid)\n\n"
    "def fetch_user(uid):\n    return db.fetch(uid)\n"
)
RESOLVABLE_CALLS = (
    "def get_user(uid):\n    return uid\n\n"
    "def fetch_user(uid):\n    return uid\n\n"
    "def load_user(uid):\n    return get_user(uid) or fetch_user(uid)\n"
)
BASE_SRC = "class Base:\n    def run(self): ...\n"
# `load_user` lives in a third module on purpose: `Impl.run` calling it is a real cross-module miss
IMPL_SRC = (
    "from base import Base\nclass Impl(Base):\n    def run(self):\n"
    "        return load_user() or _local()\n\ndef _local():\n    return 1\n"
)
SVC_SRC = "def load_user():\n    return get_user_record()\n"


def _write_graph_repo(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    module_source: str = SIMILAR_NAMES,
    graph_config: str = GRAPH_CONFIG,
) -> Path:
    """A one-module repo (pyproject + m.py) with its own AUDITOR_HOME, so no index is shared."""
    monkeypatch.setenv("AUDITOR_HOME", str(root / "home"))
    (root / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n' + graph_config
    )
    (root / "m.py").write_text(module_source)
    return root


@pytest.fixture
def graph_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The default one-module repo: two similarly named functions, graph config on."""
    return _write_graph_repo(tmp_path, monkeypatch)


@pytest.fixture
def graph_repo_unconfigured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No [tool.auditor.graph] section, so a build has to force the scan itself."""
    return _write_graph_repo(tmp_path, monkeypatch, graph_config="")


@pytest.fixture
def graph_repo_with_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Calls that resolve inside the module, so structural neighbors exist to cap."""
    return _write_graph_repo(tmp_path, monkeypatch, module_source=RESOLVABLE_CALLS)


@pytest.fixture
async def graph_store(tmp_path: Path) -> AsyncIterator[IndexStore]:
    """An empty index bound to repo ``r``, the base every other store fixture builds on."""
    store = await IndexStore.connect(tmp_path / "i.db", repo="r")
    yield store
    await store.aclose()


@pytest.fixture
async def query_store(graph_store: IndexStore) -> IndexStore:
    """Three ranked functions across two clusters, one semantic and one structural edge."""
    nodes = [
        GraphNode(
            id="m.py::get_user",
            kind=NodeKind.FUNCTION,
            name="get_user",
            module="m.py",
            qualname="get_user",
            rank=0.5,
            cluster_id=1,
        ),
        GraphNode(
            id="m.py::fetch_user",
            kind=NodeKind.FUNCTION,
            name="fetch_user",
            module="m.py",
            qualname="fetch_user",
            rank=0.3,
            cluster_id=1,
        ),
        GraphNode(
            id="m.py::charge",
            kind=NodeKind.FUNCTION,
            name="charge",
            module="m.py",
            qualname="charge",
            rank=0.2,
            cluster_id=2,
        ),
    ]
    edges = [
        GraphEdge(
            src="m.py::fetch_user",
            dst="m.py::get_user",
            kind=EdgeKind.NAME_SIMILAR,
            weight=0.8,
        ),
        GraphEdge(src="m.py::get_user", dst="m.py::charge", kind=EdgeKind.CALLS),
    ]
    clusters = [
        GraphCluster(cluster_id=1, label="user", member_count=2),
        GraphCluster(cluster_id=2, label="charge", member_count=1),
    ]
    await graph_store.graph.replace(nodes, edges, clusters)
    return graph_store


@pytest.fixture
async def viz_store(graph_store: IndexStore) -> IndexStore:
    """A class, its method and their module, plus the repos row ``build_payload`` reads."""
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
    await graph_store.repos.register(0.0)
    await graph_store.graph.replace(nodes, edges, clusters)
    return graph_store


@pytest.fixture
async def facts_store(graph_store: IndexStore) -> IndexStore:
    """Cached facts for a base/impl/svc trio, so ``GraphBuilder.run`` has something to build and
    exactly one call it cannot place: ``Impl.run`` calls ``svc.load_user`` without importing it."""
    for path, src, digest in (
        ("base.py", BASE_SRC, "h1"),
        ("impl.py", IMPL_SRC, "h2"),
        ("svc.py", SVC_SRC, "h3"),
    ):
        await graph_store.graph.set_facts(
            path,
            extract_file_facts(path, src, "production").model_dump_json(),
            digest,
        )
    return graph_store
