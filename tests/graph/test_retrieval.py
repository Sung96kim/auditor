"""The retrieval gate (spec section 22): ranked search must beat the substring scan it replaced.

Builds this repo's own graph once, then scores the hand-labeled queries in
``data/retrieval_queries.json`` two ways: the pre-slice substring scan, and the shipped
``GraphQuery.search``. The margins between them are what this slice ships on. Marked ``slow``
because that one build is about 50 s; CI runs the whole suite, `-m "not slow"` drops it locally.
"""

import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from auditor.database import IndexStore
from auditor.graph.model import TEST_ROLES
from auditor.graph.query import GraphQuery
from auditor.paths import partition_for, repo_key

pytestmark = pytest.mark.slow

REPO = Path(__file__).resolve().parents[2]
QUERIES = json.loads(
    (Path(__file__).parent / "data" / "retrieval_queries.json").read_text()
)
#: a cold build of this repo is about 50 s; the CLI waits on its rebuild lock without a timeout,
#: so this is the only bound on the wait, and the lock lives in the throwaway home below
BUILD_TIMEOUT = 600.0
#: this slice's own bar, not spec section 22's: section 22's 10 points gate (c) embeddings against
#: (b) tf-idf, a comparison this slice does not run. Measured here: +17.5 at k=5 (7 of 40 queries)
#: and +30.0 at k=20 (12 of 40), against a substring baseline of 0 at every k. The floors are those
#: minus roughly three and two queries of drift headroom, at 2.5 points per query on n = 40.
FLOOR = {5: 10.0, 20: 25.0}
#: measured 0.128; section 22 asks for MRR beside recall@5, so the gate pins it too
MRR_FLOOR = 0.08


@pytest.fixture(scope="session")
def repo_index_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """This repo's own graph, built once into a throwaway ``AUDITOR_HOME``.

    Out of process so the build's ``AUDITOR_HOME`` never leaks into the rest of the session; the
    tests reconnect to the database by path. That home also owns the rebuild lock the build takes,
    so no daemon on the developer's real home can hold it.
    """
    home = tmp_path_factory.mktemp("retrieval-home")
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            "from auditor.cli import app; app()",
            "graph",
            "build",
            str(REPO),
        ],
        cwd=REPO,
        env={**os.environ, "AUDITOR_HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT,
    )
    assert done.returncode == 0, done.stderr or done.stdout
    return home / "index.db"


@pytest.fixture
async def repo_query(repo_index_db: Path) -> AsyncIterator[GraphQuery]:
    store = await IndexStore.connect(repo_index_db, repo_key(REPO), partition_for(REPO))
    yield GraphQuery(store)
    await store.aclose()


async def test_every_labeled_answer_is_a_node_the_fit_can_still_reach(
    repo_query: GraphQuery,
):
    """A gold id that moved, or that the fit does not cover, makes the gate measure nothing."""
    known = {n["node_id"] for n in await repo_query.index.graph.nodes()}
    model = await repo_query.index.graph.text_model()
    assert model is not None
    reachable = known & set(model.node_ids)
    lost = {
        q["q"]: [
            f"{g} ({'no such node' if g not in known else 'outside the fit'})"
            for g in q["gold"]
            if g not in reachable
        ]
        for q in QUERIES
    }
    assert {q: g for q, g in lost.items() if g} == {}


async def test_the_fixture_asks_for_test_nodes_as_well_as_production_ones(
    repo_query: GraphQuery,
):
    """A fixture with no test-role answer cannot price any policy that reorders test nodes."""
    roles = {n["node_id"]: n["role"] for n in await repo_query.index.graph.nodes()}
    wanted = [q["q"] for q in QUERIES if any(roles[g] in TEST_ROLES for g in q["gold"])]
    assert len(wanted) >= 5, f"only {len(wanted)} queries whose answer is a test node"


async def test_ranked_search_beats_the_substring_scan_it_replaced(
    repo_query: GraphQuery,
):
    """The gate: ranked recall and MRR must clear the floors this slice measured."""
    ordered = [n["node_id"] for n in await repo_query.index.graph.nodes()]
    hits = {k: {"substring": 0, "search": 0} for k in FLOOR}
    reciprocal = 0.0
    for item in QUERIES:
        gold = set(item["gold"])
        substring = [n for n in ordered if item["q"].lower() in n.lower()]
        found = [r.id for r in (await repo_query.search(item["q"], limit=20)).root]
        for k in hits:
            hits[k]["substring"] += bool(gold & set(substring[:k]))
            hits[k]["search"] += bool(gold & set(found[:k]))
        reciprocal += next(
            (1 / i for i, nid in enumerate(found, 1) if nid in gold), 0.0
        )
    n = len(QUERIES)
    margins = {k: (v["search"] - v["substring"]) / n * 100 for k, v in hits.items()}
    mrr = reciprocal / n
    assert all(margins[k] >= FLOOR[k] for k in FLOOR) and mrr >= MRR_FLOOR, (
        f"recall margins {margins} against {FLOOR} and MRR {mrr:.3f} against {MRR_FLOOR}, "
        f"over {n} queries; counts {hits}"
    )
