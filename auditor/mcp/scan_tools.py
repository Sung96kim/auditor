# auditor: skip-file: PY-TYPING-UNTYPED-DICT  (MCP tool surface — JSON payloads by contract)
"""Scan/report/finding_detail/manifest/discover/aggregate — the audit-surface MCP tools."""

import ast
import difflib
import json
from pathlib import Path

from fastmcp.exceptions import ToolError
from mcp.types import ResourceLink
from pydantic import ValidationError

from auditor.aggregate import AuditAggregator
from auditor.config import load_config
from auditor.database import IndexStore
from auditor.discovery import FileDiscovery, find_root, git_changed_files
from auditor.engine import audit_target
from auditor.malware.tools import resolve_tool
from auditor.mcp.artifacts import publish
from auditor.mcp.helpers import READ_ONLY, validate_detail
from auditor.mcp.server import mcp
from auditor.models import ManifestEntry, ScanResult
from auditor.paths import index_db_path, repo_key
from auditor.registry import REGISTRY
from auditor.reporters.json_reporter import payload as json_payload
from auditor.roles import RoleClassifier


def _as_list(value: str | list[str] | None) -> list[str] | None:
    """Normalize a filter argument that accepts either a single id or a list of them."""
    if value is None:
        return None
    return [value] if isinstance(value, str) else value


def _report_or_link(
    results: list[ScanResult], *, detail: str, limit: int | None, kind: str, seed: str
) -> dict | ResourceLink:
    """Shape the audit results for an agent. ``summary``/``compact`` return inline (compact is
    capped to ``limit`` worst findings); ``full`` is the complete record, which is large, so it
    is stashed and returned as a ``ResourceLink`` for on-demand fetch rather than dumped inline."""
    if detail == "full":
        body = json.dumps(json_payload(results, detail="full"), default=str)
        findings = sum(len(r.findings) for r in results)
        return publish(
            kind,
            seed,
            body,
            mime_type="application/json",
            name=f"{kind}-full.json",
            description=f"Full {kind} report — {findings} findings across {len(results)} "
            f"files ({len(body)} bytes). Read this resource for the complete record.",
        )
    return json_payload(results, detail=detail, limit=limit)


@mcp.tool(annotations=READ_ONLY)
async def scan(
    path: str = ".",
    incremental: bool = False,
    strict_tests: bool = False,
    profile: str | None = None,
    no_skips: bool = False,
    severity: str | list[str] | None = None,
    rule: str | list[str] | None = None,
    since: str | None = None,
    show_ignored: bool = False,
    config: dict | None = None,
    detail: str = "compact",
    limit: int | None = 50,
    isolated: bool = False,
    malware: bool = False,
) -> dict | ResourceLink:
    """Audit a file or directory. Returns {files: [...], totals: {...}}. ``profile`` overrides
    the repo's profile for this run (base|strict|pydantic|all-strict). ``no_skips`` ignores
    in-file ``auditor: skip`` directives. ``severity`` keeps only findings of those levels
    (blocking|high|medium|low|suggestion) — fewer tokens when you only want the worst. ``rule``
    keeps only findings for those rule ids (see rules_list) — focus on one rule. Both ``severity``
    and ``rule`` take a single id or a list (e.g. ``rule="PY-SEC-DANGEROUS-EVAL"`` or
    ``rule=["PY-SEC-DANGEROUS-EVAL", "PY-SEC-SSRF"]``).
    ``since`` (a git ref like ``main``/``HEAD``) scopes the output to files changed vs that ref
    — ideal for reviewing a branch/PR — while the whole repo is still scanned so cross-file
    rules stay correct. Persistent ignores (see the ignore_* tools) are applied automatically;
    ``show_ignored`` includes them. ``config`` is an optional dict of config overrides
    deep-merged as the highest layer and validated by ``AuditorSettings``.
    ``detail`` (summary|compact|full, default compact) controls payload size: compact hoists rule
    metadata into a `rules` map, slims findings, drops `evidence` (recover it with finding_detail),
    and omits clean files (`scanned` carries the total file count); full restores every field.
    ``limit`` (compact only, default 50) caps the response to the worst-N findings — the surplus
    is summarized under `omitted` (raise it, or filter with severity=/rule=, to see more); pass
    null to uncap. ``detail='full'`` is the complete record and can be huge, so it is returned as
    a ResourceLink to fetch on demand rather than inline.
    Auditing a single FILE still runs the repo-wide cross-file rules (duplicate/dead-code) off the
    shared index — if the repo was never indexed, the first such call warms it once (peers come from
    the index, not a re-audit). ``isolated`` skips that: audit only this file, no index, no
    cross-file — faster for a quick standalone check. ``malware=true`` also runs the opt-in
    malware pass (requires a backend; see malware_status/malware_install)."""
    if not Path(path).exists():
        raise ToolError(f"no such path: {path}")
    validate_detail(detail)
    severity = _as_list(severity)
    rule = _as_list(rule)
    root = find_root(Path(path))
    if malware:
        if not (resolve_tool("clamdscan") or resolve_tool("clamscan") or resolve_tool("osv-scanner")):
            raise ToolError(
                "malware scan requested but neither ClamAV nor osv-scanner is installed — "
                "run malware_install (or `auditor malware install`)"
            )
        merged = dict(config or {})
        merged["malware_scan"] = {**merged.get("malware_scan", {}), "enabled": True}
        config = merged
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
            cross_file=not isolated,
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
    return _report_or_link(
        results, detail=detail, limit=limit, kind="scan", seed=str(root)
    )


@mcp.tool(annotations=READ_ONLY)
async def report(
    file: str,
    profile: str | None = None,
    detail: str = "compact",
    limit: int | None = 50,
) -> dict | ResourceLink:
    """Audit a single file statelessly (manifest + findings). ``detail``: summary|compact|full
    (default compact — hoists rule metadata, slims findings, drops evidence; use finding_detail
    to recover a finding's evidence). ``limit`` (compact only, default 50) caps to the worst-N
    findings with the surplus under `omitted`. ``detail='full'`` is returned as a ResourceLink to
    fetch on demand rather than inline."""
    validate_detail(detail)
    path = _require_file(file)
    results = await audit_target(path, profile=profile)
    return _report_or_link(
        results, detail=detail, limit=limit, kind="report", seed=str(path)
    )


@mcp.tool(annotations=READ_ONLY)
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
        for f in await index.findings.cached(rel, rule_id):
            if f.line == line:
                return f.model_dump(mode="json")
    results = await audit_target(path, root=root, apply_ignores=False)
    for r in results:
        for f in r.findings:
            if f.rule_id == rule_id and f.line == line:
                return f.model_dump(mode="json")
    raise ToolError(f"no {rule_id} finding at {file}:{line}")


@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=READ_ONLY)
def discover(
    path: str = ".", limit: int | None = None, role: str | None = None
) -> dict:
    """List auditable files under a path with their classified role. Returns {total, shown,
    roles, files}: ``roles`` is a role→count histogram (a cheap overview without the full list),
    ``total`` the full match count, ``files`` the (capped) listing. ``role`` filters to one role
    (e.g. 'source'|'test'); ``limit`` caps ``files`` — the list can be enormous on a big repo, so
    it is unbounded only when you ask for it (limit=null)."""
    root = find_root(Path(path))
    settings = load_config(root)
    classifier = RoleClassifier(settings.role_globs)
    discovery = FileDiscovery(
        root,
        exclude_globs=tuple(settings.exclude),
        respect_gitignore=settings.respect_gitignore,
    )
    matched: list[dict] = []
    roles: dict[str, int] = {}
    for p in discovery.files(Path(path)):
        rel = str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
        classified = classifier.classify(
            rel, p.read_text(encoding="utf-8", errors="replace")
        ).value
        if role and classified != role:
            continue
        roles[classified] = roles.get(classified, 0) + 1
        matched.append({"file": rel, "role": classified})
    shown = matched if limit is None else matched[:limit]
    return {
        "total": len(matched),
        "shown": len(shown),
        "roles": roles,
        "files": shown,
    }


@mcp.tool(annotations=READ_ONLY)
async def aggregate(path: str = ".") -> ResourceLink:
    """Roll up the index into a consolidated AUDIT.md (run scan with incremental=True first).
    The report is large, so it is returned as a ResourceLink — read that resource for the
    markdown rather than receiving it inline."""
    root = find_root(Path(path))
    async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
        markdown = await AuditAggregator(index).markdown()
    return publish(
        "audit",
        str(root),
        markdown,
        mime_type="text/markdown",
        name="AUDIT.md",
        description=f"Consolidated audit report ({len(markdown)} bytes).",
    )
