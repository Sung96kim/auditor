# auditor: skip-file: PY-TYPING-UNTYPED-DICT  (MCP tool surface — JSON payloads by contract)
"""ignore_add / ignore_list / ignore_remove — the persistent-ignore MCP tools."""

import time

from fastmcp.exceptions import ToolError

from auditor.engine import finding_evidence_at
from auditor.ignores import evidence_hash
from auditor.mcp.helpers import DESTRUCTIVE, MUTATING, READ_ONLY, tool_repo
from auditor.mcp.server import mcp
from auditor.registry import REGISTRY


@mcp.tool(annotations=MUTATING)
async def ignore_add(
    rule_id: str,
    file: str | None = None,
    line: int | None = None,
    reason: str | None = None,
    path: str = ".",
    force: bool = False,
) -> dict:
    """Persistently ignore findings so future scans hide them. Scope by what you pass: nothing =
    the rule across the whole repo; ``file`` = that file; ``file`` + ``line`` = that one finding
    (its offending text is snapshotted so the ignore follows the code when lines shift). Keyed by
    ``rule_id`` (must be a known rule unless ``force``, e.g. an untrusted local plugin rule).
    Idempotent per scope."""
    async with tool_repo(path) as repo:
        repo.settings()  # register the repo's plugins so their rules validate
        if not force and rule_id not in REGISTRY.rule_ids():
            raise ToolError(
                f"unknown rule_id {rule_id!r}; use rules_list to see rules (or force=true)"
            )
        ev_hash = None
        if line is not None and file is not None:
            evidence = await finding_evidence_at(repo.root, file, rule_id, line)
            ev_hash = evidence_hash(evidence) if evidence is not None else None
        ignore_id = await repo.index.ignores.add(
            rule_id, file, line, ev_hash, reason, time.time()
        )
    return {"id": ignore_id, "rule_id": rule_id, "file": file, "line": line}


@mcp.tool(annotations=READ_ONLY)
async def ignore_list(path: str = ".") -> list[dict]:
    """List the persistent ignores recorded for this repo (with their ids)."""
    async with tool_repo(path) as repo:
        return await repo.index.ignores.list()


@mcp.tool(annotations=DESTRUCTIVE)
async def ignore_remove(id: int, path: str = ".") -> dict:
    """Remove (unignore) a persistent ignore by its id (from ignore_list)."""
    async with tool_repo(path) as repo:
        removed = await repo.index.ignores.remove_by_id(id)
    return {"removed": removed, "id": id}
