# auditor: skip-file: PY-TYPING-UNTYPED-DICT  (MCP tool surface — JSON payloads by contract)
"""ignore_add / ignore_list / ignore_remove — the persistent-ignore MCP tools."""

import time
from pathlib import Path

from fastmcp.exceptions import ToolError

from auditor.config import load_config
from auditor.database import IndexStore
from auditor.discovery import find_root
from auditor.engine import finding_evidence_at
from auditor.ignores import evidence_hash
from auditor.mcp.server import mcp
from auditor.paths import index_db_path, repo_key
from auditor.registry import REGISTRY


@mcp.tool
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
    ``rule_id`` (must be a known rule unless ``force`` — e.g. an untrusted local plugin rule).
    Idempotent per scope."""
    root = find_root(Path(path))
    load_config(
        root
    )  # register the repo's entry-point/config plugins so their rules validate
    if not force and rule_id not in REGISTRY.rule_ids():
        raise ToolError(
            f"unknown rule_id {rule_id!r}; use rules_list to see rules (or force=true)"
        )
    ev_hash = None
    if line is not None and file is not None:
        evidence = await finding_evidence_at(root, file, rule_id, line)
        ev_hash = evidence_hash(evidence) if evidence is not None else None
    async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
        ignore_id = await index.ignores.add(
            rule_id, file, line, ev_hash, reason, time.time()
        )
    return {"id": ignore_id, "rule_id": rule_id, "file": file, "line": line}


@mcp.tool
async def ignore_list(path: str = ".") -> list[dict]:
    """List the persistent ignores recorded for this repo (with their ids)."""
    root = find_root(Path(path))
    async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
        return await index.ignores.list()


@mcp.tool
async def ignore_remove(id: int, path: str = ".") -> dict:
    """Remove (unignore) a persistent ignore by its id (from ignore_list)."""
    root = find_root(Path(path))
    async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
        removed = await index.ignores.remove_by_id(id)
    return {"removed": removed, "id": id}
