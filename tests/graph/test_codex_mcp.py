"""The loopback `graph` server a Codex run proposes through, driven by a real MCP client."""

import asyncio
import socket
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from graph._support import GOOD_PROPOSAL
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from auditor.graph.refine import codex_mcp
from auditor.graph.refine.codex_mcp import STOP_TIMEOUT, GraphShim
from auditor.graph.refine.models import ProposalOutcome, RefinementKind, Verdict
from auditor.graph.refine.sdk_runner import BoundTools
from auditor.graph.refine.service import RefinementService


def _listening(port: int) -> bool:
    """Whether anything still holds that loopback port."""
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture
async def bound(refine_service: RefinementService) -> BoundTools:
    run = await refine_service.begin()
    return BoundTools(
        service=refine_service, run_id=run.run_id, proposer=refine_service.propose
    )


@asynccontextmanager
async def _session(shim: GraphShim) -> AsyncIterator[ClientSession]:
    """One initialized MCP session against this run's shim, bearer token and all.

    `streamable_http_client` takes no `headers`, so the credential rides an `httpx` client it is
    handed rather than the call.
    """
    async with (
        httpx.AsyncClient(headers={"Authorization": f"Bearer {shim.token}"}) as client,
        streamable_http_client(shim.url, http_client=client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


async def test_a_real_client_lists_the_two_bound_tools(bound):
    async with GraphShim(bound) as shim, _session(shim) as session:
        listed = await session.list_tools()
    assert [tool.name for tool in listed.tools] == ["propose", "brief"]


async def test_a_tool_call_really_runs_the_bound_handler(bound):
    async with GraphShim(bound) as shim, _session(shim) as session:
        answer = await session.call_tool("propose", dict(GOOD_PROPOSAL))
    assert answer.isError is False
    assert "outcome" in answer.content[0].text


async def test_an_injected_judge_sees_the_proposal_the_run_made(
    refine_service: RefinementService,
):
    """The seam the whole shim exists for: an eval judges instead of the service storing."""
    seen: list[dict[str, Any]] = []

    async def judge(run_id: str, proposal: Mapping[str, Any]) -> Verdict:
        seen.append(dict(proposal))
        return Verdict(outcome=ProposalOutcome.REJECTED, kind=RefinementKind.ADD_EDGE)

    run = await refine_service.begin()
    tools = BoundTools(service=refine_service, run_id=run.run_id, proposer=judge)
    async with GraphShim(tools) as shim, _session(shim) as session:
        await session.call_tool("propose", dict(GOOD_PROPOSAL))
    assert len(seen) == 1


async def test_a_caller_without_this_run_s_token_is_refused(bound):
    async with (
        GraphShim(bound) as shim,
        httpx.AsyncClient(follow_redirects=True) as client,
    ):
        answer = await client.post(shim.url, json={})
    assert answer.status_code == 401


async def test_two_shims_over_one_table_mint_different_credentials(bound):
    """The whole access control on the loopback surface is that the bearer is this run's."""
    async with GraphShim(bound) as first, GraphShim(bound) as second:
        assert first.token != second.token
        assert first.handshake != second.handshake
        assert len(first.token) >= 32


async def test_another_run_s_token_is_refused_by_this_run_s_shim(bound):
    async with (
        GraphShim(bound) as mine,
        GraphShim(bound) as theirs,
        httpx.AsyncClient(follow_redirects=True) as client,
    ):
        answer = await client.post(
            mine.url,
            json={},
            headers={"Authorization": f"Bearer {theirs.token}"},
        )
    assert answer.status_code == 401


async def test_the_server_answers_with_this_run_s_handshake(bound):
    """`serverInfo.version` is what a run reads back to tell its shim from another run's."""
    async with GraphShim(bound) as shim, _session(shim) as session:
        answer = await session.initialize()
    assert answer.serverInfo.version == shim.handshake


async def test_a_startup_that_fails_leaves_no_listener_and_no_token_in_memory(
    bound, monkeypatch
):
    """`async with` never calls `__aexit__` for an enter that raised (H1)."""
    shim = GraphShim(bound)

    async def never(self, server):
        raise TimeoutError("uvicorn never bound")

    monkeypatch.setattr(GraphShim, "_bound", never)
    with pytest.raises(TimeoutError):
        await shim.__aenter__()
    assert shim.port == 0
    assert shim._task is not None and shim._task.done()
    # nothing on loopback can be asked for this run's credential any more
    assert not _listening(shim.port)


async def test_a_startup_that_bound_and_then_failed_releases_the_port(
    bound, monkeypatch
):
    """The listener is real by the time a later step raises, so the teardown has to be real too."""
    shim = GraphShim(bound)
    ports: list[int] = []
    real = GraphShim._bound

    async def bind_then_fail(self, server):
        await real(self, server)
        ports.append(server.servers[0].sockets[0].getsockname()[1])
        raise RuntimeError("the config could not be written")

    monkeypatch.setattr(GraphShim, "_bound", bind_then_fail)
    with pytest.raises(RuntimeError):
        await shim.__aenter__()
    assert ports and not _listening(ports[0])


async def test_the_shim_is_asked_to_stop_rather_than_cancelled_out(bound):
    """M27: an exit that never sets `should_exit` waits out the whole ceiling before cancelling."""
    shim = GraphShim(bound)
    await shim.__aenter__()
    began = time.monotonic()
    await shim.__aexit__(None, None, None)
    assert time.monotonic() - began < STOP_TIMEOUT
    assert not _listening(shim.port)


async def test_a_drain_that_never_finishes_is_cancelled_at_the_ceiling(monkeypatch):
    """A `propose` can wait on the rebuild lock for two minutes; uvicorn would wait forever."""
    monkeypatch.setattr(codex_mcp, "STOP_TIMEOUT", 0.1)
    hung = asyncio.create_task(asyncio.sleep(60))
    await GraphShim._stopped(hung)
    assert hung.cancelled()


async def test_the_listener_is_built_with_the_same_ceiling_it_waits_out(bound):
    """Both halves are needed: uvicorn's own default is to wait for a request indefinitely."""
    async with GraphShim(bound) as shim:
        assert shim._server is not None
        assert shim._server.config.timeout_graceful_shutdown == STOP_TIMEOUT


async def test_a_cancelled_run_still_releases_the_port(bound):
    """A run cancelled mid-turn leaves no listener behind, which the ceiling must not change."""
    ports: list[int] = []

    async def serving() -> None:
        async with GraphShim(bound) as shim:
            ports.append(shim.port)
            await asyncio.sleep(60)

    task = asyncio.create_task(serving())
    while not ports:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not _listening(ports[0])
