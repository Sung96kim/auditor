"""`auditor rules list` — enumerate detector rules, with category / standard filters."""

import json
from pathlib import Path

import pytest
from _support import cli_json, invoke, write_plugin_repo

#: (argv, source-of-HOUSE-NO-PRINT-or-None) per command that lists a repo's plugin rules
_LISTINGS = [
    pytest.param(
        ("rules", "list"),
        lambda p: next(
            (r["source"] for r in p if r["rule_id"] == "HOUSE-NO-PRINT"), None
        ),
        id="rules list",
    ),
    pytest.param(
        ("plugins", "list"),
        lambda p: p["detectors"].get("HOUSE-NO-PRINT", {}).get("source"),
        id="plugins list",
    ),
]


@pytest.fixture
def plugin_repo(tmp_path, restore_registry) -> Path:
    """A repo whose trusted `.auditor/plugins/` contributes the HOUSE-NO-PRINT rule."""
    return write_plugin_repo(tmp_path)


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


@pytest.mark.parametrize(("argv", "source_of_house"), _LISTINGS)
def test_plugin_rule_source_names_the_plugin_file(plugin_repo, argv, source_of_house):
    payload = cli_json(invoke(*argv, "--root", str(plugin_repo)))
    assert source_of_house(payload).endswith("house_rules.py")


@pytest.mark.parametrize(("argv", "source_of_house"), _LISTINGS)
def test_listing_from_a_subdirectory_finds_the_repo_root(
    plugin_repo, monkeypatch, argv, source_of_house
):
    """With no --root, the walk up from the working directory still finds the repo's plugins."""
    deep = plugin_repo / "sub" / "deep"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)

    assert source_of_house(cli_json(invoke(*argv))) is not None


@pytest.mark.parametrize(
    ("trusted", "listed"), [(True, True), (False, False)], ids=["trusted", "untrusted"]
)
def test_rules_list_explains_an_omitted_plugin_rule(
    tmp_path, restore_registry, trusted, listed
):
    """A plugin rule is either in the catalogue or explained on stderr, never silently absent."""
    repo = write_plugin_repo(tmp_path, trusted=trusted)
    result = invoke("rules", "list", "--root", str(repo), "--json")

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert any(r["rule_id"] == "HOUSE-NO-PRINT" for r in rows) is listed
    assert ("ignored" in result.stderr) is not listed


def test_rules_list_reports_a_broken_plugin_on_stderr(tmp_path, restore_registry):
    """A plugin that raises on import warns on stderr; stdout stays a clean JSON array."""
    repo = write_plugin_repo(tmp_path)
    (repo / ".auditor" / "plugins" / "zz_broken.py").write_text(
        'raise RuntimeError("boom on import")\n'
    )
    result = invoke("rules", "list", "--root", str(repo), "--json")

    assert result.exit_code == 0, result.output
    assert {r["rule_id"] for r in json.loads(result.stdout)} >= {"HOUSE-NO-PRINT"}
    assert "failed to load local plugin" in result.stderr
    assert "zz_broken.py" in result.stderr


def test_rules_list_invalid_config_fails_cleanly(tmp_path):
    """An invalid repo config exits 1 with one clean line that does not recommend this command."""
    (tmp_path / ".auditor").mkdir()
    (tmp_path / ".auditor" / "config.toml").write_text(
        'extends = "base"\n[rules]\nNO-SUCH-RULE = { enabled = false }\n'
    )
    result = invoke("rules", "list", "--root", str(tmp_path))

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Traceback" not in result.output
    assert "invalid config" in result.output and "NO-SUCH-RULE" in result.output
    assert "rules list" not in result.output
