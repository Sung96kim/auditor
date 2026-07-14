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

#: skills scoped to specific files in the target repo (carry a `paths` glob)
CODE_FACING_SKILLS = {
    "judge-findings",
    "audit-changes",
    "write-detector",
    "explore-graph",
}
#: skills that operate on the repo as a whole, not a file scope — no `paths` key
REPO_LEVEL_SKILLS = {"setup-auditor", "malware-scan", "aggregate-report"}


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


def test_code_facing_and_repo_level_partition_all_skills():
    assert CODE_FACING_SKILLS | REPO_LEVEL_SKILLS == EXPECTED_SKILLS
    assert not CODE_FACING_SKILLS & REPO_LEVEL_SKILLS


@pytest.mark.parametrize("name", sorted(EXPECTED_SKILLS))
def test_skill_has_name_and_description(name):
    fm = _frontmatter(SKILL_DIR / name / "SKILL.md")
    assert fm.get("name") == name
    assert fm.get("description")


@pytest.mark.parametrize("name", sorted(CODE_FACING_SKILLS))
def test_code_facing_skill_has_paths(name):
    fm = _frontmatter(SKILL_DIR / name / "SKILL.md")
    assert "paths" in fm


@pytest.mark.parametrize("name", sorted(REPO_LEVEL_SKILLS))
def test_repo_level_skill_has_no_paths(name):
    fm = _frontmatter(SKILL_DIR / name / "SKILL.md")
    assert "paths" not in fm


def test_judge_findings_runs_as_forked_reviewer_subagent():
    fm = _frontmatter(SKILL_DIR / "judge-findings" / "SKILL.md")
    assert fm.get("context") == "fork"
    assert fm.get("agent") == "auditor-reviewer"


def test_agent_frontmatter():
    fm = _frontmatter(AGENT_DIR / "auditor-reviewer.md")
    assert fm["name"] == "auditor-reviewer"
    assert fm.get("description")
    assert fm.get("tools") == "Read, Grep, Glob, Bash, mcp__auditor__*"
    assert fm.get("model") == "inherit"
    assert fm.get("color") == "blue"
