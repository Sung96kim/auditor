"""Graph test scaffolding: one connected ``IndexStore`` per data shape, and the one-module repo
the CLI and MCP tests scan. Fixture-only by design: a sibling ``conftest`` cannot be imported by
name without colliding with the root ``tests/conftest.py``."""

import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from graph._support import ClaudeShaped, CodexShaped
from pydantic import BaseModel, ConfigDict
from typer.testing import CliRunner

from auditor.cli import app
from auditor.config import AuditorSettings
from auditor.database import IndexStore
from auditor.graph.build import GraphBuilder
from auditor.graph.extract import extract_file_facts
from auditor.graph.hashes import file_hashes
from auditor.graph.model import EdgeKind, GraphCluster, GraphEdge, GraphNode, NodeKind
from auditor.graph.refine import drive
from auditor.graph.refine.models import (
    Refinement,
    RefinementKind,
    RefinementStatus,
    RefinementTarget,
    Run,
    RunnerKind,
)
from auditor.graph.refine.service import RefinementService, RunRegistry
from auditor.user_settings import UserSettings

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
# a three-link chain inside one module, so a flow query has something to walk end to end
FLOW_CALLS = (
    "def leaf(uid):\n    return uid\n\n"
    "def middle(uid):\n    return leaf(uid)\n\n"
    "def entry(uid):\n    return middle(uid)\n"
)


# leaf is called from two places, so a floor of 2 makes it a hub; the second module is what
# --stop-at cuts, and the test-role caller is what --include-tests adds
HUB_MAIN = (
    "from svc import leaf\n\n"
    "def middle(uid):\n    return leaf(uid)\n\n"
    "def other(uid):\n    return leaf(uid)\n\n"
    "def entry(uid):\n    return middle(uid) or other(uid)\n"
)
HUB_SVC = "def leaf(uid):\n    return uid\n"
HUB_TEST = "from m import entry\n\ndef test_entry():\n    return entry(1)\n"

# `spread` calls twelve leaves and nothing calls it; `sink` is called by twelve and calls nothing,
# so one repo trips both GOD-CONCEPT centralities and neither masks the other
GOD_CONCEPTS = (
    "".join(f"def leaf{i}(uid):\n    return uid\n\n" for i in range(12))
    + "def sink(uid):\n    return uid\n\n"
    + "def spread(uid):\n"
    + "".join(f"    leaf{i}(uid)\n" for i in range(12))
    + "    return uid\n\n"
    + "".join(f"def caller{i}(uid):\n    return sink(uid)\n\n" for i in range(12))
)


# six documents with real prose, so tf-idf has a vocabulary and LSI has components to keep: the
# one-module fixtures are two functions wide, which floors `n_components` below 2 and fits nothing
TEXT_MAIN = (
    "def read_user_account(uid):\n"
    '    """Load the stored account profile for one user record."""\n'
    "    return uid\n\n"
    "def fetch_user_profile(uid):\n"
    '    """Fetch the saved profile record belonging to a user account."""\n'
    "    return uid\n\n"
    "def charge_invoice_payment(amount):\n"
    '    """Charge a payment against an outstanding invoice balance."""\n'
    "    return amount\n\n"
    "def refund_invoice_payment(amount):\n"
    '    """Refund a payment already charged to an invoice balance."""\n'
    "    return amount\n\n"
    "def render_status_line(text):\n"
    '    """Draw the one line summary shown in the editor status bar."""\n'
    "    return text\n\n"
    "def compress_status_counts(counts):\n"
    '    """Shorten the counts drawn in the editor summary line."""\n'
    "    return counts\n"
)


#: a bare call the resolver cannot place: `caller.main` calls a name `helper` defines
QUEUED_HELPER = "def read_event():\n    return {}\n"
QUEUED_CALLER = "def main():\n    return read_event()\n"


# every rule the add suite's ground truth applies, in one package: a same-module bare call, a
# direct-import call, a re-exported call that lands in `neither`, a `self` method call, a name the
# node binds itself, an attribute call, a definer only a test file has, and an externally bound row
EVAL_MAIN = (
    "import re\n"
    "from lib import direct_target\n"
    "from pkg import reexported\n"
    "from tests.stub import only_in_tests\n\n"
    "_RX = re.compile('x')\n\n"
    "def same_target(uid):\n    return uid\n\n"
    "def calls_same(uid):\n    return same_target(uid)\n\n"
    "def calls_direct(uid):\n    return direct_target(uid)\n\n"
    "def calls_reexported(uid):\n    return reexported(uid)\n\n"
    "def calls_test_only(uid):\n    return only_in_tests(uid)\n\n"
    "def binds_then_calls(uid):\n"
    "    def shadowed(x):\n        return x\n"
    "    return shadowed(uid)\n\n"
    "def attribute_call(job):\n    return job.handle()\n\n"
    "def externally_bound_call(text):\n    return _RX.match(text)\n\n"
    "class Holder:\n"
    "    def helper(self):\n        return 1\n\n"
    "    def uses(self):\n        return self.helper()\n"
)
EVAL_LIB = "def direct_target(uid):\n    return uid\n"
# `match` lives where `m.py` cannot import it, so the call on a receiver aliased from `re` stays
# unresolved with a definer to name: the shape of a collision row
EVAL_OTHER = "def match(text):\n    return text\n"
EVAL_PKG_INIT = "from pkg.deep import reexported\n"
EVAL_PKG_DEEP = "def reexported(uid):\n    return uid\n"
# `uses_same` resolves to a test-role `same_target` whose one role-filtered definer is elsewhere
EVAL_TEST_STUB = (
    "def only_in_tests(uid):\n    return uid\n\n"
    "def same_target(uid):\n    return uid\n\n"
    "def uses_same(uid):\n    return same_target(uid)\n"
)
# an attribute call that does resolve, so only the call-form filter can leave it out
EVAL_ATTRS = (
    "from m import Holder\n\n_H = Holder()\n\ndef via_attr():\n    return _H.helper()\n"
)
# Bound from non-repo import and defined here; externally-bound rule excludes it.
EVAL_EXTBOUND = (
    "from re import escape\n\n"
    "def escape(text):\n    return text\n\n"
    "def calls_escape(text):\n    return escape(text)\n"
)
# a bare call the node binds itself, which `form_for` reports no form for, and nothing else drops
EVAL_SHADOW = (
    "from lib import direct_target\n\n"
    "def shadowed_call(direct_target):\n    return direct_target(1)\n"
)
EVAL_POPULATION = {
    # the `refine_repo` pair as well, so one fixture holds both the suites' truths and a tier B
    # queue row for the gate to open or hold shut
    "caller.py": QUEUED_CALLER,
    "helper.py": QUEUED_HELPER,
    "lib.py": EVAL_LIB,
    "other.py": EVAL_OTHER,
    "pkg/__init__.py": EVAL_PKG_INIT,
    "pkg/deep.py": EVAL_PKG_DEEP,
    "tests/stub.py": EVAL_TEST_STUB,
    "attrs.py": EVAL_ATTRS,
    "shadow.py": EVAL_SHADOW,
    "extbound.py": EVAL_EXTBOUND,
}


def _write_graph_repo(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    module_source: str = SIMILAR_NAMES,
    graph_config: str = GRAPH_CONFIG,
    extra_files: dict[str, str] | None = None,
) -> Path:
    """A one-module repo (pyproject + m.py) with its own AUDITOR_HOME, so no index is shared."""
    monkeypatch.setenv("AUDITOR_HOME", str(root / "home"))
    (root / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n' + graph_config
    )
    (root / "m.py").write_text(module_source)
    for rel, source in (extra_files or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return root


@pytest.fixture
def graph_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The default one-module repo: two similarly named functions, graph config on."""
    return _write_graph_repo(tmp_path, monkeypatch)


@pytest.fixture
def graph_repo_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repo wide enough to fit: six documented functions across three vocabularies."""
    return _write_graph_repo(tmp_path, monkeypatch, module_source=TEXT_MAIN)


@pytest.fixture
def graph_repo_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A linked worktree: the one shape where `repo_key(root)` and the git common dir differ.

    Every other fixture is a bare directory, where the two candidate identities are the same
    string, so nothing there can tell a tool that binds the wrong one.
    """
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    main = tmp_path / "main"
    main.mkdir()
    (main / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n' + GRAPH_CONFIG
    )
    (main / "m.py").write_text(SIMILAR_NAMES)
    for args in (
        ("init", "-q", "."),
        ("config", "user.email", "t@t"),
        ("config", "user.name", "t"),
        ("add", "-A"),
        ("commit", "-qm", "init"),
        ("worktree", "add", "-q", str(tmp_path / "wt"), "-b", "wt"),
    ):
        subprocess.run(["git", *args], cwd=main, check=True, capture_output=True)
    return tmp_path / "wt"


@pytest.fixture
def graph_repo_unconfigured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No [tool.auditor.graph] section, so a build has to force the scan itself."""
    return _write_graph_repo(tmp_path, monkeypatch, graph_config="")


@pytest.fixture
def graph_repo_with_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Calls that resolve inside the module, so structural neighbors exist to cap."""
    return _write_graph_repo(tmp_path, monkeypatch, module_source=RESOLVABLE_CALLS)


@pytest.fixture
def graph_repo_god_concepts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repo with one pure fan-out hub and one pure bottleneck, so both subkinds are populated."""
    return _write_graph_repo(tmp_path, monkeypatch, module_source=GOD_CONCEPTS)


@pytest.fixture
def graph_repo_eval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The package the eval suites are measured against: every ground-truth rule, once each."""
    return _write_graph_repo(
        tmp_path,
        monkeypatch,
        module_source=EVAL_MAIN,
        extra_files=EVAL_POPULATION,
    )


@pytest.fixture
def graph_repo_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A three-link call chain (entry -> middle -> leaf), the repo the flow surfaces walk."""
    return _write_graph_repo(tmp_path, monkeypatch, module_source=FLOW_CALLS)


@pytest.fixture
def graph_repo_flow_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Two modules, a hub (``leaf``, called twice) and a test caller, with the hub floor at 2 —
    the repo the flow flags are exercised against end to end."""
    return _write_graph_repo(
        tmp_path,
        monkeypatch,
        module_source=HUB_MAIN,
        graph_config=GRAPH_CONFIG + "flow_hub_fan_in=2\n",
        extra_files={"svc.py": HUB_SVC, "tests/test_entry.py": HUB_TEST},
    )


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


_FACTS_FILES = (
    ("base.py", BASE_SRC, "h1"),
    ("impl.py", IMPL_SRC, "h2"),
    ("svc.py", SVC_SRC, "h3"),
)


async def _cache_facts(store: IndexStore, paths: tuple[str, ...]) -> None:
    for path, src, digest in _FACTS_FILES:
        if path in paths:
            facts = extract_file_facts(path, src, "production")
            await store.graph.set_facts(
                path, facts.model_dump_json(), digest, file_hashes(facts.nodes)
            )


@pytest.fixture
async def facts_store(graph_store: IndexStore) -> IndexStore:
    """Cached facts for a base/impl/svc trio, so ``GraphBuilder.run`` has something to build and
    exactly one call it cannot place: ``Impl.run`` calls ``svc.load_user`` without importing it."""
    await _cache_facts(graph_store, tuple(p for p, _, _ in _FACTS_FILES))
    return graph_store


class RefinedStore(BaseModel):
    """A connected store plus the refinement id the fixture inserted into it, so no test has to
    assume its row is the first insert."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    store: IndexStore
    refinement_id: int


@pytest.fixture
async def refined_facts_store(facts_store: IndexStore) -> RefinedStore:
    """`facts_store` plus one active `add_edge` refinement for the call the resolver cannot place:
    `impl.py::Impl.run` calls `load_user`, which lives in `svc.py`."""
    run_id = await facts_store.runs.add_run(
        Run(repo_identity=facts_store.partition.identity, started_at=1.0)
    )
    rid = await facts_store.refinements.add_refinement(
        Refinement(
            run_id=run_id,
            repo_identity=facts_store.partition.identity,
            kind=RefinementKind.ADD_EDGE,
            reason="the call resolves there",
            target=RefinementTarget(
                src="impl.py::Impl.run",
                dst="svc.py::load_user",
                edge_kind=EdgeKind.CALLS,
                name="load_user",  # the queue row this answers (spec 5.7)
            ),
            status=RefinementStatus.ACTIVE,
        )
    )
    return RefinedStore(store=facts_store, refinement_id=rid)


@pytest.fixture
async def half_scanned_refined_store(refined_facts_store: RefinedStore) -> RefinedStore:
    """`refined_facts_store` as a build landing mid-``--rebuild`` sees it: the cache was cleared
    and the rescan has reached `impl.py` but not `svc.py`, the refinement's destination file."""
    await refined_facts_store.store.graph.clear_facts()
    await _cache_facts(refined_facts_store.store, ("base.py", "impl.py"))
    return refined_facts_store


def _write_facts_sources(root: Path) -> Path:
    """The three files behind `facts_store`, on disk, so the verifier can re-read them."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, source in (
        ("base.py", BASE_SRC),
        ("impl.py", IMPL_SRC),
        ("svc.py", SVC_SRC),
    ):
        (root / rel).write_text(source)
    return root


@pytest.fixture
async def refine_service(facts_store: IndexStore, tmp_path: Path) -> RefinementService:
    """A service over `facts_store`'s three files, written to disk and built once, so a proposal
    has both a queue row and a file the verifier can re-extract.

    Its own registry, not the process one: a test must not see what another test staged.
    """
    root = _write_facts_sources(tmp_path / "src")
    settings = AuditorSettings()
    await GraphBuilder().run(facts_store, settings)
    return RefinementService(
        facts_store, root, settings, UserSettings(), registry=RunRegistry()
    )


@pytest.fixture
async def refine_service_other(refine_service: RefinementService) -> RefinementService:
    """A second service over the same index with its own registry: what another MCP process sees."""
    return RefinementService(
        refine_service.index,
        refine_service.root,
        refine_service.settings,
        refine_service.user,
        registry=RunRegistry(),
    )


@pytest.fixture
def refine_repo(graph_repo: Path, process_runs: dict[str, RunRegistry]) -> Path:
    """The one-module repo plus a queue row the refinement surfaces answer, scanned and built.

    One fixture for both surfaces: the CLI and the MCP tests were building the same repo two ways.
    Taking `process_runs` empties every per-identity registry around each test that uses it.
    """
    (graph_repo / "helper.py").write_text(QUEUED_HELPER)
    (graph_repo / "caller.py").write_text(QUEUED_CALLER)
    assert CliRunner().invoke(app, ["graph", "build", str(graph_repo)]).exit_code == 0
    return graph_repo


@pytest.fixture
def eval_repo(graph_repo_eval: Path, process_runs: dict[str, RunRegistry]) -> Path:
    """The eval package, scanned and built, so the suites have a graph to mask edges out of."""
    assert (
        CliRunner().invoke(app, ["graph", "build", str(graph_repo_eval)]).exit_code == 0
    )
    return graph_repo_eval


@pytest.fixture
def process_runs() -> Iterator[dict[str, RunRegistry]]:
    """Every per-identity registry this process shares, emptied around the test.

    Taking the whole map rather than one registry: a test that drives two checkouts gets two, and
    a test that leaked one into the next would be the bug this fixture exists to prevent.
    """
    RunRegistry.PROCESS.clear()
    yield RunRegistry.PROCESS
    RunRegistry.PROCESS.clear()


@pytest.fixture
def claude_runner(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A logged-in machine with the extra installed, driving the scripted Claude-shaped runner.

    One fixture for both surfaces: the CLI and the MCP tests each carried a copy of these same
    three patches, and a fourth would have been written for the next surface.
    """
    monkeypatch.setattr(drive, "SDK_AVAILABLE", True)
    monkeypatch.setattr(drive, "auth_hinted", lambda *a, **k: True)
    monkeypatch.setitem(drive.RUNNERS, RunnerKind.CLAUDE, ClaudeShaped)
    return monkeypatch


@pytest.fixture
def codex_runner(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A logged-in machine with the Codex extra, driving the scripted Codex-shaped runner.

    The twin of `claude_runner`, for the same reason: without it the CLI and the MCP tests would
    each grow their own copy of these three patches.
    """
    monkeypatch.setattr(drive, "CODEX_AVAILABLE", True)
    monkeypatch.setattr(drive, "codex_auth_hinted", lambda *a, **k: True)
    monkeypatch.setitem(drive.RUNNERS, RunnerKind.CODEX, CodexShaped)
    return monkeypatch
