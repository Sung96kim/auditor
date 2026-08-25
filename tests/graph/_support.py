"""What `tests/graph/` shares: the refinement-run drivers, a run row, and a console render.

Graph-local on purpose. Driving a run needs `fastmcp` and `auditor.mcp`, and the tree-wide
`tests/_support.py` is imported by every test session, fast CLI tests included.
"""

import asyncio
import io
import re
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeVar

from _support import tool_data
from fastmcp import Client
from rich.console import Console

from auditor.database import open_repo_index
from auditor.graph.refine.models import (
    ProducerKind,
    Run,
    RunnerKind,
    RunStatus,
    TriggerKind,
)
from auditor.mcp import mcp

PayloadT = TypeVar("PayloadT")

#: the one true correction on the `refine_repo` pair: `caller.main` calls a name `helper` defines
GOOD_PROPOSAL: Mapping[str, str] = MappingProxyType(
    {
        "kind": "add_edge",
        "src": "caller.py::main",
        "dst": "helper.py::read_event",
        "edge_kind": "calls",
        "name": "read_event",
        "reason": "main calls read_event, which helper.py defines",
    }
)


async def _drive(
    repo: Path, proposals: Sequence[Mapping[str, Any]], reason: str | None
) -> dict[str, Any]:
    """Open a run, propose into it, and end it the way ``reason`` says: abort, or commit."""
    async with Client(mcp) as client:
        begun = await client.call_tool("graph_refine_begin", {"path": str(repo)})
        run_id = tool_data(begun)["run_id"]
        for proposal in proposals:
            await client.call_tool(
                "graph_refine_propose",
                {"path": str(repo), "run_id": run_id, **proposal},
            )
        args: dict[str, Any] = {"path": str(repo), "run_id": run_id}
        if reason is None:
            return tool_data(await client.call_tool("graph_refine_commit", args))
        ended = await client.call_tool("graph_refine_abort", args | {"reason": reason})
        return tool_data(ended)


def refine_run(repo: Path, *proposals: Mapping[str, Any]) -> dict[str, Any]:
    """Drive one run through the MCP tools to its commit; answers that commit's ``CommitResult``.

    The tools are the public producer, so the rows a test reads were written the way an agent
    writes them.
    """
    return asyncio.run(_drive(repo, proposals, None))


def refine_abort(
    repo: Path, *proposals: Mapping[str, Any], reason: str
) -> dict[str, Any]:
    """Drive one run and abort it; answers the finished run row, not a commit result."""
    return asyncio.run(_drive(repo, proposals, reason))


def tool_log(repo: Path, **kw: Any) -> dict[str, Any]:
    """One page of the provenance log through the `graph_log` MCP tool, the surface the CLI mirrors."""

    async def go() -> dict[str, Any]:
        async with Client(mcp) as client:
            return tool_data(
                await client.call_tool("graph_log", {"path": str(repo), **kw})
            )

    return asyncio.run(go())


def add_observer_run(repo: Path, *, status: RunStatus, age_seconds: float) -> str:
    """One observer run row written directly and aged by hand, which is the only way a test can
    have a run older than a retention window. The assessment writes `skipped` rows in S8;
    eviction already does today."""

    async def go() -> str:
        index = await open_repo_index(repo)
        try:
            return await index.runs.add_run(
                Run(
                    repo_identity=index.partition.identity,
                    producer=ProducerKind.OBSERVER,
                    runner=RunnerKind.NONE,
                    trigger_kind=TriggerKind.EDIT,
                    status=status,
                    summary="no structural change",
                    started_at=time.time() - age_seconds,
                )
            )
        finally:
            await index.aclose()

    return asyncio.run(go())


def render_text(
    render: Callable[[Console, PayloadT], None],
    payload: PayloadT,
    *,
    width: int = 120,
    color: bool = False,
) -> str:
    """One payload through its own renderer, as text at a fixed console width.

    ``color`` forces the ANSI codes a real terminal would get: off a TTY rich drops every style,
    so a cell that is styled apart from its neighbours is invisible without it.
    """
    buf = io.StringIO()
    console = (
        Console(file=buf, width=width, force_terminal=True, color_system="standard")
        if color
        else Console(file=buf, width=width)
    )
    render(console, payload)
    return buf.getvalue()


def cells(rendered: str, first: str) -> list[str]:
    """The cells of the row whose first column is ``first``, so a value under the wrong header is
    visible where a substring search over the whole table is not."""
    for line in rendered.splitlines():
        parts = re.split(r"[│┃]", line)
        row = [c.strip() for c in parts[1:-1]]
        if len(parts) > 2 and row and row[0] == first:
            return row
    raise AssertionError(f"no row starting {first!r} in\n{rendered}")
