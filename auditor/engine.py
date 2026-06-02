"""Scan orchestration.

``ScanEngine`` owns the resolved root/settings/deps/index for a run and audits files with
the per-rule incremental cache. Module-level ``scan_file`` / ``scan_path`` are convenience
entry points that build an engine for a target.
"""

import re
import time
import tomllib
from pathlib import Path

from auditor import crossfile
from auditor.config import AuditorSettings, ResolvedConfig, load_config
from auditor.discovery import FileDiscovery, find_root
from auditor.fingerprints import content_hash, rule_fingerprint
from auditor.index import IndexStore
from auditor.languages.base import LanguageAuditor
from loguru import logger

from auditor.models import FileRole, Finding, IndexEntry, ScanResult, SkippedRule
from auditor.noqa import filter_findings
from auditor.registry import REGISTRY
from auditor.roles import RoleClassifier

_DEP_NAME = re.compile(r"^[A-Za-z0-9_.-]+")


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
            "<light-black>scanning</light-black> <bold>{}</bold> <light-black>files under {}</light-black>",
            len(files),
            self.rel(target) or ".",
        )
        results = [await self.scan_file(p) for p in files]
        if self.index is not None and len(results) > 1:
            logger.opt(colors=True).info(
                "<light-black>cross-file pass over {} files</light-black>", len(results)
            )
            await self._apply_crossfile(results)
        _log_summary(results)
        return results

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
            res.findings, res.suppressed = filter_findings(source, res.findings)
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
        rows = auditor.shapes(source)
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
        xfindings = await crossfile.run(self.index)
        for res in results:
            extra = xfindings.get(res.file)
            if not extra:
                continue
            if self.settings.respect_noqa:
                source = (self.root / res.file).read_text(
                    encoding="utf-8", errors="replace"
                )
                extra, dropped = filter_findings(source, extra)
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
) -> list[ScanResult]:
    """High-level entry used by the CLI and MCP server: resolve root + config, optionally
    use the on-disk cache, and audit a file or directory. ``profile`` overrides the repo's
    ``extends`` for the run (e.g. ``"strict"`` to enable the OOP/composition rules);
    ``exclude`` adds ad-hoc ignore globs on top of the configured ``exclude``; ``no_noqa``
    ignores in-file noqa directives (e.g. an un-silenceable security sweep)."""
    root = find_root(target)
    settings = load_config(root, profile=profile, allow_local_plugins=allow_local_plugins)
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
        if target.is_dir():
            return await engine.scan_path(target)
        return [await engine.scan_file(target)]

    if incremental and not no_index and target.is_dir():
        async with await IndexStore.connect(root / ".auditor" / "index.db") as index:
            return await _run(ScanEngine(root, settings, index=index))
    return await _run(ScanEngine(root, settings))


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
        _clog.debug("<light-black>· {}  (unrecognized language)</light-black>", res.file)
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
        notes.append(f"{len(res.skipped_rules)} rules skipped ({res.skipped_rules[0].reason})")
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
        "<bold>done</bold>" + sep + "{} files" + sep + "<light-red>{} findings</light-red>"
        + sep + "{} cached" + sep + "{} suppressed",
        len(results),
        findings,
        cached,
        suppressed,
    )
