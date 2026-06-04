"""ManifestAuditor — runs the enabled supply-chain detectors over a dependency/build manifest,
dispatched by filename (``package.json`` today). No parse tree or symbol manifest; config/role
severity overrides are applied in one place, mirroring the Python/TS/shell auditors."""

from typing import TYPE_CHECKING, ClassVar

from auditor.languages.base import LanguageAuditor
from auditor.languages.manifest import detectors as _detectors  # noqa: F401
from auditor.languages.manifest.base import ManifestContext
from auditor.models import FileRole, ScanResult

if TYPE_CHECKING:
    from auditor.config import ResolvedConfig


class ManifestAuditor(LanguageAuditor):
    language: ClassVar[str] = "manifest"
    extensions: ClassVar[tuple[str, ...]] = ()
    filenames: ClassVar[tuple[str, ...]] = ("package.json",)

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
        ctx = ManifestContext(
            file_path=file_path, source=source, role=role, config=config
        )

        findings, skipped = self._collect(ctx, config, rule_ids)
        return ScanResult(
            file=file_path,
            language="manifest",
            role=role,
            manifest=[],
            findings=findings,
            skipped_rules=skipped,
        )
