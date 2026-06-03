"""BashAuditor — runs the enabled shell detectors over a ``.sh``/``.bash`` file. No parse tree
and no manifest (shell has no class/function model worth indexing here); config/role severity
overrides are applied in one place, mirroring the Python/TS auditors."""

from typing import TYPE_CHECKING, ClassVar

from auditor.languages.base import LanguageAuditor
from auditor.languages.bash import detectors as _detectors  # noqa: F401
from auditor.languages.bash.base import ShAuditContext, ShDetector
from auditor.models import FileRole, Finding, ScanResult, SkippedRule
from auditor.registry import REGISTRY

if TYPE_CHECKING:
    from auditor.config import ResolvedConfig


class BashAuditor(LanguageAuditor):
    language: ClassVar[str] = "shell"
    extensions: ClassVar[tuple[str, ...]] = (".sh", ".bash")

    def audit(
        self,
        *,
        file_path: str,
        source: str,
        role: FileRole,
        config: "ResolvedConfig",
        rule_ids: list[str] | None = None,
        **_: object,
    ) -> ScanResult:
        ctx = ShAuditContext(
            file_path=file_path, source=source, role=role, config=config
        )

        detector_classes = REGISTRY.detectors_for_language("shell")
        if rule_ids is not None:
            wanted = set(rule_ids)
            detector_classes = [d for d in detector_classes if d.rule_id in wanted]

        findings: list[Finding] = []
        skipped: list[SkippedRule] = []
        for cls in detector_classes:
            eff = config.effective(cls.rule_id)
            if not eff.enabled:
                skipped.append(
                    SkippedRule(
                        rule_id=cls.rule_id, reason=eff.skipped_reason or "disabled"
                    )
                )
                continue
            detector: ShDetector = cls()
            for f in detector.run(ctx):
                findings.append(
                    f.model_copy(
                        update={
                            "severity": eff.severity,
                            "verdict_kind": eff.verdict_kind,
                        }
                    )
                )

        findings.sort(key=lambda f: (f.line, f.rule_id))
        return ScanResult(
            file=file_path,
            language="shell",
            role=role,
            manifest=[],
            findings=findings,
            skipped_rules=skipped,
        )
