"""The one `graph` MCP server a Codex run proposes through, inside this process (spec 9.3).

Codex has no in-process MCP transport, so the same `BoundTools` table the Claude runner binds is
served over loopback streamable HTTP instead. That is what keeps an eval's judge injectable.
"""

import asyncio
import contextlib
import secrets
from collections.abc import Mapping
from typing import Any

import mcp.types as types
import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from auditor.graph.refine.prompts import GRAPH_SERVER
from auditor.graph.refine.sdk_runner import BoundTools

MOUNT = "/mcp"
LOOPBACK = "127.0.0.1"
#: how long `__aenter__` waits for uvicorn to bind before giving up
START_TIMEOUT = 5.0
#: how long `__aexit__` waits for uvicorn to drain before cancelling it. Bounded on purpose: a
#: `propose` handler can sit on the rebuild lock for `rebuild_lock_timeout_seconds`, and uvicorn's
#: own default is to wait for an in-flight request forever.
STOP_TIMEOUT = 5.0


def _result(answer: Mapping[str, Any]) -> types.CallToolResult:
    """One `BoundTool` answer as the MCP result shape, error flag included."""
    blocks = [
        types.TextContent(type="text", text=str(part.get("text", "")))
        for part in answer.get("content", ())
    ]
    return types.CallToolResult(content=blocks, isError=bool(answer.get("is_error")))


def graph_server(tools: BoundTools, handshake: str) -> Server[Any, Any]:
    """The lowlevel MCP server over one run's bound tools, in `BoundTools.tools()` order.

    ``handshake`` rides out as the server version, which is the one field of `serverInfo` a run
    can read back through `mcpServerStatus/list` to tell its own shim from another run's.
    """
    server: Server[Any, Any] = Server(GRAPH_SERVER, version=handshake)
    table = {bound.name: bound for bound in tools.tools()}

    @server.list_tools()
    async def _list() -> list[types.Tool]:
        return [
            types.Tool(
                name=bound.name,
                description=bound.description,
                inputSchema=bound.input_schema,
            )
            for bound in table.values()
        ]

    @server.call_tool()
    async def _call(name: str, args: dict[str, Any]) -> types.CallToolResult:
        bound = table.get(name)
        if bound is None:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"no tool {name!r}")],
                isError=True,
            )
        return _result(await bound.handler(args))

    return server


class GraphShim:
    """One run's `graph` server on an ephemeral loopback port, torn down with the run.

    The bearer credential is minted per run and travels by env var, so a stale `config.toml`
    cannot reach a later run's tools. The handshake is minted per run too and is not the
    credential: it is echoed back in server status, where a secret has no business being.
    """

    def __init__(self, tools: BoundTools) -> None:
        self.tools = tools
        self.token = secrets.token_urlsafe(24)
        self.handshake = secrets.token_hex(8)
        self.port = 0
        self._stack = contextlib.AsyncExitStack()
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def url(self) -> str:
        """Where the private `config.toml` points `[mcp_servers.graph]`."""
        return f"http://{LOOPBACK}:{self.port}{MOUNT}"

    def _app(self, manager: StreamableHTTPSessionManager) -> Any:
        """The bare ASGI app, behind the one check that this run minted the caller's credential.

        No router: the manager answers whatever path it is given, and a Starlette mount would
        redirect `/mcp` to `/mcp/` before the credential is ever looked at.
        """

        async def handle(scope: Any, receive: Any, send: Any) -> None:
            offered = dict(scope.get("headers") or ()).get(b"authorization", b"")
            if not secrets.compare_digest(
                offered.decode("utf-8", "replace"), f"Bearer {self.token}"
            ):
                await send(
                    {"type": "http.response.start", "status": 401, "headers": []}
                )
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
            await manager.handle_request(scope, receive, send)

        return handle

    async def __aenter__(self) -> "GraphShim":
        try:
            await self._start()
        except BaseException:
            # `async with` never calls `__aexit__` for an enter that raised, so a half-built
            # shim would keep a listener and this run's credential alive
            await self.__aexit__(None, None, None)
            raise
        return self

    async def _start(self) -> None:
        """Bring the session manager and the loopback listener up, in that order."""
        manager = StreamableHTTPSessionManager(
            app=graph_server(self.tools, self.handshake),
            json_response=True,
            stateless=True,
        )
        await self._stack.enter_async_context(manager.run())
        config = uvicorn.Config(
            self._app(manager),
            host=LOOPBACK,
            port=0,
            log_level="warning",
            timeout_graceful_shutdown=STOP_TIMEOUT,
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())
        await self._bound(self._server)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]

    async def _bound(self, server: uvicorn.Server) -> None:
        """Wait for uvicorn to own a socket, refusing rather than serving a port of zero."""
        async with asyncio.timeout(START_TIMEOUT):
            while not server.started:
                await asyncio.sleep(0.01)

    async def __aexit__(self, *exc: Any) -> None:
        if self._server is not None:
            self._server.should_exit = True
        try:
            if self._task is not None:
                await self._stopped(self._task)
        finally:
            # the drain is cancellable, and a cancellation there must not leave the session
            # manager running
            await self._stack.aclose()

    @staticmethod
    async def _stopped(task: asyncio.Task[None]) -> None:
        """Wait out the graceful drain, then cancel: a hung handler must not hold the run open.

        Unshielded on purpose: a cancellation from outside has to reach uvicorn too, which is
        what already released the port when a run was cancelled mid-turn.
        """
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(task, STOP_TIMEOUT)
