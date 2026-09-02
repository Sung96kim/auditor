"""The retrieval gate (spec section 22): what ranked search recovers from this repo's own graph.

Builds the graph once, then scores the hand-labeled queries in ``data/retrieval_queries.json``
with the shipped ``GraphQuery.search``. Two bars, because one cannot do both jobs: absolute
recall and MRR carry drift headroom and catch a collapse, and a per-query snapshot carries none
and catches a regression one query wide. Marked ``slow`` because that one build is about 50 s;
CI runs the whole suite, `-m "not slow"` drops it locally.
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
#: (b) tf-idf, a comparison this slice does not run. Absolute recall rather than a margin, because
#: the substring scan scores 0 at every k on this fixture by construction, which
#: `test_the_substring_scan_answers_none_of_these_queries` keeps true. Measured here: 15.0 at k=5
#: (6 of 40 queries) and 32.5 at k=20 (13 of 40). The floors are those minus two and three queries
#: of drift headroom, at 2.5 points per query on n = 40, so they are a drift alarm and not a
#: correctness gate: one query of headroom is wider than the whole effect of a weighting step.
RECALL_FLOOR = {5: 10.0, 20: 25.0}
#: measured 0.125; section 22 asks for MRR beside recall@5, so the gate pins it too
MRR_FLOOR = 0.08
#: the queries whose labelled answer a 20-row ranked page holds, measured at HEAD. This is the
#: sensitivity half of the gate and carries no headroom on purpose: it fails when any one of them
#: stops being answered, which the floors above cannot see. A gain is allowed; a loss is either a
#: regression or a corpus shift, and a corpus shift is re-measured in the commit that causes it.
RECOVERED_AT_20 = (
    "compute the Wilson lower confidence bound for a proportion",
    "append-only file the hook writes events into when the daemon is down",
    "is this checkout the primary one rather than a linked copy",
    "how much money is left to spend today",
    "stop two processes from rebuilding at the same moment",
    "add a missing column to a table that already exists",
    "which other symbols reach this one and which it reaches",
    "check a proposed answer against what the parser actually saw",
    "the instructions handed to the model before it answers",
    "the test that pins the confidence bound arithmetic to the numbers the spec lists",
    "the test proving a half-written line from a killed process is skipped instead of raised",
    "the test that the give-up error carries the lock it waited on and the budget it spent",
)


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


def test_the_substring_scan_answers_none_of_these_queries():
    """Why the floors are absolute recall: every query is a sentence and no node id holds a space.

    The scan `search` replaced therefore scores 0 at every k here, so a margin against it would be
    the recall itself wearing another name. Adding a single-word query would break that.
    """
    assert all(" " in item["q"] for item in QUERIES)


async def _pages(repo_query: GraphQuery) -> list[tuple[dict, list[str]]]:
    """Each fixture query beside the 20-row page the shipped surface answers it with."""
    return [
        (item, [r.id for r in (await repo_query.search(item["q"], limit=20)).root])
        for item in QUERIES
    ]


async def test_ranked_search_clears_the_recall_and_mrr_floors(repo_query: GraphQuery):
    """The drift alarm: absolute recall at two depths and MRR, each with headroom."""
    hits = {k: 0 for k in RECALL_FLOOR}
    reciprocal = 0.0
    for item, found in await _pages(repo_query):
        gold = set(item["gold"])
        for k in hits:
            hits[k] += bool(gold & set(found[:k]))
        reciprocal += next(
            (1 / i for i, nid in enumerate(found, 1) if nid in gold), 0.0
        )
    n = len(QUERIES)
    recall = {k: v / n * 100 for k, v in hits.items()}
    mrr = reciprocal / n
    assert (
        all(recall[k] >= RECALL_FLOOR[k] for k in RECALL_FLOOR) and mrr >= MRR_FLOOR
    ), (
        f"recall {recall} against {RECALL_FLOOR} and MRR {mrr:.3f} against {MRR_FLOOR}, "
        f"over {n} queries; counts {hits}"
    )


async def test_every_question_this_fit_could_answer_is_still_answered(
    repo_query: GraphQuery,
):
    """The sensitivity half: per query, so a lost answer cannot be paid for by a gained one.

    Recall counts queries and the floors budget three of them, which is wider than the effect of
    deleting a weighting step; a named query that stops being answered is not. A failure here
    means the corpus moved: re-measure and update ``RECOVERED_AT_20`` in the commit that moved
    it, rather than widening this assertion.
    """
    recovered = {
        item["q"]
        for item, found in await _pages(repo_query)
        if set(item["gold"]) & set(found)
    }
    lost = sorted(set(RECOVERED_AT_20) - recovered)
    assert not lost, (
        f"{len(lost)} of {len(RECOVERED_AT_20)} answers left the page: {lost} "
        "(see this test's docstring to re-measure RECOVERED_AT_20 rather than widen this)"
    )
