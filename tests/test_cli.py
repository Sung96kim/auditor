"""CLI smoke tests via typer's CliRunner against the sample-repo fixture."""

import json

from typer.testing import CliRunner

from auditor.cli import app

runner = CliRunner()


def _json(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_rules_list():
    payload = _json(runner.invoke(app, ["rules", "list"]))
    ids = {r["rule_id"] for r in payload}
    assert "PY-SEC-DANGEROUS-EVAL" in ids
    assert "PY-XFILE-DUP-MODEL" in ids


def test_rules_list_filtered():
    payload = _json(runner.invoke(app, ["rules", "list", "--category", "security"]))
    assert payload and all(r["category"] == "security" for r in payload)
    bandit = _json(runner.invoke(app, ["rules", "list", "--standard", "bandit"]))
    assert all(any(ref.startswith("bandit:") for ref in r["standard_refs"]) for r in bandit)


def test_scan_json(sample_repo):
    result = runner.invoke(app, ["scan", str(sample_repo / "src"), "--format", "json"])
    payload = _json(result)
    assert payload["totals"]["blocking"] >= 1
    files = {f["file"]: f for f in payload["files"]}
    integ = next(f for k, f in files.items() if k.endswith("integrations.py"))
    rules = {x["rule_id"] for x in integ["findings"]}
    assert "PY-SEC-DANGEROUS-EVAL" in rules


def test_scan_sarif(sample_repo):
    result = runner.invoke(app, ["scan", str(sample_repo / "src"), "--format", "sarif"])
    assert result.exit_code == 0
    sarif = json.loads(result.output)
    assert sarif["version"] == "2.1.0"


def test_report_md(sample_repo):
    result = runner.invoke(app, ["report", str(sample_repo / "src" / "web.py"), "--format", "md"])
    assert result.exit_code == 0
    assert "# Audit report" in result.output


def test_manifest(sample_repo):
    payload = _json(runner.invoke(app, ["manifest", str(sample_repo / "src" / "models.py")]))
    symbols = {e["symbol"] for e in payload}
    assert "OpportunityRecord" in symbols


def test_discover(sample_repo):
    payload = _json(runner.invoke(app, ["discover", str(sample_repo)]))
    roles = {f["role"] for f in payload}
    assert {"production", "test", "test_support", "script"} <= roles


def test_incremental_and_aggregate(sample_repo, tmp_path):
    src = str(sample_repo / "src")
    assert runner.invoke(app, ["scan", src, "--incremental"]).exit_code == 0
    out = tmp_path / "AUDIT.md"
    res = runner.invoke(app, ["aggregate", src, "-o", str(out)])
    assert res.exit_code == 0
    assert out.exists() and "consolidated report" in out.read_text()


def test_config_show(sample_repo):
    payload = _json(runner.invoke(app, ["config", "show", "--root", str(sample_repo)]))
    assert payload["extends"] == "strict"


def test_plugins_list(sample_repo):
    payload = _json(runner.invoke(app, ["plugins", "list", "--root", str(sample_repo)]))
    assert "json" in payload["reporters"]
    assert "PY-SEC-DANGEROUS-EVAL" in payload["detectors"]


def test_index_add_and_list(sample_repo):
    files = [str(p) for p in (sample_repo / "src").glob("*.py")][:2]
    r = runner.invoke(app, ["index", "add", *files, "--root", str(sample_repo)])
    assert r.exit_code == 0, r.output
    listed = _json(runner.invoke(app, ["index", "list", "--root", str(sample_repo)]))
    assert isinstance(listed, list)


def test_crossfile_command(sample_repo):
    src = str(sample_repo / "src")
    assert runner.invoke(app, ["scan", src, "--incremental"]).exit_code == 0
    payload = _json(runner.invoke(app, ["crossfile", src]))
    assert "cross_file_findings" in payload


def test_scan_strict_tests(sample_repo):
    payload = _json(runner.invoke(app, ["scan", str(sample_repo / "tests"), "--strict-tests"]))
    rules = {x["rule_id"] for f in payload["files"] for x in f["findings"]}
    assert "PY-SEC-HARDCODED-SECRET" in rules
