"""The ``FastMCP`` server instance, its middleware and its ``main()`` entry point. No tools are
registered here: see ``auditor.mcp`` (the composition root) and the ``*_tools`` modules for those.
"""

import sys
from pathlib import Path

import mcp.types as mt
from fastmcp import FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware
from fastmcp.tools.base import ToolResult

from auditor.config_notice import NOTICE, ConfigNotice
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


class ConfigNoticeMiddleware(Middleware):
    """Note the config keys no model declares, once per server process, on stderr.

    stdout carries the MCP protocol, so the note can only go there; a long-lived server must not
    repeat it on every call, and the repo comes from the first tool call that names one.
    """

    def __init__(self) -> None:
        self.noted = False

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        arguments = context.message.arguments or {}
        # six tools take neither: rules_list, malware_status, malware_install take nothing,
        # manifest/report/finding_detail take `file`. Only a call that names a repo may latch.
        named = arguments.get("path") or arguments.get("file")
        if named is not None and not self.noted:
            self.noted = True
            NOTICE.record(find_root(Path(str(named))))
            keys = NOTICE.reportable()
            if keys:
                print(
                    f"auditr: ignoring unknown config key(s) {', '.join(keys)}; {ConfigNotice.HINT}",
                    file=sys.stderr,
                )
        return await call_next(context)


CONFIG_NOTICE_MIDDLEWARE = ConfigNoticeMiddleware()

mcp.add_middleware(ResponseLimitingMiddleware(max_size=MAX_TOOL_RESPONSE_BYTES))
mcp.add_middleware(CONFIG_NOTICE_MIDDLEWARE)


def main() -> None:
    # Silence FastMCP's ASCII banner + "update available" notice — on a stdio server they're just
    # noise in the client's MCP logs on every launch (stdout stays clean regardless).
    mcp.run(show_banner=False)
