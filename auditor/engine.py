"""Scan orchestration.

``ScanEngine`` owns the resolved root/settings/deps/index for a run and audits files with
the per-rule incremental cache. Module-level ``scan_file`` / ``scan_path`` are convenience
entry points that build an engine for a target.
"""

import asyncio
import re
import time
import tomllib
from pathlib import Path

from loguru import logger

from auditor import crossfile
from auditor.config import AuditorSettings, ResolvedConfig, load_config
from auditor.discovery import FileDiscovery, find_root
from auditor.fingerprints import content_hash, rule_fingerprint
from auditor.ignores import IgnoreList
from auditor.index import IndexStore
from auditor.languages.base import LanguageAuditor
from auditor.models import FileRole, Finding, IndexEntry, ScanResult, SkippedRule
from auditor.noqa import filter_findings
from auditor.paths import index_db_path, repo_key
from auditor.registry import REGISTRY
from auditor.roles import RoleClassifier

_DEP_NAME = re.compile(r"^[A-Za-z0-9_.-]+")
#: max files audited concurrently — overlaps index I/O on re-scans (CPU work stays GIL-serialized)
_SCAN_CONCURRENCY = 8


def project_deps(root: Path) -> frozenset[str]:
    """Top-level dependency names from the nearest pyproject.toml (e.g. 'pydantic')."""
    pp = root / "pyproject.toml"
    if not pp.exists():
        return frozenset()
    data = tomllib.loads(pp.read_text())
    project = data.get("project", {})
    specs = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        specs.extend(group)
    names = {
        m.group(0).lower() for spec in specs if (m := _DEP_NAME.match(spec.strip()))
    }
    return frozenset(names)


class ScanEngine:
    """Audits files under one resolved root, with config, project facts, and an optional cache."""

    def __init__(
        self, root: Path, settings: AuditorSettings, *, index: IndexStore | None = None
    ) -> None:
        self.root = root
        self.settings = settings
        self.index = index
        self.deps = project_deps(root)
        self.roles = RoleClassifier(settings.role_globs)

    @classmethod
    def for_target(
        cls,
        target: Path,
        *,
        settings: AuditorSettings | None = None,
        index: IndexStore | None = None,
    ) -> "ScanEngine":
        root = find_root(target)
        settings = settings if settings is not None else load_config(root)
        return cls(root, settings, index=index)

    def rel(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.root.resolve()))
        except ValueError:
            return str(path)

    async def scan_file(self, path: Path) -> ScanResult:
        res = await self._scan_file(path)
        _log_file(res)
        return res

    async def _scan_file(self, path: Path) -> ScanResult:
        source = path.read_text(encoding="utf-8", errors="replace")
        rel = self.rel(path)
        role = self.roles.classify(rel, source)
        rc = ResolvedConfig(self.settings, role=role, rel_path=rel)

        lang_cls = REGISTRY.language_for_path(rel)
        if lang_cls is None:
            return ScanResult(file=rel, language="unknown", role=role)
        auditor = lang_cls()

        enabled, skipped = self._partition_rules(rc, lang_cls.language)
        sha = content_hash(source)

        if self.index is not None:
            return await self._scan_cached(
                auditor, rel, source, sha, role, rc, enabled, skipped
            )

        res = self._audit(auditor, rel, source, role, rc, list(enabled))
        res.skipped_rules.extend(skipped)
        return res

    async def scan_path(self, target: Path) -> list[ScanResult]:
        files = FileDiscovery(
            self.root, exclude_globs=tuple(self.settings.exclude)
        ).files(target)
        logger.opt(colors=True).info(
            "<light-black>scanning</light-black> <bold>{}</bold> <light-black>files in</light-black> <bold>{}</bold> <light-black>· paths shown relative to</light-black> <bold>{}</bold>",
            len(files),
            target,
            self.root.name,
        )
        results = await self._scan_files(files)
        if self.index is not None:
            # reconcile: drop index rows for files under this scan's scope that no longer exist,
            # so a deleted file leaves no stale findings/shapes (which would otherwise leak into
            # `aggregate` and produce phantom cross-file dup findings). Runs before the cross-file
            # pass so it doesn't group shapes from removed files.
            pruned = await self.index.prune(
                {r.file for r in results}, prefix=self._scope_prefix(target)
            )
            if pruned:
                logger.opt(colors=True).info(
                    "<light-black>pruned {} removed file(s) from the index</light-black>",
                    len(pruned),
                )
            # Always recompute the cross-file pass when an index is present — it operates on the
            # whole shapes table (not just this scan's files), so even a single-file rescan must
            # re-run it to clear a dup finding whose partner was just deleted/pruned.
            if len(results) > 1:
                logger.opt(colors=True).info(
                    "<light-black>cross-file pass over {} files</light-black>",
                    len(results),
                )
            await self._apply_crossfile(results)
        elif len(results) > 1:
            # no index (stateless dir scan): run the cross-file pass in memory so `scan .` still
            # surfaces XFILE dup findings, just without the cache
            self._apply_crossfile_in_memory(results)
        _log_summary(results)
        return results

    async def _scan_files(self, files: list[Path]) -> list[ScanResult]:
        """Audit files with bounded concurrency, returned in ``files`` order. The per-file parse +
        detector work is CPU-bound (GIL), so this mainly overlaps the index ``await``s on
        incremental re-scans; the single index worker serializes the writes safely. (True CPU
        parallelism would need a process pool — a larger change deferred for stability.)"""
        if len(files) <= 1:
            return [await self.scan_file(p) for p in files]
        sem = asyncio.Semaphore(_SCAN_CONCURRENCY)

        async def one(path: Path) -> ScanResult:
            async with sem:
                return await self.scan_file(path)

        return list(await asyncio.gather(*(one(p) for p in files)))

    def _scope_prefix(self, target: Path) -> str:
        """Path prefix (root-relative) the scan covered, so pruning stays inside it. ``""`` for a
        whole-repo scan; ``"pkg/"`` for a subdirectory."""
        rel = self.rel(target)
        return "" if rel in ("", ".") else rel.rstrip("/") + "/"

    # --- internals --------------------------------------------------------

    def _partition_rules(
        self, rc: ResolvedConfig, language: str
    ) -> tuple[dict[str, str], list[SkippedRule]]:
        enabled: dict[str, str] = {}
        skipped: list[SkippedRule] = []
        for det in REGISTRY.detectors_for_language(language):
            if det.repo_level:
                continue  # cross-file rules are computed by the repo-level pass
            eff = rc.effective(det.rule_id)
            if eff.enabled:
                enabled[det.rule_id] = rule_fingerprint(det.rule_id, eff)
            else:
                skipped.append(
                    SkippedRule(
                        rule_id=det.rule_id, reason=eff.skipped_reason or "disabled"
                    )
                )
        return enabled, skipped

    def _audit(
        self,
        auditor: LanguageAuditor,
        rel: str,
        source: str,
        role: FileRole,
        rc: ResolvedConfig,
        rule_ids: list[str],
    ) -> ScanResult:
        res = auditor.audit(
            file_path=rel,
            source=source,
            role=role,
            config=rc,
            project_deps=self.deps,
            package_root=str(self.root),
            rule_ids=rule_ids,
        )
        # Suppress before the index stores the findings, so cached re-scans stay consistent.
        if self.settings.respect_noqa:
            res.findings, res.suppressed = filter_findings(
                source, res.findings, language=res.language
            )
        return res

    async def _scan_cached(
        self,
        auditor: LanguageAuditor,
        rel: str,
        source: str,
        sha: str,
        role: FileRole,
        rc: ResolvedConfig,
        enabled: dict[str, str],
        skipped: list[SkippedRule],
    ) -> ScanResult:
        index = self.index
        cached_sha = await index.file_sha(rel)
        missed = [
            rid
            for rid, fp in enabled.items()
            if cached_sha != sha or await index.rule_fingerprint(rel, rid) != fp
        ]

        if not missed:  # nothing to re-run
            findings = [
                f for rid in enabled for f in await index.cached_findings(rel, rid)
            ]
            findings.sort(key=lambda f: (f.line, f.rule_id))
            # genuinely cached only if a prior scan recorded this file; a file with no
            # enabled rules (e.g. role-excluded) has nothing to do, not a cache hit.
            cached = enabled != {} and cached_sha is not None
            return ScanResult(
                file=rel,
                language=auditor.language,
                role=role,
                findings=findings,
                cached=cached,
                skipped_rules=skipped,
            )

        res = self._audit(auditor, rel, source, role, rc, missed)
        now = time.time()
        await index.upsert_file(
            IndexEntry(
                path=rel,
                sha256=sha,
                lines=len(source.splitlines()),
                language=auditor.language,
                role=role,
                last_scanned=now,
            )
        )
        by_rule: dict[str, list[Finding]] = {rid: [] for rid in missed}
        for f in res.findings:
            by_rule.setdefault(f.rule_id, []).append(f)
        for rid in missed:
            await index.record_rule(rel, rid, enabled[rid], by_rule.get(rid, []), now)

        await index.clear_shapes(rel)
        rows = auditor.shapes(
            source,
            method_min_statements=self.settings.threshold.dry.xfile_method_min_statements,
        )
        if rows:
            await index.add_shapes(
                [(s.shape_hash, s.kind, rel, s.symbol, s.line) for s in rows]
            )

        hit = [rid for rid in enabled if rid not in missed]
        findings = list(res.findings) + [
            f for rid in hit for f in await index.cached_findings(rel, rid)
        ]
        findings.sort(key=lambda f: (f.line, f.rule_id))
        return ScanResult(
            file=rel,
            language=auditor.language,
            role=role,
            manifest=res.manifest,
            findings=findings,
            cached=False,
            skipped_rules=skipped + res.skipped_rules,
            suppressed=res.suppressed,
        )

    async def _apply_crossfile(self, results: list[ScanResult]) -> None:
        self._merge_xfindings(
            results,
            await crossfile.run(
                self.index,
                settings_modules=self.settings.settings_modules,
                settings_cohesion_on=self.settings.settings_cohesion,
            ),
        )

    def _apply_crossfile_in_memory(self, results: list[ScanResult]) -> None:
        """Cross-file dedup without an index: compute shapes in memory and group them, so a
        stateless ``scan <dir>`` surfaces XFILE findings (the index path persists them instead)."""
        min_stmts = self.settings.threshold.dry.xfile_method_min_statements
        shape_rows: list[dict] = []
        roles: dict[str, str] = {}
        for res in results:
            roles[res.file] = res.role.value
            lang_cls = REGISTRY.language_for_path(res.file)
            if lang_cls is None:
                continue
            try:
                source = (self.root / res.file).read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                continue
            for s in lang_cls().shapes(source, method_min_statements=min_stmts):
                shape_rows.append(
                    {
                        "shape_hash": s.shape_hash,
                        "kind": s.kind,
                        "path": res.file,
                        "symbol": s.symbol,
                        "line": s.line,
                    }
                )
        self._merge_xfindings(
            results,
            crossfile.run_in_memory(
                shape_rows,
                roles,
                settings_modules=self.settings.settings_modules,
                settings_cohesion_on=self.settings.settings_cohesion,
            ),
        )

    def _merge_xfindings(
        self, results: list[ScanResult], xfindings: dict[str, list[Finding]]
    ) -> None:
        for res in results:
            extra = xfindings.get(res.file)
            if not extra:
                continue
            if self.settings.respect_noqa:
                source = (self.root / res.file).read_text(
                    encoding="utf-8", errors="replace"
                )
                extra, dropped = filter_findings(source, extra, language=res.language)
                res.suppressed += dropped
            res.findings.extend(extra)
            res.findings.sort(key=lambda f: (f.line, f.rule_id))


async def audit_target(
    target: Path,
    *,
    incremental: bool = False,
    no_index: bool = False,
    strict_tests: bool = False,
    allow_local_plugins: bool = False,
    profile: str | None = None,
    exclude: tuple[str, ...] = (),
    no_noqa: bool = False,
    report_only: set[str] | None = None,
    root: Path | None = None,
    apply_ignores: bool = True,
    show_ignored: bool = False,
) -> list[ScanResult]:
    """High-level entry used by the CLI and MCP server: resolve root + config, optionally
    use the on-disk cache, and audit a file or directory. ``profile`` overrides the repo's
    ``extends`` for the run (e.g. ``"strict"`` to enable the OOP/composition rules);
    ``exclude`` adds ad-hoc ignore globs on top of the configured ``exclude``; ``no_noqa``
    ignores in-file noqa directives (e.g. an un-silenceable security sweep). ``report_only``
    (paths relative to root) scopes the *returned* results to those files — the whole repo is
    still scanned so cross-file/repo-global rules stay correct (e.g. a git-diff scan). ``root``
    pins the project root explicitly (default: nearest ``.git``/``pyproject.toml``/``.auditor``)."""
    root = root or find_root(target)
    settings = load_config(
        root, profile=profile, allow_local_plugins=allow_local_plugins
    )
    updates: dict[str, object] = {}
    if strict_tests:
        updates["test_mode"] = "strict"
    if no_noqa:
        updates["respect_noqa"] = False
    if exclude:
        updates["exclude"] = [*settings.exclude, *exclude]
    if updates:
        settings = settings.model_copy(update=updates)

    async def _run(engine: ScanEngine) -> list[ScanResult]:
        results = (
            await engine.scan_path(target)
            if target.is_dir()
            else [await engine.scan_file(target)]
        )
        if report_only is not None:
            results = [r for r in results if r.file in report_only]
        return results

    if incremental and not no_index and target.is_dir():
        async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
            await index.register(root.name, time.time())
            results = await _run(ScanEngine(root, settings, index=index))
            if apply_ignores:
                await _apply_ignores(index, results, show_ignored=show_ignored)
            return results
    results = await _run(ScanEngine(root, settings))
    if apply_ignores:
        await _apply_ignores_standalone(root, results, show_ignored=show_ignored)
    return results


async def _apply_ignores(
    index: IndexStore, results: list[ScanResult], *, show_ignored: bool
) -> None:
    rows = await index.ignores()
    if rows:
        IgnoreList.from_rows(rows).filter(results, show_ignored=show_ignored)


async def _apply_ignores_standalone(
    root: Path, results: list[ScanResult], *, show_ignored: bool
) -> None:
    """Apply a repo's ignores on a stateless (non-incremental) scan. Skips opening — and thus
    creating — the shared db when it doesn't exist yet (no db ⇒ no ignores)."""
    if not index_db_path().exists():
        return
    async with await IndexStore.connect(index_db_path(), repo_key(root)) as index:
        await _apply_ignores(index, results, show_ignored=show_ignored)


async def finding_evidence_at(
    root: Path, file: str, rule_id: str, line: int
) -> str | None:
    """The offending text of the finding at ``file:line`` for ``rule_id`` in the current source,
    if one exists — used to snapshot a line-level ignore so it can follow the code on later edits.
    Scans without applying ignores so an existing ignore doesn't hide the finding being captured."""
    abs_file = root / file
    if not abs_file.is_file():
        return None
    results = await audit_target(abs_file, root=root, apply_ignores=False)
    for result in results:
        for finding in result.findings:
            if finding.rule_id == rule_id and finding.line == line:
                return finding.evidence
    return None


# loguru color tag per severity (stripped automatically when the sink isn't a TTY)
_SEV_COLOR = {
    "blocking": "red",
    "high": "light-red",
    "medium": "yellow",
    "low": "cyan",
    "suggestion": "light-black",
}
_clog = logger.opt(colors=True)


def _log_file(res: ScanResult) -> None:
    if res.language == "unknown":
        _clog.debug(
            "<light-black>· {}  (unrecognized language)</light-black>", res.file
        )
        return
    if res.cached:
        tag = "<light-black>cached</light-black>"
    elif res.findings:
        tag = f"<light-red>{len(res.findings)} findings</light-red>"
    else:
        tag = "<green>clean</green>"
    _clog.info(
        "<bold>{}</bold> <light-black>·</light-black> <light-black>{}</light-black> "
        "<light-black>·</light-black> " + tag,
        res.file,
        res.role.value,
    )
    notes = []
    if res.suppressed:
        notes.append(f"{res.suppressed} suppressed")
    if res.skipped_rules:
        notes.append(
            f"{len(res.skipped_rules)} rules skipped ({res.skipped_rules[0].reason})"
        )
    if notes:
        _clog.debug("<light-black>    {}</light-black>", " · ".join(notes))
    for f in res.findings:
        color = _SEV_COLOR.get(f.severity.value, "white")
        _clog.trace(
            f"    <{color}>{{:<10}}</{color}> <light-black>L{{:<4}}</light-black> {{}}",
            f.severity.value,
            f.line,
            f.rule_id,
        )


def _log_summary(results: list[ScanResult]) -> None:
    findings = sum(len(r.findings) for r in results)
    cached = sum(1 for r in results if r.cached)
    suppressed = sum(r.suppressed for r in results)
    sep = " <light-black>·</light-black> "
    _clog.info(
        "<bold>done</bold>"
        + sep
        + "{} files"
        + sep
        + "<light-red>{} findings</light-red>"
        + sep
        + "{} cached"
        + sep
        + "{} suppressed",
        len(results),
        findings,
        cached,
        suppressed,
    )
