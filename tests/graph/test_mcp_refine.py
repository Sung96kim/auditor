"""The refinement MCP tools (spec 9.5's in-session producer)."""

import asyncio
import time
from contextlib import suppress
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from auditor.graph.refine.service import RunRegistry
from auditor.mcp import mcp
from auditor.paths import user_config_path
from auditor.user_settings import load_user_settings

HELPER = "def read_event():\n    return {}\n"
CALLER = "def main():\n    return read_event()\n"


def _data(result):
    return result.data if hasattr(result, "data") else result


@pytest.fixture
async def queued_repo(graph_repo: Path, process_runs: dict[str, RunRegistry]) -> Path:
    """The one-module repo plus a bare call the resolver cannot place, scanned and built.

    `process_runs` is every per-identity registry this process shares; taking it here empties them
    around each test instead of this file building one of its own.
    """
    (graph_repo / "helper.py").write_text(HELPER)
    (graph_repo / "caller.py").write_text(CALLER)
    async with Client(mcp) as client:
        await client.call_tool("graph_build", {"path": str(graph_repo)})
    return graph_repo


async def _begin(client, repo: Path, **kw) -> str:
    begun = await client.call_tool("graph_refine_begin", {"path": str(repo), **kw})
    return _data(begun)["run_id"]


def _add_edge(repo: Path, run_id: str) -> dict:
    return {
        "path": str(repo),
        "run_id": run_id,
        "kind": "add_edge",
        "src": "caller.py::main",
        "dst": "helper.py::read_event",
        "edge_kind": "calls",
        "name": "read_event",
        "reason": "main calls read_event, which helper.py defines",
    }


async def test_the_queue_row_this_repo_offers_is_the_one_we_answer(queued_repo: Path):
    async with Client(mcp) as client:
        rows = _data(
            await client.call_tool("graph_unresolved", {"path": str(queued_repo)})
        )
    assert any(
        r["node_id"] == "caller.py::main" and r["name"] == "read_event" for r in rows
    )


async def test_a_proposal_is_staged_then_committed(queued_repo: Path):
    async with Client(mcp) as client:
        run_id = await _begin(client, queued_repo)
        verdict = _data(
            await client.call_tool(
                "graph_refine_propose", _add_edge(queued_repo, run_id)
            )
        )
        assert verdict["outcome"] == "staged"
        assert verdict["verify"] == "ok"
        assert verdict["tier"] == "B"
        assert verdict["status"] == "pending"

        status = _data(
            await client.call_tool(
                "graph_refine_status", {"path": str(queued_repo), "run_id": run_id}
            )
        )
        assert status["staged_here"] is True
        assert len(status["staged"]) == 1
        assert status["rejected"] == []

        result = _data(
            await client.call_tool(
                "graph_refine_commit", {"path": str(queued_repo), "run_id": run_id}
            )
        )
        assert len(result["committed"]) == 1
        assert (result["landed"], result["rebuilt"]) == (1, True)
        assert result["build"]["nodes"] > 0
        assert result["build"]["refined"] == 0  # pending until accepted


async def test_a_run_that_staged_nothing_commits_without_a_build(queued_repo: Path):
    """Spec 6 wants a run's queue rows retired in the same lock as its insert. With no insert there
    is nothing to retire, and a rebuild of a real repo costs about 11.5 s."""
    async with Client(mcp) as client:
        run_id = await _begin(client, queued_repo)
        result = _data(
            await client.call_tool(
                "graph_refine_commit", {"path": str(queued_repo), "run_id": run_id}
            )
        )
        assert (result["landed"], result["rebuilt"], result["build"]) == (
            0,
            False,
            None,
        )
        log = _data(await client.call_tool("graph_log", {"path": str(queued_repo)}))
        assert [r["status"] for r in log["runs"]] == ["succeeded"]


async def test_a_proposal_the_facts_do_not_back_is_rejected_and_recorded(
    queued_repo: Path,
):
    async with Client(mcp) as client:
        run_id = await _begin(client, queued_repo)
        payload = _add_edge(queued_repo, run_id) | {"name": "get_user"}
        verdict = _data(await client.call_tool("graph_refine_propose", payload))
        assert verdict["outcome"] == "rejected"
        assert verdict["refinement_id"] > 0
        rows = _data(
            await client.call_tool(
                "graph_refinements", {"path": str(queued_repo), "status": ["rejected"]}
            )
        )
        assert [r["refinement_id"] for r in rows["rows"]] == [verdict["refinement_id"]]


async def test_a_proposal_with_no_reason_is_stored_as_an_invalid_rejection(
    queued_repo: Path,
):
    """Spec 9.2 wants every rejection stored. `Proposal` owns the reason rule, so the tool hands
    `propose` its raw arguments and the service records the refusal instead of raising."""
    async with Client(mcp) as client:
        run_id = await _begin(client, queued_repo)
        payload = _add_edge(queued_repo, run_id) | {"reason": ""}
        verdict = _data(await client.call_tool("graph_refine_propose", payload))
        assert (verdict["outcome"], verdict["refusal"]) == ("rejected", "invalid")
        assert verdict["verify"] == "unverified"
        assert verdict["refinement_id"] > 0
        assert "needs a reason" in verdict["detail"]
        rows = _data(
            await client.call_tool("graph_refinements", {"path": str(queued_repo)})
        )
        assert [r["status"] for r in rows["rows"]] == ["rejected"]


#: the payload shapes the service cannot turn into a correction at all (spec 9.2)
UNSHAPEABLE: dict[str, dict] = {
    "no target": {"src": None, "dst": None, "edge_kind": None, "name": None},
    "unknown edge kind": {"edge_kind": "callz"},
    "unknown call form": {"call_form": "nope"},
    "malformed evidence": {"evidence": [{"path": "caller.py", "line": "x"}]},
    "confidence off the scale": {"confidence": 99.0},
}


@pytest.mark.parametrize("broken", sorted(UNSHAPEABLE))
async def test_a_payload_that_cannot_be_shaped_is_stored_as_a_rejection(
    queued_repo: Path, broken: str
):
    """Spec 9.2 stores every rejection, and these are the shapes an agent gets wrong: an unknown
    enum used to raise from the model layer instead, so the one rejection worth reading back was
    the one nothing recorded."""
    async with Client(mcp) as client:
        run_id = await _begin(client, queued_repo)
        payload = _add_edge(queued_repo, run_id) | UNSHAPEABLE[broken]
        verdict = _data(await client.call_tool("graph_refine_propose", payload))
        assert (verdict["outcome"], verdict["refusal"]) == ("rejected", "invalid")
        assert verdict["verify"] == "unverified"
        assert verdict["refinement_id"] > 0
        assert verdict["detail"]
        rows = _data(
            await client.call_tool(
                "graph_refinements", {"path": str(queued_repo), "status": ["rejected"]}
            )
        )
        stored = rows["rows"]
        assert [r["refinement_id"] for r in stored] == [verdict["refinement_id"]]
        # Invariant 2: the row a reader can reach belongs to the run that earned it
        assert stored[0]["run_id"] == run_id
        log = _data(
            await client.call_tool(
                "graph_log", {"path": str(queued_repo), "view": "runs"}
            )
        )
        assert [r["run_id"] for r in log["runs"]] == [run_id]


async def test_abort_ends_the_run_and_drops_the_staging(queued_repo: Path):
    async with Client(mcp) as client:
        run_id = await _begin(client, queued_repo)
        await client.call_tool("graph_refine_propose", _add_edge(queued_repo, run_id))
        aborted = _data(
            await client.call_tool(
                "graph_refine_abort",
                {
                    "path": str(queued_repo),
                    "run_id": run_id,
                    "reason": "changed my mind",
                },
            )
        )
        assert aborted["status"] == "aborted"
        with pytest.raises(ToolError, match="not open"):
            await client.call_tool(
                "graph_refine_commit", {"path": str(queued_repo), "run_id": run_id}
            )
        log = _data(await client.call_tool("graph_log", {"path": str(queued_repo)}))
        assert [r["status"] for r in log["runs"]] == ["aborted"]


async def test_the_client_is_recorded_and_an_unknown_one_is_refused(queued_repo: Path):
    """Spec 9.5 records the client, and names Codex as well as Claude Code: a hard-coded value
    would log every Codex run as claude-code."""
    async with Client(mcp) as client:
        begun = _data(
            await client.call_tool(
                "graph_refine_begin", {"path": str(queued_repo), "client": "codex"}
            )
        )
        assert begun["client"] == "codex"
        assert begun["producer"] == "agent"
        with pytest.raises(ToolError, match="unknown client"):
            await client.call_tool(
                "graph_refine_begin", {"path": str(queued_repo), "client": "emacs"}
            )


async def test_an_impossible_scope_is_refused_by_name(queued_repo: Path):
    """A scope that could never name a node here is refused when the run opens, rather than
    silently refusing every proposal the run goes on to make."""
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "graph_refine_begin", {"path": str(queued_repo), "scope": "/etc"}
            )


async def test_the_settings_read_is_off_the_event_loop(
    queued_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    """`load_user_settings` reads two files and, unless it is handed the state dir, shells out to
    git with a 30 s timeout. On the loop every other tool call on the server waits behind it."""
    real = load_user_settings

    def slow(root, *, directory=None):
        time.sleep(0.2)
        return real(root, directory=directory)

    monkeypatch.setattr("auditor.mcp.helpers.load_user_settings", slow)
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.005)

    ticker = asyncio.create_task(tick())
    try:
        async with Client(mcp) as client:
            await _begin(client, queued_repo)
    finally:
        ticker.cancel()
        with suppress(asyncio.CancelledError):
            await ticker
    assert ticks > 5


#: the tools that build a `RefinementService`, and therefore read the user's own settings
SERVICE_TOOLS: dict[str, dict] = {
    "graph_refine_begin": {},
    "graph_refine_propose": {"run_id": "no-such-run", "kind": "confirm_edge"},
    "graph_refine_commit": {"run_id": "no-such-run"},
    "graph_refine_abort": {"run_id": "no-such-run"},
    "graph_refine_status": {"run_id": "no-such-run"},
}


@pytest.mark.parametrize("tool", sorted(SERVICE_TOOLS))
async def test_a_broken_user_config_is_one_line_from_every_tool(
    queued_repo: Path, tool: str
):
    """The preamble covers repo policy; these five read the user's own settings as well, outside
    it, so a bad `~/.auditor/config.json` reached a client as an unhandled pydantic traceback."""
    user_config_path().parent.mkdir(parents=True, exist_ok=True)
    user_config_path().write_text('{"observer": {"limits": {"max_open_runs": "many"}}}')
    async with Client(mcp) as client:
        with pytest.raises(ToolError) as raised:
            await client.call_tool(
                tool, {"path": str(queued_repo), **SERVICE_TOOLS[tool]}
            )
    message = str(raised.value)
    assert message.startswith("invalid config: "), message
    assert "\n" not in message and "Traceback" not in message


async def test_the_run_env_binding_replaces_the_run_id_argument(
    queued_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    async with Client(mcp) as client:
        run_id = await _begin(client, queued_repo)
        monkeypatch.setenv("AUDITOR_REFINE_RUN", run_id)
        payload = _add_edge(queued_repo, run_id)
        payload.pop("run_id")
        verdict = _data(await client.call_tool("graph_refine_propose", payload))
        assert verdict["outcome"] == "staged"


async def test_a_propose_with_no_run_says_which_tool_opens_one(queued_repo: Path):
    async with Client(mcp) as client:
        payload = _add_edge(queued_repo, "")
        payload.pop("run_id")
        with pytest.raises(ToolError, match="graph_refine_begin"):
            await client.call_tool("graph_refine_propose", payload)


async def test_an_unknown_kind_names_the_kinds_that_exist(queued_repo: Path):
    async with Client(mcp) as client:
        run_id = await _begin(client, queued_repo)
        payload = _add_edge(queued_repo, run_id) | {"kind": "delete_edge"}
        with pytest.raises(ToolError, match="unknown kind"):
            await client.call_tool("graph_refine_propose", payload)


async def test_an_unknown_log_status_names_the_valid_set(queued_repo: Path):
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="unknown status"):
            await client.call_tool(
                "graph_log", {"path": str(queued_repo), "status": ["nope"]}
            )


async def test_the_log_and_the_status_agree_on_what_a_run_produced(queued_repo: Path):
    """One field, two meanings: the log counted every row a run owned and the status counted the
    ones it kept, so a run that stored four rejections read as seven corrections in one view and
    three in the other, and its own summary line said "0 rejected"."""
    async with Client(mcp) as client:
        run_id = await _begin(client, queued_repo)
        await client.call_tool("graph_refine_propose", _add_edge(queued_repo, run_id))
        await client.call_tool(
            "graph_refine_propose",
            _add_edge(queued_repo, run_id) | {"name": "get_user"},
        )
        await client.call_tool(
            "graph_refine_commit", {"path": str(queued_repo), "run_id": run_id}
        )
        status = _data(
            await client.call_tool(
                "graph_refine_status", {"path": str(queued_repo), "run_id": run_id}
            )
        )
        log = _data(await client.call_tool("graph_log", {"path": str(queued_repo)}))
    row = next(r for r in log["runs"] if r["run_id"] == run_id)
    assert row["refinements"] == {"committed": 1, "rejected": 1}
    assert row["refinements"] == status["run"]["refinements"]
    assert row["summary"] == "1 committed, 1 rejected"


async def test_the_refinements_view_reports_whether_it_was_filtered(queued_repo: Path):
    """One recorded row, so "empty" and "filtered to nothing" are two different answers rather than
    the same empty list twice."""
    async with Client(mcp) as client:
        run_id = await _begin(client, queued_repo)
        await client.call_tool(
            "graph_refine_propose",
            _add_edge(queued_repo, run_id) | {"name": "get_user"},
        )
        unfiltered = _data(
            await client.call_tool("graph_refinements", {"path": str(queued_repo)})
        )
        assert [r["status"] for r in unfiltered["rows"]] == ["rejected"]
        assert unfiltered["filtered"] is False
        filtered = _data(
            await client.call_tool(
                "graph_refinements", {"path": str(queued_repo), "status": ["active"]}
            )
        )
        assert filtered["rows"] == []
        assert filtered["filtered"] is True


async def test_the_refinements_limit_caps_the_rows(queued_repo: Path):
    """`limit` is a parameter, so it gets a test: an uncapped read of a busy repo is the failure
    the default exists to prevent."""
    async with Client(mcp) as client:
        run_id = await _begin(client, queued_repo)
        for name in ("get_user", "get_order"):
            await client.call_tool(
                "graph_refine_propose", _add_edge(queued_repo, run_id) | {"name": name}
            )
        every = _data(
            await client.call_tool("graph_refinements", {"path": str(queued_repo)})
        )
        assert len(every["rows"]) == 2
        capped = _data(
            await client.call_tool(
                "graph_refinements", {"path": str(queued_repo), "limit": 1}
            )
        )
        assert len(capped["rows"]) == 1
