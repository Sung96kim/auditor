"""The ``FastMCP`` server instance, its middleware and its ``main()`` entry point. No tools are
registered here: see ``auditor.mcp`` (the composition root) and the ``*_tools`` modules for those.
"""

import asyncio
import sys
from contextlib import suppress
from pathlib import Path

import mcp.types as mt
from fastmcp import FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware
from fastmcp.tools.base import ToolResult

from auditor.config_notice import NOTICE
from auditor.discovery import find_root

# Backstop so no single tool call can flood an agent's context. The per-tool bounds keep
# responses small by design; this catches anything that slips past. Tool calls only — resource
# reads (where the full artifacts live) are never truncated.
MAX_TOOL_RESPONSE_BYTES = 500_000

mcp: FastMCP = FastMCP(
    "auditor",
    instructions=(
        "Deterministic codebase auditor. `scan` a directory or `report` a single file to get "
        "structured findings (mechanical issues are pre-decided; semantic ones are flagged as "
        "'candidate' for you to judge). `manifest` returns a file's class/function structure. "
        "`rules_list` enumerates the detectors. "
        "`scan`/`report` default to a compact payload (rule metadata hoisted, `evidence` omitted, "
        "capped to the worst findings via `limit`); call `finding_detail` to recover a specific "
        "finding's full record, or `detail='full'` for the complete report as a resource."
    ),
)


# The parameter a tool names its repo with, most specific first. A tool that declares none of
# them (malware_status, malware_install) can never point the notice at the server's own cwd.
REPO_PARAMETERS = ("path", "file", "root")


def notice_lines(named: str) -> list[str]:
    """This process's notice for the repo a tool call named, empty when it has nothing to add.

    Blocking by nature: a git call and up to two config merges, so the caller runs it in a worker
    thread rather than on the event loop. A path the filesystem rejects says nothing rather than
    failing the tool call that carried it.
    """
    with suppress(OSError, ValueError):
        NOTICE.record(find_root(Path(named)))
        return NOTICE.report()
    return []


class ConfigNoticeMiddleware(Middleware):
    """Note the config keys no model declares on stderr, once per repo the server is asked about.

    stdout carries the MCP protocol, so the note can only go there; a long-lived server must not
    repeat it on every call, and a session that moves between repos hears about each of them.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        named = await self.repo_argument(context)
        if named is not None:
            lines = await asyncio.to_thread(notice_lines, named)
            if lines:
                print(f"auditr: {'; '.join(lines)}", file=sys.stderr)
        return await call_next(context)

    async def repo_argument(
        self, context: MiddlewareContext[mt.CallToolRequestParams]
    ) -> str | None:
        """The repo this call works on, falling back to the parameter's own default.

        The raw request carries only what the client sent, and ``path: str = "."`` is the shape
        agents actually call, so the declared default is where most calls name their repo.
        """
        tool = await context.fastmcp_context.fastmcp.get_tool(context.message.name)
        declared = tool.parameters.get("properties", {}) if tool is not None else {}
        arguments = context.message.arguments or {}
        for name in REPO_PARAMETERS:
            if name in declared:
                value = arguments.get(name, declared[name].get("default"))
                return None if value is None else str(value)
        return None


CONFIG_NOTICE_MIDDLEWARE = ConfigNoticeMiddleware()

mcp.add_middleware(ResponseLimitingMiddleware(max_size=MAX_TOOL_RESPONSE_BYTES))
mcp.add_middleware(CONFIG_NOTICE_MIDDLEWARE)


def main() -> None:
    # Silence FastMCP's ASCII banner + "update available" notice — on a stdio server they're just
    # noise in the client's MCP logs on every launch (stdout stays clean regardless).
    mcp.run(show_banner=False)
