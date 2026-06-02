"""MCP server (FastMCP) — exposes the auditor's core ops as agent tools.

Thin wrapper: every tool calls the same functions the CLI calls and returns structured
data. Run with ``python -m auditor.mcp_server`` (stdio) or the ``auditor-mcp`` script.
Requires the ``mcp`` extra (``pip install auditor[mcp]``).
"""

import ast
from pathlib import Path

from fastmcp import FastMCP

from auditor.aggregate import AuditAggregator
from auditor.config import load_config
from auditor.discovery import FileDiscovery, find_root
from auditor.engine import audit_target
from auditor.index import IndexStore
from auditor.models import ManifestEntry
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
    no_noqa: bool = False,
    severity: list[str] | None = None,
) -> dict:
    """Audit a file or directory. Returns {files: [...], totals: {...}}. ``profile`` overrides
    the repo's profile for this run (base|strict|pydantic|all-strict). ``no_noqa`` ignores
    in-file noqa directives. ``severity`` keeps only findings of those levels
    (blocking|high|medium|low|suggestion) — fewer tokens when you only want the worst."""
    results = await audit_target(
        Path(path),
        incremental=incremental,
        strict_tests=strict_tests,
        profile=profile,
        no_noqa=no_noqa,
    )
    if severity:
        wanted = {s.lower() for s in severity}
        for r in results:
            r.findings = [f for f in r.findings if f.severity.value in wanted]
    return json_payload(results)


@mcp.tool
async def report(file: str, profile: str | None = None) -> dict:
    """Audit a single file statelessly (manifest + findings)."""
    results = await audit_target(Path(file), profile=profile)
    return json_payload(results)


@mcp.tool
def manifest(file: str) -> list[dict]:
    """Return the AST class+function manifest for a Python file (no detectors)."""
    tree = ast.parse(Path(file).read_text(encoding="utf-8", errors="replace"))
    return [e.model_dump(mode="json") for e in ManifestEntry.from_module(tree)]


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
    async with await IndexStore.connect(root / ".auditor" / "index.db") as index:
        return await AuditAggregator(index).markdown()


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
