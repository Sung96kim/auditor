"""CLI smoke tests via typer's CliRunner against the sample-repo fixture."""

import json

import pytest
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
    assert all(
        any(ref.startswith("bandit:") for ref in r["standard_refs"]) for r in bandit
    )


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
    # every result carries a stable partialFingerprint so code-scanning dedupes across runs
    res0 = sarif["runs"][0]["results"][0]
    assert res0["partialFingerprints"]["auditorFingerprint/v1"]


def test_baseline_hides_preexisting_findings(sample_repo, tmp_path):
    src = str(sample_repo / "src")
    bl = tmp_path / "baseline.json"
    write = runner.invoke(app, ["scan", src, "--write-baseline", str(bl)])
    assert write.exit_code == 0, write.output
    assert bl.exists()

    # re-scan against the baseline: every prior finding is hidden
    payload = _json(
        runner.invoke(app, ["scan", src, "--baseline", str(bl), "--format", "json"])
    )
    assert payload["totals"]["blocking"] == 0
    assert sum(len(f["findings"]) for f in payload["files"]) == 0


def test_baseline_makes_ci_gate_pass(sample_repo, tmp_path):
    src = str(sample_repo / "src")
    bl = tmp_path / "baseline.json"
    runner.invoke(app, ["scan", src, "--write-baseline", str(bl)])
    # the gate trips on the pre-existing findings, but passes once they're baselined
    assert runner.invoke(app, ["scan", src, "--fail-on", "blocking"]).exit_code == 1
    passed = runner.invoke(
        app, ["scan", src, "--baseline", str(bl), "--fail-on", "blocking"]
    )
    assert passed.exit_code == 0, passed.output


def test_missing_baseline_file_errors(sample_repo, tmp_path):
    result = runner.invoke(
        app,
        ["scan", str(sample_repo / "src"), "--baseline", str(tmp_path / "nope.json")],
    )
    assert result.exit_code == 1
    assert "baseline file not found" in result.output


def test_report_md(sample_repo):
    result = runner.invoke(
        app, ["report", str(sample_repo / "src" / "web.py"), "--format", "md"]
    )
    assert result.exit_code == 0
    assert "# Audit report" in result.output


def test_manifest_rejects_non_python(tmp_path):
    f = tmp_path / "x.ts"
    f.write_text("const x = 1;\n")
    result = runner.invoke(app, ["manifest", str(f)])
    assert result.exit_code == 1
    assert "Python-only" in result.output  # clean error, not a raw traceback


def test_manifest_handles_unparseable_python(tmp_path):
    f = tmp_path / "broken.py"
    f.write_text("def f(:\n")
    result = runner.invoke(app, ["manifest", str(f)])
    assert result.exit_code == 1
    assert "could not parse" in result.output


def test_manifest_valid_python(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text("class A:\n    def m(self):\n        return 1\n")
    result = _json(runner.invoke(app, ["manifest", str(f)]))
    assert any(e["symbol"] == "A" for e in result)


def test_scan_html_short_flag(sample_repo):
    result = runner.invoke(app, ["scan", str(sample_repo / "src"), "-f", "html"])
    assert result.exit_code == 0, result.output
    assert result.output.lstrip().startswith("<!doctype html>")


def test_default_scan_prints_summary_not_json(sample_repo):
    # no -f / -o → a human summary on stdout, never a raw JSON dump
    result = runner.invoke(app, ["scan", str(sample_repo / "src"), "--no-index"])
    assert result.exit_code == 0, result.output
    assert "findings" in result.output
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.output)


@pytest.mark.parametrize("cmd", [["scan"], ["report"], ["manifest"], ["discover"]])
def test_missing_target_fails_cleanly(cmd):
    result = runner.invoke(app, [*cmd, "does/not/exist.py"])
    assert result.exit_code == 1
    assert "no such file" in result.output
    assert "Traceback" not in result.output


def test_scan_severity_filter(sample_repo):
    payload = _json(
        runner.invoke(
            app,
            [
                "scan",
                str(sample_repo / "src"),
                "--no-index",
                "-f",
                "json",
                "-s",
                "blocking",
            ],
        )
    )
    findings = [f for fl in payload["files"] for f in fl["findings"]]
    assert findings and all(f["severity"] == "blocking" for f in findings)


def test_scan_severity_invalid(sample_repo):
    result = runner.invoke(app, ["scan", str(sample_repo / "src"), "-s", "critical"])
    assert result.exit_code == 1
    assert "unknown severity" in result.output


def test_min_severity_filter(sample_repo):
    payload = _json(
        runner.invoke(
            app,
            [
                "scan",
                str(sample_repo / "src"),
                "--no-index",
                "-f",
                "json",
                "-m",
                "blocking",
            ],
        )
    )
    findings = [f for fl in payload["files"] for f in fl["findings"]]
    assert findings and all(f["severity"] == "blocking" for f in findings)


def test_fail_on_gates_exit_code(sample_repo):
    src = str(sample_repo / "src")
    assert (
        runner.invoke(
            app, ["scan", src, "--no-index", "--fail-on", "blocking"]
        ).exit_code
        == 1
    )
    clean = str(sample_repo / "src" / "clean.py")
    assert (
        runner.invoke(
            app, ["scan", clean, "--no-index", "--fail-on", "suggestion"]
        ).exit_code
        == 0
    )


def test_fail_on_ignores_candidate_findings(tmp_path):
    # a HIGH *candidate* finding (agent-judges) must not auto-fail CI — only confirmed (auto) gates
    f = tmp_path / "a.py"
    f.write_text("import time\n\n\nasync def f():\n    time.sleep(1)\n")
    payload = _json(runner.invoke(app, ["scan", str(f), "--no-index", "-f", "json"]))
    findings = [x for fl in payload["files"] for x in fl["findings"]]
    sync = next(x for x in findings if x["rule_id"] == "PY-ASYNC-SYNC-IO")
    assert sync["severity"] == "high" and sync["verdict_kind"] == "candidate"
    assert (
        runner.invoke(
            app, ["scan", str(f), "--no-index", "--fail-on", "high"]
        ).exit_code
        == 0
    )


def _git(repo, *args):
    import subprocess

    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_scan_changed_scopes_output_to_diff(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nextends="strict"\n'
    )
    (tmp_path / "a.py").write_text("def f(x):\n    eval(x)\n")
    (tmp_path / "b.py").write_text("def g(y):\n    return y\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    # change only a.py; b.py is untouched
    (tmp_path / "a.py").write_text("def f(x):\n    eval(x)\n    return x\n")

    result = runner.invoke(app, ["scan", str(tmp_path), "--changed", "-f", "json"])
    payload = json.loads(result.output)
    assert [r["file"] for r in payload["files"]] == [
        "a.py"
    ]  # output scoped to the change


def test_changed_in_subproject_scopes_correctly(tmp_path):
    # project root (own pyproject) sits *inside* a larger git repo: diff-scoping paths and result
    # paths must still align (git --relative makes both relative to the resolved root)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text(
        '[project]\nname="p"\nversion="0"\n[tool.auditor]\nextends="strict"\n'
    )
    (proj / "a.py").write_text("def f(x):\n    eval(x)\n")
    (proj / "b.py").write_text("def g(y):\n    return y\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    (proj / "a.py").write_text(
        "def f(x):\n    eval(x)\n    return x\n"
    )  # change only a.py

    payload = json.loads(
        runner.invoke(app, ["scan", str(proj), "--changed", "-f", "json"]).output
    )
    assert [r["file"] for r in payload["files"]] == ["a.py"]


def test_root_option_overrides_autodetected_root(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "pyproject.toml").write_text(
        '[project]\nname="s"\nversion="0"\n[tool.auditor]\nextends="strict"\n'
    )
    (sub / "a.py").write_text("eval(x)\n")
    # auto-detected: find_root(sub) == sub, so the path is "a.py"
    auto = _json(runner.invoke(app, ["scan", str(sub), "-f", "json"]))
    assert [r["file"] for r in auto["files"]] == ["a.py"]
    # pinned to the outer root: the path becomes "sub/a.py"
    pinned = _json(
        runner.invoke(app, ["scan", str(sub), "--root", str(tmp_path), "-f", "json"])
    )
    assert [r["file"] for r in pinned["files"]] == ["sub/a.py"]


def test_since_requires_git_repo(tmp_path):
    (tmp_path / "m.py").write_text("x = 1\n")
    result = runner.invoke(app, ["scan", str(tmp_path), "--since", "main"])
    assert result.exit_code == 1
    assert "git repository" in result.output


def test_vs_base_autodetects_non_main_branch(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "master")  # repo whose base isn't `main`
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nextends="strict"\n'
    )
    (tmp_path / "a.py").write_text("def f(x):\n    eval(x)\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    (tmp_path / "a.py").write_text("def f(x):\n    eval(x)\n    return x\n")

    result = runner.invoke(app, ["scan", str(tmp_path), "--vs-base", "-f", "json"])
    payload = json.loads(result.output)
    assert [r["file"] for r in payload["files"]] == ["a.py"]


def test_scan_exclude_glob(sample_repo):
    src = str(sample_repo / "src")
    full = _json(runner.invoke(app, ["scan", src, "--no-index", "-f", "json"]))
    excluded = _json(
        runner.invoke(
            app, ["scan", src, "--no-index", "-f", "json", "-x", "**/integrations.py"]
        )
    )
    full_files = {f["file"] for f in full["files"]}
    excl_files = {f["file"] for f in excluded["files"]}
    assert any(f.endswith("integrations.py") for f in full_files)
    assert not any(f.endswith("integrations.py") for f in excl_files)


def test_scan_output_to_file(sample_repo, tmp_path):
    out = tmp_path / "report.json"
    result = runner.invoke(app, ["scan", str(sample_repo / "src"), "-o", str(out)])
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text())
    assert payload["totals"]["blocking"] >= 1
    assert out.name in result.output  # stdout/stderr notes where it wrote


def test_manifest(sample_repo):
    payload = _json(
        runner.invoke(app, ["manifest", str(sample_repo / "src" / "models.py")])
    )
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
    payload = _json(
        runner.invoke(
            app, ["scan", str(sample_repo / "tests"), "--strict-tests", "-f", "json"]
        )
    )
    rules = {x["rule_id"] for f in payload["files"] for x in f["findings"]}
    assert "PY-SEC-HARDCODED-SECRET" in rules


def test_profile_override_enables_oop(sample_repo):
    # a config-less subdir defaults to base (oop off); --profile strict turns it on
    # remove the sample repo's pyproject [tool.auditor] to simulate a config-less repo
    (sample_repo / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\ndependencies=["pydantic"]\n'
    )
    base = _json(
        runner.invoke(
            app, ["scan", str(sample_repo / "src"), "--no-index", "-f", "json"]
        )
    )
    strict = _json(
        runner.invoke(
            app,
            [
                "scan",
                str(sample_repo / "src"),
                "--no-index",
                "-f",
                "json",
                "--profile",
                "strict",
            ],
        )
    )
    base_rules = {x["rule_id"] for f in base["files"] for x in f["findings"]}
    strict_rules = {x["rule_id"] for f in strict["files"] for x in f["findings"]}
    assert not any(
        r.startswith("PY-OOP-") and r != "PY-OOP-DATACLASS-IN-PYDANTIC"
        for r in base_rules
    )
    assert "PY-OOP-CONSTRUCTOR-WALL" in strict_rules
