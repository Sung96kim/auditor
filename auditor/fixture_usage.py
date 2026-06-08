"""Repo-level analysis behind ``PY-TEST-UNUSED-FIXTURE``: a pytest fixture defined (in a test
file or conftest) but never requested anywhere in the test suite. Pure logic over
``pytest-fixture-def`` / ``pytest-fixture-ref`` shape rows from the index; no db.

Fixtures cross file/role boundaries (a conftest fixture serves tests in child dirs), so the
def/ref namespace is pooled across all test + test-support files — NOT compared within-role.
``autouse`` fixtures are exempt at extraction time (they're used implicitly).
"""

from auditor.models import Category, Finding, Severity, VerdictKind

RULE_ID = "PY-TEST-UNUSED-FIXTURE"


def _is_test_role(role: str | None) -> bool:
    return role in ("test", "test_support")


def find_unused(
    def_rows: list[dict], ref_rows: list[dict], roles: dict[str, str]
) -> dict[str, list[Finding]]:
    used = {r["symbol"] for r in ref_rows if _is_test_role(roles.get(r["path"]))}
    out: dict[str, list[Finding]] = {}
    for d in def_rows:
        if not _is_test_role(roles.get(d["path"])) or d["symbol"] in used:
            continue
        out.setdefault(d["path"], []).append(_finding(d["symbol"], d["line"]))
    return out


def _finding(name: str, line: int) -> Finding:
    return Finding(
        rule_id=RULE_ID,
        category=Category.TESTING,
        severity=Severity.LOW,
        verdict_kind=VerdictKind.CANDIDATE,
        line=line,
        message=f"fixture `{name}` is never requested by any test",
        evidence=name,
        suggestion="remove the unused fixture, or request it where it's needed",
        detector="fixture-usage",
    )
