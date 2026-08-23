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
)

#: shipped markdown that must spell all three verdicts out by name
VERDICT_DOCS = (
    "skills/judge-findings/SKILL.md",
    "skills/judge-findings/references/judging.md",
)


@pytest.mark.parametrize("rel", POLICY_DOCS)
def test_policy_docs_never_instruct_a_source_edit(rel: str) -> None:
    text = (PLUGIN / rel).read_text()
    assert [p for p in BANNED if re.search(p, text)] == []


@pytest.mark.parametrize("rel", VERDICT_DOCS)
def test_policy_docs_name_all_three_verdicts(rel: str) -> None:
    text = (PLUGIN / rel).read_text()
    assert [v for v in VERDICTS if v not in text] == []
