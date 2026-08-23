"""`auditor rules list` — enumerate detector rules, with category / standard filters."""

import shutil

import pytest
from _support import PLUGIN_FILE, cli_json, invoke


@pytest.fixture
def plugin_repo(tmp_path, restore_registry):
    """A repo whose trusted `.auditor/plugins/` contributes the HOUSE-NO-PRINT rule."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    plugins = tmp_path / ".auditor" / "plugins"
    plugins.mkdir(parents=True)
    shutil.copy(PLUGIN_FILE, plugins / "house_rules.py")
    (tmp_path / ".auditor" / "config.toml").write_text(
        'extends = "base"\ntrust_local_plugins = true\n'
    )
    return tmp_path


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


def test_rules_list_framework_sqlalchemy():
    rows = cli_json(invoke("rules", "list", "--framework", "sqlalchemy"))
    ids = {r["rule_id"] for r in rows}
    assert {"SA-MUTABLE-DEFAULT", "SA-RAW-SQL", "SA-GREENLET-ATTR-AFTER-COMMIT"} <= ids
    assert all(r["framework"] == "sqlalchemy" for r in rows)


def test_rules_list_unknown_framework_errors():
    result = invoke("rules", "list", "--framework", "nope")
    assert result.exit_code == 1
    assert "unknown framework" in result.output


def test_rules_list_includes_repo_plugin_rules(plugin_repo):
    rows = cli_json(invoke("rules", "list", "--root", str(plugin_repo)))
    house = [r for r in rows if r["rule_id"] == "HOUSE-NO-PRINT"]
    assert house and house[0]["category"] == "house"


@pytest.mark.parametrize("command", [("rules", "list"), ("plugins", "list")])
def test_plugin_rule_source_names_the_plugin_file(plugin_repo, command):
    payload = cli_json(invoke(*command, "--root", str(plugin_repo)))
    source = (
        payload["detectors"]["HOUSE-NO-PRINT"]["source"]
        if isinstance(payload, dict)
        else next(r["source"] for r in payload if r["rule_id"] == "HOUSE-NO-PRINT")
    )
    assert source.endswith("house_rules.py")
