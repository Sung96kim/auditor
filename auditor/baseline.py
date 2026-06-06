"""Baseline support: snapshot today's findings, then on later scans report only the *new* ones.

A baseline stores a line-independent fingerprint per finding — ``(file, rule_id, hash(evidence))``
— so a finding survives line shifts (edits elsewhere in the file) but a genuinely new issue (new
offending text) is still surfaced. This is what makes the auditor adoptable on a large existing
repo: accept the current findings as the baseline, then gate only on what you add.

Fingerprints are stored as a **multiset** (one entry per occurrence), so when several distinct
findings in a file legitimately share a snippet — e.g. three untyped ``def __init__(`` — all three
are recorded and a fourth, newly-added one still surfaces. A set would collapse them and silently
hide the new occurrence. ``filter`` therefore hides up to the recorded count per fingerprint."""

import hashlib
from collections import Counter
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
    """A recorded multiset of accepted findings, by fingerprint. Stored as JSON — ``fingerprints``
    is sorted with one entry per baselined occurrence (repeats are meaningful)."""

    version: int = 1
    fingerprints: list[str] = Field(default_factory=list)  # sorted; one entry per occurrence

    @classmethod
    def from_results(cls, results: list[ScanResult]) -> "Baseline":
        fps = [finding_fingerprint(r.file, f) for r in results for f in r.findings]
        return cls(fingerprints=sorted(fps))

    @classmethod
    def load(cls, path: Path) -> "Baseline":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def write(self, path: Path) -> int:
        """Persist the baseline; return the number of finding occurrences recorded."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return len(self.fingerprints)

    def filter(self, results: list[ScanResult]) -> int:
        """Drop already-baselined findings from each result in place; return how many were hidden.
        Hides up to the recorded count per fingerprint, so an occurrence beyond what was baselined
        (e.g. a newly-added finding sharing a snippet with baselined ones) still surfaces."""
        budget = Counter(self.fingerprints)
        hidden = 0
        for result in results:
            kept: list[Finding] = []
            for finding in result.findings:
                fp = finding_fingerprint(result.file, finding)
                if budget[fp] > 0:
                    budget[fp] -= 1
                    hidden += 1
                else:
                    kept.append(finding)
            result.findings = kept
        return hidden
