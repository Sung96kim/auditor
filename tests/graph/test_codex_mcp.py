"""The loopback `graph` server a Codex run proposes through, driven by a real MCP client."""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from graph._support import GOOD_PROPOSAL
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from auditor.graph.refine.codex_mcp import GraphShim
from auditor.graph.refine.models import ProposalOutcome, RefinementKind, Verdict
from auditor.graph.refine.sdk_runner import BoundTools
from auditor.graph.refine.service import RefinementService


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
