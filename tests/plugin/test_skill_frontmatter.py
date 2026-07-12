from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "plugin" / "skills"
AGENT_DIR = ROOT / "plugin" / "agents"

EXPECTED_SKILLS = {
    "judge-findings",
    "audit-changes",
    "write-detector",
    "setup-auditor",
    "explore-graph",
    "malware-scan",
    "aggregate-report",
}


def _frontmatter(md: Path) -> dict:
    text = md.read_text()
    assert text.startswith("---\n"), f"{md} missing frontmatter"
    block = text.split("---\n", 2)[1]
    out = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


def test_all_skills_present():
    assert {p.name for p in SKILL_DIR.iterdir() if p.is_dir()} == EXPECTED_SKILLS


@pytest.mark.parametrize("name", sorted(EXPECTED_SKILLS))
def test_skill_has_name_and_description(name):
    fm = _frontmatter(SKILL_DIR / name / "SKILL.md")
    assert fm.get("name") == name
    assert fm.get("description")


def test_agent_frontmatter():
    fm = _frontmatter(AGENT_DIR / "auditor-reviewer.md")
    assert fm["name"] == "auditor-reviewer"
    assert fm.get("description")
