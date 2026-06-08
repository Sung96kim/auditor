"""`auditor rules list` — enumerate detector rules, with category / standard filters."""

from _support import cli_json, invoke


def test_rules_list():
    payload = cli_json(invoke("rules", "list"))
    ids = {r["rule_id"] for r in payload}
    assert "PY-SEC-DANGEROUS-EVAL" in ids
    assert "PY-XFILE-DUP-MODEL" in ids


def test_rules_list_filtered_by_category_and_standard():
    by_cat = cli_json(invoke("rules", "list", "--category", "security"))
    assert by_cat and all(r["category"] == "security" for r in by_cat)
    by_std = cli_json(invoke("rules", "list", "--standard", "bandit"))
    assert all(
        any(ref.startswith("bandit:") for ref in r["standard_refs"]) for r in by_std
    )


def test_unknown_category_errors():
    res = invoke("rules", "list", "--category", "nonsense")
    assert res.exit_code == 1
    assert "unknown category" in res.output


def test_unknown_standard_errors():
    res = invoke("rules", "list", "--standard", "nope")
    assert res.exit_code == 1
    assert "unknown standard" in res.output


def test_rules_list_framework_filter():
    rows = cli_json(invoke("rules", "list", "--framework", "pytest"))
    assert rows and all(r["framework"] == "pytest" for r in rows)
    assert "PY-TEST-NO-ASSERTION" in {r["rule_id"] for r in rows}


def test_rules_list_unknown_framework_errors():
    result = invoke("rules", "list", "--framework", "nope")
    assert result.exit_code == 1
    assert "unknown framework" in result.output
