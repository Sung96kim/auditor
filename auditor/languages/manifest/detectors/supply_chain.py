"""Supply-chain manifest detectors — install-time hooks and dependency hygiene.

The install-hook check targets npm lifecycle scripts (``preinstall``/``install``/``postinstall``),
the foremost npm supply-chain execution vector: they run automatically on ``npm install``, so a
compromised dependency runs code the moment it's added. Surfaced as a candidate (legitimate
packages use ``postinstall`` too) with the script body as evidence for the agent to judge."""

from typing import ClassVar

from auditor.languages.manifest.base import NPM, ManifestContext, ManifestDetector
from auditor.models import Category, Finding, Severity, VerdictKind

# npm lifecycle scripts that execute automatically during `npm install`
_INSTALL_HOOKS = ("preinstall", "install", "postinstall")


class InstallHook(ManifestDetector):
    rule_id: ClassVar[str] = "MF-SUPPLY-INSTALL-HOOK"
    category: ClassVar[Category] = Category.SUPPLY_CHAIN
    default_severity: ClassVar[Severity] = Severity.MEDIUM
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE

    def run(self, ctx: ManifestContext) -> list[Finding]:
        if ctx.manifest_type != NPM:
            return []
        scripts = ctx.data.get("scripts")
        if not isinstance(scripts, dict):
            return []
        out: list[Finding] = []
        for hook in _INSTALL_HOOKS:
            body = scripts.get(hook)
            if not isinstance(body, str) or not body.strip():
                continue  # absent, non-string, or an empty hook — nothing runs on install
            out.append(
                self.make_finding(
                    ctx,
                    line=ctx.line_of(f'"{hook}"'),
                    message=f"`{hook}` runs automatically on `npm install` — an install-time hook (supply-chain execution point)",
                    suggestion="confirm this lifecycle script is necessary and its command is trusted; prefer explicit, reviewed build steps over auto-run install hooks",
                    evidence=body.strip()[:200],
                )
            )
        return out
