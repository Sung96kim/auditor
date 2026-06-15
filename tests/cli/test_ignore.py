"""`auditor ignore add|list|rm|clear` + scan honoring ignores (count, --show-ignored, gate)."""

import shutil
from pathlib import Path

import pytest
from _support import PLUGIN_FILE, cli_json, invoke

from auditor.registry import REGISTRY


@pytest.fixture
def _restore_registry():
    """`ignore add` loads the repo's plugins (mutating the global registry); restore around it."""
    detectors = dict(REGISTRY._detectors)
    categories = set(REGISTRY._plugin_categories)
    sources = dict(REGISTRY._sources)
    yield
    REGISTRY._detectors = detectors
    REGISTRY._plugin_categories = categories
    REGISTRY._sources = sources


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nextends="base"\n'
    )
    (root / "mod.py").write_text(
        "password = 'hunter2'\n"
    )  # PY-SEC-HARDCODED-SECRET (high)
    return root


def _ignored(repo: Path) -> int:
    res = cli_json(invoke("scan", str(repo), "-f", "json", "--root", str(repo)))
    return res["totals"]["ignored"]


def _rules(repo: Path, *extra) -> set[str]:
    res = cli_json(invoke("scan", str(repo), "-f", "json", "--root", str(repo), *extra))
    return {x["rule_id"] for f in res["files"] for x in f["findings"]}


def test_add_list_rm_roundtrip(repo):
    add = cli_json(
        invoke("ignore", "add", "PY-SEC-HARDCODED-SECRET", "--root", str(repo))
    )
    assert add["id"] == 1 and add["rule_id"] == "PY-SEC-HARDCODED-SECRET"

    listed = cli_json(invoke("ignore", "list", "--root", str(repo)))
    assert [r["rule_id"] for r in listed] == ["PY-SEC-HARDCODED-SECRET"]

    assert "PY-SEC-HARDCODED-SECRET" not in _rules(repo)  # hidden on scan
    assert _ignored(repo) == 1

    cli_json(invoke("ignore", "rm", "1", "--root", str(repo)))
    assert "PY-SEC-HARDCODED-SECRET" in _rules(repo)  # back after unignore


def test_show_ignored_reveals(repo):
    invoke("ignore", "add", "PY-SEC-HARDCODED-SECRET", "--root", str(repo))
    assert "PY-SEC-HARDCODED-SECRET" not in _rules(repo)
    assert "PY-SEC-HARDCODED-SECRET" in _rules(repo, "--show-ignored")


def test_line_level_add_snapshots_evidence(repo):
    add = cli_json(
        invoke(
            "ignore",
            "add",
            "PY-SEC-HARDCODED-SECRET",
            "--file",
            "mod.py",
            "--line",
            "1",
            "--root",
            str(repo),
        )
    )
    assert add["note"] is None  # finding existed → evidence captured (no fallback note)
    row = cli_json(invoke("ignore", "list", "--root", str(repo)))[0]
    assert row["evidence_hash"] is not None
    assert _ignored(repo) == 1


def test_line_level_add_without_finding_falls_back(repo):
    add = cli_json(
        invoke(
            "ignore",
            "add",
            "PY-SEC-HARDCODED-SECRET",
            "--file",
            "mod.py",
            "--line",
            "99",
            "--root",
            str(repo),
        )
    )
    assert add["note"] is not None  # no finding at line 99 → literal-line fallback note
    row = cli_json(invoke("ignore", "list", "--root", str(repo)))[0]
    assert row["evidence_hash"] is None


def test_line_requires_file(repo):
    res = invoke("ignore", "add", "PY-X", "--line", "5", "--root", str(repo))
    assert res.exit_code == 1
    assert "--line requires --file" in res.output


def test_add_unknown_rule_errors(repo):
    res = invoke("ignore", "add", "PY-NOPE-RULE", "--root", str(repo))
    assert res.exit_code == 1
    assert "unknown rule_id" in res.output
    assert (
        cli_json(invoke("ignore", "list", "--root", str(repo))) == []
    )  # nothing stored


def test_add_unknown_rule_with_force(repo):
    # --force is the last-resort escape hatch (e.g. a rule you'll define later)
    out = cli_json(
        invoke("ignore", "add", "ACME-PLUGIN-RULE", "--force", "--root", str(repo))
    )
    assert out["rule_id"] == "ACME-PLUGIN-RULE"
    assert [
        r["rule_id"] for r in cli_json(invoke("ignore", "list", "--root", str(repo)))
    ] == ["ACME-PLUGIN-RULE"]


def test_add_plugin_rule_without_force(tmp_path, _restore_registry):
    """A repo's plugin-contributed rule validates like a built-in — no --force needed,
    because `ignore add` loads the repo's config (which registers its plugins)."""
    root = tmp_path / "r"
    (root / ".auditor" / "plugins").mkdir(parents=True)
    shutil.copy(PLUGIN_FILE, root / ".auditor" / "plugins" / "house_rules.py")
    (root / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (root / ".auditor" / "config.toml").write_text(
        'extends = "base"\ntrust_local_plugins = true\n'
    )
    out = cli_json(invoke("ignore", "add", "HOUSE-NO-PRINT", "--root", str(root)))
    assert out["rule_id"] == "HOUSE-NO-PRINT"  # plugin rule accepted without --force


def test_rm_by_selector(repo):
    invoke(
        "ignore",
        "add",
        "PY-SEC-HARDCODED-SECRET",
        "--file",
        "mod.py",
        "--root",
        str(repo),
    )
    out = cli_json(
        invoke(
            "ignore",
            "rm",
            "PY-SEC-HARDCODED-SECRET",
            "--file",
            "mod.py",
            "--root",
            str(repo),
        )
    )
    assert out["removed"] is True
    assert cli_json(invoke("ignore", "list", "--root", str(repo))) == []


def test_rm_nonexistent_errors(repo):
    res = invoke("ignore", "rm", "999", "--root", str(repo))
    assert res.exit_code == 1


def test_clear(repo):
    invoke("ignore", "add", "PY-SEC-HARDCODED-SECRET", "--root", str(repo))
    invoke("ignore", "add", "PY-OOP-THIN-WRAPPER", "--root", str(repo))
    out = cli_json(invoke("ignore", "clear", "--root", str(repo)))
    assert out["cleared"] == 2
    assert cli_json(invoke("ignore", "list", "--root", str(repo))) == []


def test_ignored_finding_does_not_trip_the_gate(repo):
    # the lone high finding fails the gate...
    assert (
        invoke("scan", str(repo), "--fail-on", "high", "--root", str(repo)).exit_code
        == 1
    )
    # ...but once ignored, the gate passes (ignored never gates)
    invoke("ignore", "add", "PY-SEC-HARDCODED-SECRET", "--root", str(repo))
    assert (
        invoke("scan", str(repo), "--fail-on", "high", "--root", str(repo)).exit_code
        == 0
    )
