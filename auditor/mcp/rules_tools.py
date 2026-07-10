# auditor: skip-file: PY-TYPING-UNTYPED-DICT  (MCP tool surface — JSON payloads by contract)
"""rules_list — the detector registry/metadata MCP tool."""

from auditor.mcp.helpers import READ_ONLY
from auditor.mcp.server import mcp
from auditor.registry import REGISTRY


@mcp.tool(annotations=READ_ONLY)
def rules_list(
    category: str | None = None,
    standard: str | None = None,
    framework: str | None = None,
) -> list[dict]:
    """Enumerate detector rules, optionally filtered by category, standard (bandit/owasp), or
    framework (e.g. pytest)."""
    rows = []
    for rid in sorted(REGISTRY.rule_ids()):
        det = REGISTRY.detector(rid)
        if category and str(det.category) != category:
            continue
        if framework and getattr(det, "framework", None) != framework:
            continue
        refs = list(det.standard_refs)
        if standard and not any(r.startswith(f"{standard}:") for r in refs):
            continue
        rows.append(
            {
                "rule_id": rid,
                "category": str(det.category),
                "framework": getattr(det, "framework", None),
                "default_severity": det.default_severity.value,
                "verdict_kind": det.verdict_kind.value,
                "standard_refs": refs,
            }
        )
    return rows
