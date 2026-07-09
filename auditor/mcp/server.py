"""The ``FastMCP`` server instance and its ``main()`` entry point. No tools are registered
here — see ``auditor.mcp`` (the composition root) and the ``*_tools`` modules for those.
"""

from fastmcp import FastMCP

mcp: FastMCP = FastMCP(
    "auditor",
    instructions=(
        "Deterministic codebase auditor. `scan` a directory or `report` a single file to get "
        "structured findings (mechanical issues are pre-decided; semantic ones are flagged as "
        "'candidate' for you to judge). `manifest` returns a file's class/function structure. "
        "`rules_list` enumerates the detectors. "
        "`scan`/`report` default to a compact payload (rule metadata hoisted, `evidence` omitted); "
        "call `finding_detail` to recover a specific finding's full record."
    ),
)


def main() -> None:
    mcp.run()
