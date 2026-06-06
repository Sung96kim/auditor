"""MCP server (FastMCP) — exposes the auditor's core ops as agent tools.

Thin wrapper: every tool calls the same functions the CLI calls and returns structured
data. Run with ``python -m auditor.mcp_server`` (stdio) or the ``auditor-mcp`` script.
Requires the ``mcp`` extra (``pip install auditor[mcp]``).
"""

import ast
import time
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from auditor.aggregate import AuditAggregator
from auditor.config import load_config
from auditor.discovery import FileDiscovery, find_root, git_changed_files
from auditor.engine import audit_target, finding_evidence_at
from auditor.ignores import evidence_hash
from auditor.index import IndexStore
from auditor.models import ManifestEntry
from auditor.paths import index_db_path, repo_key
from auditor.registry import REGISTRY
from auditor.reporters.json_reporter import payload as json_payload
from auditor.roles import RoleClassifier

mcp: FastMCP = FastMCP(
    "auditor",
    instructions=(
        "Token-efficient repo auditor. `scan` a directory or `report` a single file to get "
        "structured findings (mechanical issues are pre-decided; semantic ones are flagged as "
        "'candidate' for you to judge). `manifest` returns a file's class/function structure. "
        "`rules_list` enumerates the detectors."
    ),
)


@mcp.tool
async def scan(
    path: str = ".",
    incremental: bool = False,
    strict_tests: bool = False,
    profile: str | None = None,
    no_skips: bool = False,
    severity: list[str] | None = None,
    since: str | None = None,
    show_ignored: bool = False,
) -> dict:
    """Audit a file or directory. Returns {files: [...], totals: {...}}. ``profile`` overrides
    the repo's profile for this run (base|strict|pydantic|all-strict). ``no_skips`` ignores
    in-file ``auditor: skip`` directives. ``severity`` keeps only findings of those levels
    (blocking|high|medium|low|suggestion) — fewer tokens when you only want the worst.
    ``since`` (a git ref like ``main``/``HEAD``) scopes the output to files changed vs that ref
    — ideal for reviewing a branch/PR — while the whole repo is still scanned so cross-file
    rules stay correct. Persistent ignores (see the ignore_* tools) are applied automatically;
    ``show_ignored`` includes them."""
    if not Path(path).exists():
        raise ToolError(f"no such path: {path}")
    root = find_root(Path(path))
    report_only = git_changed_files(root, since) if since else None
    results = await audit_target(
        Path(path),
        incremental=incremental or since is not None,
        strict_tests=strict_tests,
        profile=profile,
        no_skips=no_skips,
        report_only=report_only,
        show_ignored=show_ignored,
    )
    if severity:
        wanted = {s.lower() for s in severity}
        for r in results:
            r.findings = [f for f in r.findings if f.severity.value in wanted]
    return json_payload(results)


@mcp.tool
async def report(file: str, profile: str | None = None) -> dict:
    """Audit a single file statelessly (manifest + findings)."""
    results = await audit_target(_require_file(file), profile=profile)
    return json_payload(results)


@mcp.tool
def manifest(file: str) -> list[dict]:
    """Return the AST class+function manifest for a Python file (no detectors)."""
    path = _require_file(file)
    if path.suffix not in (".py", ".pyi"):
        raise ToolError(f"manifest is Python-only; {path.name} is not a .py file")
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError) as exc:  # ValueError: source contains null bytes
        raise ToolError(f"could not parse {path.name}: {exc}") from exc
    return [e.model_dump(mode="json") for e in ManifestEntry.from_module(tree)]


def _require_file(file: str) -> Path:
    """Resolve ``file`` to an existing path or raise a clean ``ToolError`` (not a raw OSError
    traceback) — the agent-facing equivalent of the CLI's file checks."""
    path = Path(file)
    if not path.is_file():
        raise ToolError(f"no such file: {file}")
    return path


@mcp.tool
def discover(path: str = ".") -> list[dict]:
    """List auditable files under a path with their classified role."""
    root = find_root(Path(path))
    classifier = RoleClassifier(load_config(root).role_globs)
    out = []
    for p in FileDiscovery(root).files(Path(path)):
        rel = str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
        role = classifier.classify(rel, p.read_text(encoding="utf-8", errors="replace"))
        out.append({"file": rel, "role": role.value})
    return out


@mcp.tool
async def aggregate(path: str = ".") -> str:
    """Roll up the index into an AUDIT.md string (run scan with incremental=True first)."""
    root = find_root(Path(path))
    async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
        return await AuditAggregator(index).markdown()


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
    load_config(root)  # register the repo's entry-point/config plugins so their rules validate
    if not force and rule_id not in REGISTRY.rule_ids():
        raise ToolError(
            f"unknown rule_id {rule_id!r}; use rules_list to see rules (or force=true)"
        )
    ev_hash = None
    if line is not None and file is not None:
        evidence = await finding_evidence_at(root, file, rule_id, line)
        ev_hash = evidence_hash(evidence) if evidence is not None else None
    async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
        ignore_id = await index.add_ignore(
            rule_id, file, line, ev_hash, reason, time.time()
        )
    return {"id": ignore_id, "rule_id": rule_id, "file": file, "line": line}


@mcp.tool
async def ignore_list(path: str = ".") -> list[dict]:
    """List the persistent ignores recorded for this repo (with their ids)."""
    root = find_root(Path(path))
    async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
        return await index.ignores()


@mcp.tool
async def ignore_remove(id: int, path: str = ".") -> dict:
    """Remove (unignore) a persistent ignore by its id (from ignore_list)."""
    root = find_root(Path(path))
    async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
        removed = await index.remove_ignore_by_id(id)
    return {"removed": removed, "id": id}


@mcp.tool
def rules_list(category: str | None = None, standard: str | None = None) -> list[dict]:
    """Enumerate detector rules, optionally filtered by category or standard (bandit/owasp)."""
    rows = []
    for rid in sorted(REGISTRY.rule_ids()):
        det = REGISTRY.detector(rid)
        if category and str(det.category) != category:
            continue
        refs = list(det.standard_refs)
        if standard and not any(r.startswith(f"{standard}:") for r in refs):
            continue
        rows.append(
            {
                "rule_id": rid,
                "category": str(det.category),
                "default_severity": det.default_severity.value,
                "verdict_kind": det.verdict_kind.value,
                "standard_refs": refs,
            }
        )
    return rows


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
