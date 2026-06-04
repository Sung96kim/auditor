"""SARIF 2.1.0 reporter — drops findings into CI / GitHub code scanning / SAST dashboards."""

import json
from typing import ClassVar

from auditor.baseline import finding_fingerprint
from auditor.models import Finding, ScanResult, Severity
from auditor.registry import REGISTRY
from auditor.reporters.base import Reporter

_SARIF_LEVEL = {
    Severity.BLOCKING: "error",
    Severity.HIGH: "error",
    Severity.MEDIUM: "warning",
    Severity.LOW: "note",
}


class SarifReporter(Reporter):
    format: ClassVar[str] = "sarif"

    def render(self, results: list[ScanResult]) -> str:
        rule_ids = {f.rule_id for r in results for f in r.findings}
        sarif = {
            "version": "2.1.0",
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "auditor",
                            "informationUri": "https://github.com/Sung96kim/auditor",
                            "rules": [
                                _rule_descriptor(rid) for rid in sorted(rule_ids)
                            ],
                        }
                    },
                    "results": [
                        _result(r.file, f) for r in results for f in r.findings
                    ],
                }
            ],
        }
        return json.dumps(sarif, indent=2)


def _rule_descriptor(rule_id: str) -> dict:
    det = REGISTRY.detector(rule_id) if rule_id in REGISTRY.rule_ids() else None
    desc: dict = {"id": rule_id, "name": rule_id}
    if det is not None:
        desc["properties"] = {
            "category": str(det.category),
            "tags": list(det.standard_refs),
        }
    return desc


def _result(file: str, f: Finding) -> dict:
    return {
        "ruleId": f.rule_id,
        "level": _SARIF_LEVEL.get(f.severity, "warning"),
        "message": {"text": f.message},
        # line-independent fingerprint (file + rule + offending text) so code-scanning dedupes the
        # same finding across runs/commits even when surrounding lines shift
        "partialFingerprints": {"auditorFingerprint/v1": finding_fingerprint(file, f)},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": file},
                    "region": {"startLine": max(f.line, 1)},
                }
            }
        ],
    }
