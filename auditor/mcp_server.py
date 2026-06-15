"""MCP server (FastMCP) — exposes the auditor's core ops as agent tools.

Thin wrapper: every tool calls the same functions the CLI calls and returns structured
data. Run with ``python -m auditor.mcp_server`` (stdio) or the ``auditor-mcp`` script.
Requires the ``mcp`` extra (``pip install auditor[mcp]``).
"""

import ast
import difflib
import time
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

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
        "`rules_list` enumerates the detectors. "
        "`scan`/`report` default to a compact payload (rule metadata hoisted, `evidence` omitted); "
        "call `finding_detail` to recover a specific finding's full record."
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
    rule: list[str] | None = None,
    since: str | None = None,
    show_ignored: bool = False,
    config: dict | None = None,
    detail: str = "compact",
) -> dict:
    """Audit a file or directory. Returns {files: [...], totals: {...}}. ``profile`` overrides
    the repo's profile for this run (base|strict|pydantic|all-strict). ``no_skips`` ignores
    in-file ``auditor: skip`` directives. ``severity`` keeps only findings of those levels
    (blocking|high|medium|low|suggestion) — fewer tokens when you only want the worst. ``rule``
    keeps only findings for those rule ids (see rules_list) — focus on one rule.
    ``since`` (a git ref like ``main``/``HEAD``) scopes the output to files changed vs that ref
    — ideal for reviewing a branch/PR — while the whole repo is still scanned so cross-file
    rules stay correct. Persistent ignores (see the ignore_* tools) are applied automatically;
    ``show_ignored`` includes them. ``config`` is an optional dict of config overrides
    deep-merged as the highest layer and validated by ``AuditorSettings``.
    ``detail`` (summary|compact|full, default compact) controls payload size: compact hoists rule
    metadata into a `rules` map, slims findings, and drops `evidence` (recover it with
    finding_detail); full restores every field inline."""
    if not Path(path).exists():
        raise ToolError(f"no such path: {path}")
    _validate_detail(detail)
    root = find_root(Path(path))
    report_only = git_changed_files(root, since) if since else None
    try:
        results = await audit_target(
            Path(path),
            incremental=incremental or since is not None,
            strict_tests=strict_tests,
            profile=profile,
            no_skips=no_skips,
            report_only=report_only,
            config_overrides=config,
            show_ignored=show_ignored,
        )
    except ValidationError as exc:
        err = exc.errors()[0]
        loc = ".".join(str(p) for p in err["loc"])
        raise ToolError(
            f"invalid config: {loc + ': ' if loc else ''}{err['msg']}"
        ) from exc
    if severity:
        wanted = {s.lower() for s in severity}
        for r in results:
            r.findings = [f for f in r.findings if f.severity.value in wanted]
    if rule:
        known = REGISTRY.rule_ids()
        bad = [rid for rid in rule if rid not in known]
        if bad:
            match = difflib.get_close_matches(bad[0], list(known), n=1, cutoff=0.6)
            hint = f" Did you mean {match[0]!r}?" if match else ""
            raise ToolError(f"unknown rule {bad[0]!r}.{hint}")
        keep = set(rule)
        for r in results:
            r.findings = [f for f in r.findings if f.rule_id in keep]
    return json_payload(results, detail=detail)


@mcp.tool
async def report(
    file: str, profile: str | None = None, detail: str = "compact"
) -> dict:
    """Audit a single file statelessly (manifest + findings). ``detail``: summary|compact|full
    (default compact — hoists rule metadata, slims findings, drops evidence; use finding_detail
    to recover a finding's evidence; detail='full' restores every field inline)."""
    _validate_detail(detail)
    results = await audit_target(_require_file(file), profile=profile)
    return json_payload(results, detail=detail)


@mcp.tool
async def finding_detail(file: str, rule_id: str, line: int) -> dict:
    """Full record for one finding — `evidence`, `suggestion`, `standard_refs`, etc. — that the
    compact `scan`/`report` output omits. Reads the persisted index first; falls back to a fresh
    single-file re-scan so it works whether or not the scan was incremental. The index record may
    reflect a prior scan if the file was edited since it was indexed."""
    path = _require_file(file)
    root = find_root(path)
    try:
        rel = str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        rel = str(path)
    async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
        for f in await index.cached_findings(rel, rule_id):
            if f.line == line:
                return f.model_dump(mode="json")
    results = await audit_target(path, root=root, apply_ignores=False)
    for r in results:
        for f in r.findings:
            if f.rule_id == rule_id and f.line == line:
                return f.model_dump(mode="json")
    raise ToolError(f"no {rule_id} finding at {file}:{line}")


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


def _validate_detail(detail: str) -> None:
    if detail not in ("summary", "compact", "full"):
        raise ToolError(
            f"detail must be one of: summary, compact, full (got {detail!r})"
        )


@mcp.tool
def discover(path: str = ".") -> list[dict]:
    """List auditable files under a path with their classified role."""
    root = find_root(Path(path))
    settings = load_config(root)
    classifier = RoleClassifier(settings.role_globs)
    out = []
    discovery = FileDiscovery(
        root,
        exclude_globs=tuple(settings.exclude),
        respect_gitignore=settings.respect_gitignore,
    )
    for p in discovery.files(Path(path)):
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
def rules_list(
    category: str | None = None,
    standard: str | None = None,
    framework: str | None = None,
) -> list[dict]:
    """Enumerate detector rules, optionally filtered by category, standard (bandit/owasp), or
    framework (e.g. pytest)."""
    rows = []
    for rid in sorted(REGISTRY.rule_ids()):
        det = REGISTRY.detector(rid)
        if category and str(det.category) != category:
            continue
        if framework and getattr(det, "framework", None) != framework:
            continue
        refs = list(det.standard_refs)
        if standard and not any(r.startswith(f"{standard}:") for r in refs):
            continue
        rows.append(
            {
                "rule_id": rid,
                "category": str(det.category),
                "framework": getattr(det, "framework", None),
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
