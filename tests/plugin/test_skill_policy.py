import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugin"

#: the only three verdicts a judging surface may offer
VERDICTS = ("fix-recommended", "suppress-recommended", "dismiss")

#: apply-in-place wording from before the report-and-recommend policy
BANNED = (
    r"\*\*fix\*\* it",
    r"change the code",
    r"→ fix(?!-)",
    r"fix/skip(-directive)?/dismiss",
    r"false-positive-suppressed",
    r"Delete the code",
    r"remove-vs-patch",
    r"report and act",
    r"\bskip-directive\b",
)

#: shipped markdown that carries the verdict vocabulary, relative to plugin/
POLICY_DOCS = (
    "skills/judge-findings/SKILL.md",
    "skills/judge-findings/references/judging.md",
    "skills/judge-findings/references/examples.md",
    "skills/malware-scan/SKILL.md",
    "skills/malware-scan/references/triage.md",
    "skills/aggregate-report/SKILL.md",
    "skills/aggregate-report/references/reading-audit-md.md",
    "skills/audit-changes/SKILL.md",
    "skills/audit-changes/references/output-formats.md",
    "agents/auditor-reviewer.md",
    "skills/write-detector/references/patterns.md",
)

#: auditor categories the judge-findings family promotes as maintainability work
PROMOTED_CATEGORIES = (
    "oop-composition",
    "dead-code",
    "typing",
    "testing",
    "config",
    "style",
    "design-system",
    "react",
)

#: the promotion rule those categories are reported under
PROMOTION_PHRASE = "never rolled up"

#: shipped markdown that must name every promoted category and state the promotion rule
PROMOTION_DOCS = (
    "skills/judge-findings/SKILL.md",
    "skills/judge-findings/references/judging.md",
)

#: shipped markdown that must spell all three verdicts out by name
VERDICT_DOCS = (
    "skills/judge-findings/SKILL.md",
    "skills/judge-findings/references/judging.md",
    "skills/aggregate-report/SKILL.md",
    "skills/audit-changes/SKILL.md",
    "agents/auditor-reviewer.md",
)


@pytest.mark.parametrize("rel", POLICY_DOCS)
def test_policy_docs_never_instruct_a_source_edit(rel: str) -> None:
    text = (PLUGIN / rel).read_text()
    assert [p for p in BANNED if re.search(p, text)] == []


@pytest.mark.parametrize("rel", VERDICT_DOCS)
def test_policy_docs_name_all_three_verdicts(rel: str) -> None:
    text = (PLUGIN / rel).read_text()
    assert [v for v in VERDICTS if v not in text] == []


@pytest.mark.parametrize("rel", PROMOTION_DOCS)
def test_maintainability_categories_are_promoted(rel: str) -> None:
    text = (PLUGIN / rel).read_text()
    assert [c for c in PROMOTED_CATEGORIES if c not in text] == []
    assert PROMOTION_PHRASE in text
