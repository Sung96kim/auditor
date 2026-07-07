"""Shell style detectors: long standalone-comment blocks (SH-STYLE-LONG-COMMENT)."""

from typing import ClassVar

from auditor.languages.bash.base import ShAuditContext, ShDetector
from auditor.languages.comment_blocks import ShellCommentBlocks
from auditor.models import Category, Finding, Severity, VerdictKind


class LongComment(ShDetector):
    rule_id: ClassVar[str] = "SH-STYLE-LONG-COMMENT"
    category: ClassVar[Category] = Category.STYLE
    default_severity: ClassVar[Severity] = Severity.LOW
    verdict_kind: ClassVar[VerdictKind] = VerdictKind.CANDIDATE
    language: ClassVar[str] = "shell"
    _analyzer: ClassVar[ShellCommentBlocks] = ShellCommentBlocks()

    def run(self, ctx: ShAuditContext) -> list[Finding]:
        threshold = ctx.config.effective(
            self.rule_id
        ).threshold.size.comment_block_max_lines
        return [
            self.make_finding(
                ctx,
                line=block.anchor,
                message=f"comment block is {block.prose_count} prose lines (> {threshold}); "
                "tighten it",
                evidence=f"{block.prose_count} prose lines",
                suggestion="delete restating comments; keep only what the code can't say",
            )
            for block in self._analyzer.blocks(
                ctx.source, ctx.lines, threshold=threshold
            )
        ]
