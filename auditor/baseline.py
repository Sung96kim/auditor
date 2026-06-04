"""Baseline support: snapshot today's findings, then on later scans report only the *new* ones.

A baseline stores a line-independent fingerprint per finding — ``(file, rule_id, hash(evidence))``
— so a finding survives line shifts (edits elsewhere in the file) but a genuinely new issue (new
offending text) is still surfaced. This is what makes the auditor adoptable on a large existing
repo: accept the current findings as the baseline, then gate only on what you add."""

import hashlib
from pathlib import Path

from pydantic import BaseModel, Field

from auditor.models import Finding, ScanResult


def finding_fingerprint(file: str, finding: Finding) -> str:
    """A stable, line-independent identity: file + rule + a hash of the offending text. Survives
    line moves; changed/new offending text yields a new fingerprint (reported as new)."""
    evidence = (finding.evidence or "").strip()
    return hashlib.sha256(
        f"{file}\x00{finding.rule_id}\x00{evidence}".encode()
    ).hexdigest()


class Baseline(BaseModel):
    """A recorded set of accepted findings, by fingerprint. Stored as JSON."""

    version: int = 1
    fingerprints: list[str] = Field(default_factory=list)  # sorted + deduped

    @classmethod
    def from_results(cls, results: list[ScanResult]) -> "Baseline":
        fps = {finding_fingerprint(r.file, f) for r in results for f in r.findings}
        return cls(fingerprints=sorted(fps))

    @classmethod
    def load(cls, path: Path) -> "Baseline":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def write(self, path: Path) -> int:
        """Persist the baseline; return the number of fingerprints recorded."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return len(self.fingerprints)

    def filter(self, results: list[ScanResult]) -> int:
        """Drop already-baselined findings from each result in place; return how many were hidden."""
        known = set(self.fingerprints)
        hidden = 0
        for result in results:
            kept: list[Finding] = []
            for finding in result.findings:
                if finding_fingerprint(result.file, finding) in known:
                    hidden += 1
                else:
                    kept.append(finding)
            result.findings = kept
        return hidden
