"""The ``FastMCP`` server instance and its ``main()`` entry point. No tools are registered
here — see ``auditor.mcp`` (the composition root) and the ``*_tools`` modules for those.
"""

from fastmcp import FastMCP
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware

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
mcp.add_middleware(ResponseLimitingMiddleware(max_size=MAX_TOOL_RESPONSE_BYTES))


def main() -> None:
    # Silence FastMCP's ASCII banner + "update available" notice — on a stdio server they're just
    # noise in the client's MCP logs on every launch (stdout stays clean regardless).
    mcp.run(show_banner=False)
