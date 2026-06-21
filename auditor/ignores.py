"""Persistent ignores: manual, db-backed suppression of findings at repo / file / line scope.

Complements in-file ``noqa`` and the snapshot ``baseline.json``. An ignore is always keyed by
``rule_id``; its scope widens as ``file``/``line`` are left unset. Line-level ignores match by a
hash of the offending text first (so they follow the code when lines shift) and fall back to the
literal line. The rows live in the shared index db (``auditor.database``); this module is the pure,
db-free matching logic over them."""

import hashlib

from pydantic import BaseModel, Field

from auditor.models import Finding, ScanResult


def evidence_hash(evidence: str) -> str:
    """Stable hash of a finding's offending text — the drift-tolerant half of a line-level match
    (rule_id + file are already pinned by the ignore row)."""
    return hashlib.sha256(evidence.strip().encode()).hexdigest()


class IgnoreRule(BaseModel):
    """One ignore row. ``file is None`` = repo-wide; ``file`` set, ``line is None`` = file-wide;
    both set = line-level (``evidence_hash`` captured from the finding at add time, if it existed)."""

    id: int
    rule_id: str
    file: str | None = None
    line: int | None = None
    evidence_hash: str | None = None
    reason: str | None = None

    def matches(self, file: str, finding: Finding) -> bool:
        if self.rule_id != finding.rule_id:
            return False
        if self.file is not None and self.file != file:
            return False
        if self.line is None:
            return True  # repo- or file-wide: every line of the in-scope file(s)
        # line-level: prefer the evidence hash (survives line shifts), else the literal line
        if self.evidence_hash is not None:
            return self.evidence_hash == evidence_hash(finding.evidence or "")
        return self.line == finding.line


class IgnoreList(BaseModel):
    """The set of ignore rules for one repo, with the filter applied during a scan."""

    rules: list[IgnoreRule] = Field(default_factory=list)

    @classmethod
    def from_rows(cls, rows: list[dict]) -> "IgnoreList":
        """Build from ``IndexStore.ignores()`` rows."""
        return cls(
            rules=[
                IgnoreRule(
                    id=r["id"],
                    rule_id=r["rule_id"],
                    file=r["file"],
                    line=r["line"],
                    evidence_hash=r["evidence_hash"],
                    reason=r.get("reason"),
                )
                for r in rows
            ]
        )

    def filter(self, results: list[ScanResult], *, show_ignored: bool = False) -> int:
        """Set each result's ``ignored`` count and drop the matched findings in place (unless
        ``show_ignored``, which leaves them but still counts). Returns the total matched."""
        total = 0
        for result in results:
            kept: list[Finding] = []
            matched = 0
            for finding in result.findings:
                if any(rule.matches(result.file, finding) for rule in self.rules):
                    matched += 1
                    if show_ignored:
                        kept.append(finding)
                else:
                    kept.append(finding)
            result.ignored = matched
            result.findings = kept
            total += matched
        return total
