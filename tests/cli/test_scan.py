"""`auditor scan` — output formats, severity filtering, CI gating, baseline, diff-scoping,
and the scan-level flags (--exclude / --strict-tests / --profile / -o / --root)."""

import json

import pytest
from _support import cli_json, git, invoke

# --- output formats ----------------------------------------------------------------------


def test_scan_json(sample_repo):
    payload = cli_json(invoke("scan", str(sample_repo / "src"), "--format", "json"))
    assert payload["totals"]["blocking"] >= 1
    files = {f["file"]: f for f in payload["files"]}
    integ = next(f for k, f in files.items() if k.endswith("integrations.py"))
    assert "PY-SEC-DANGEROUS-EVAL" in {x["rule_id"] for x in integ["findings"]}


def test_scan_sarif(sample_repo):
    sarif = cli_json(invoke("scan", str(sample_repo / "src"), "--format", "sarif"))
    assert sarif["version"] == "2.1.0"
    # every result carries a stable partialFingerprint so code-scanning dedupes across runs
    res0 = sarif["runs"][0]["results"][0]
    assert res0["partialFingerprints"]["auditorFingerprint/v1"]


def test_scan_html_short_flag(sample_repo):
    result = invoke("scan", str(sample_repo / "src"), "-f", "html")
    assert result.exit_code == 0, result.output
    assert result.output.lstrip().startswith("<!doctype html>")


def test_default_scan_prints_summary_not_json(sample_repo):
    # no -f / -o → a human summary on stdout, never a raw JSON dump
    result = invoke("scan", str(sample_repo / "src"), "--no-index")
    assert result.exit_code == 0, result.output
    assert "findings" in result.output
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.output)


def test_scan_output_to_file(sample_repo, tmp_path):
    out = tmp_path / "report.json"
    result = invoke("scan", str(sample_repo / "src"), "-o", str(out))
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text())
    assert payload["totals"]["blocking"] >= 1
    assert out.name in result.output  # stdout/stderr notes where it wrote


# --- severity filtering ------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["-s", "-m"])
def test_scan_severity_filters_to_blocking(sample_repo, flag):
    payload = cli_json(
        invoke(
            "scan",
            str(sample_repo / "src"),
            "--no-index",
            "-f",
            "json",
            flag,
            "blocking",
        )
    )
    findings = [f for fl in payload["files"] for f in fl["findings"]]
    assert findings and all(f["severity"] == "blocking" for f in findings)


def test_scan_severity_invalid(sample_repo):
    result = invoke("scan", str(sample_repo / "src"), "-s", "critical")
    assert result.exit_code == 1
    assert "unknown severity" in result.output


# --- CI gate (--fail-on) -----------------------------------------------------------------


def test_fail_on_gates_exit_code(sample_repo):
    src = str(sample_repo / "src")
    assert invoke("scan", src, "--no-index", "--fail-on", "blocking").exit_code == 1
    clean = str(sample_repo / "src" / "clean.py")
    assert invoke("scan", clean, "--no-index", "--fail-on", "suggestion").exit_code == 0


def test_fail_on_ignores_candidate_findings(tmp_path):
    # a HIGH *candidate* finding (agent-judges) must not auto-fail CI — only confirmed (auto) gates
    f = tmp_path / "a.py"
    f.write_text("import time\n\n\nasync def f():\n    time.sleep(1)\n")
    payload = cli_json(invoke("scan", str(f), "--no-index", "-f", "json"))
    findings = [x for fl in payload["files"] for x in fl["findings"]]
    sync = next(x for x in findings if x["rule_id"] == "PY-ASYNC-SYNC-IO")
    assert sync["severity"] == "high" and sync["verdict_kind"] == "candidate"
    assert invoke("scan", str(f), "--no-index", "--fail-on", "high").exit_code == 0


# --- baseline ----------------------------------------------------------------------------


def test_baseline_hides_preexisting_findings(sample_repo, tmp_path):
    src = str(sample_repo / "src")
    bl = tmp_path / "baseline.json"
    write = invoke("scan", src, "--write-baseline", str(bl))
    assert write.exit_code == 0, write.output
    assert bl.exists()

    # re-scan against the baseline: every prior finding is hidden
    payload = cli_json(invoke("scan", src, "--baseline", str(bl), "--format", "json"))
    assert payload["totals"]["blocking"] == 0
    assert sum(len(f["findings"]) for f in payload["files"]) == 0


def test_baseline_makes_ci_gate_pass(sample_repo, tmp_path):
    src = str(sample_repo / "src")
    bl = tmp_path / "baseline.json"
    invoke("scan", src, "--write-baseline", str(bl))
    # the gate trips on the pre-existing findings, but passes once they're baselined
    assert invoke("scan", src, "--fail-on", "blocking").exit_code == 1
    passed = invoke("scan", src, "--baseline", str(bl), "--fail-on", "blocking")
    assert passed.exit_code == 0, passed.output


def test_missing_baseline_file_errors(sample_repo, tmp_path):
    result = invoke(
        "scan", str(sample_repo / "src"), "--baseline", str(tmp_path / "nope.json")
    )
    assert result.exit_code == 1
    assert "baseline file not found" in result.output


# --- diff-scoping (--changed / --since / --vs-base / --root) ------------------------------


def _diff_repo(root, *, branch: str = "main", proj=None):
    """A committed git repo with a.py (eval) + b.py (clean); returns the project dir."""
    git(root, "init", "-q", "-b", branch)
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    proj = proj or root
    proj.mkdir(exist_ok=True)
    (proj / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nextends="strict"\n'
    )
    (proj / "a.py").write_text("def f(x):\n    eval(x)\n")
    (proj / "b.py").write_text("def g(y):\n    return y\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    (proj / "a.py").write_text(
        "def f(x):\n    eval(x)\n    return x\n"
    )  # change only a.py
    return proj


def test_scan_changed_scopes_output_to_diff(tmp_path):
    _diff_repo(tmp_path)
    payload = cli_json(invoke("scan", str(tmp_path), "--changed", "-f", "json"))
    assert [r["file"] for r in payload["files"]] == [
        "a.py"
    ]  # output scoped to the change


def test_changed_in_subproject_scopes_correctly(tmp_path):
    # project root (own pyproject) sits *inside* a larger git repo: diff-scoping paths and result
    # paths must still align (git --relative makes both relative to the resolved root)
    proj = _diff_repo(tmp_path, proj=tmp_path / "proj")
    payload = cli_json(invoke("scan", str(proj), "--changed", "-f", "json"))
    assert [r["file"] for r in payload["files"]] == ["a.py"]


def test_vs_base_autodetects_non_main_branch(tmp_path):
    _diff_repo(tmp_path, branch="master")  # repo whose base isn't `main`
    payload = cli_json(invoke("scan", str(tmp_path), "--vs-base", "-f", "json"))
    assert [r["file"] for r in payload["files"]] == ["a.py"]


def test_since_requires_git_repo(tmp_path):
    (tmp_path / "m.py").write_text("x = 1\n")
    result = invoke("scan", str(tmp_path), "--since", "main")
    assert result.exit_code == 1
    assert "git repository" in result.output


def test_root_option_overrides_autodetected_root(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "pyproject.toml").write_text(
        '[project]\nname="s"\nversion="0"\n[tool.auditor]\nextends="strict"\n'
    )
    (sub / "a.py").write_text("eval(x)\n")
    # auto-detected: find_root(sub) == sub, so the path is "a.py"
    auto = cli_json(invoke("scan", str(sub), "-f", "json"))
    assert [r["file"] for r in auto["files"]] == ["a.py"]
    # pinned to the outer root: the path becomes "sub/a.py"
    pinned = cli_json(invoke("scan", str(sub), "--root", str(tmp_path), "-f", "json"))
    assert [r["file"] for r in pinned["files"]] == ["sub/a.py"]


# --- scope / profile flags ---------------------------------------------------------------


def test_scan_exclude_glob(sample_repo):
    src = str(sample_repo / "src")
    full = cli_json(invoke("scan", src, "--no-index", "-f", "json"))
    excluded = cli_json(
        invoke("scan", src, "--no-index", "-f", "json", "-x", "**/integrations.py")
    )
    assert any(f["file"].endswith("integrations.py") for f in full["files"])
    assert not any(f["file"].endswith("integrations.py") for f in excluded["files"])


def test_scan_strict_tests(sample_repo):
    payload = cli_json(
        invoke("scan", str(sample_repo / "tests"), "--strict-tests", "-f", "json")
    )
    rules = {x["rule_id"] for f in payload["files"] for x in f["findings"]}
    assert "PY-SEC-HARDCODED-SECRET" in rules


def test_profile_override_enables_oop(sample_repo):
    # a config-less subdir defaults to base (oop off); --profile strict turns it on.
    # remove the sample repo's [tool.auditor] to simulate a config-less repo
    (sample_repo / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\ndependencies=["pydantic"]\n'
    )
    src = str(sample_repo / "src")
    base = cli_json(invoke("scan", src, "--no-index", "-f", "json"))
    strict = cli_json(
        invoke("scan", src, "--no-index", "-f", "json", "--profile", "strict")
    )
    base_rules = {x["rule_id"] for f in base["files"] for x in f["findings"]}
    strict_rules = {x["rule_id"] for f in strict["files"] for x in f["findings"]}
    assert not any(
        r.startswith("PY-OOP-") and r != "PY-OOP-DATACLASS-IN-PYDANTIC"
        for r in base_rules
    )
    assert "PY-OOP-CONSTRUCTOR-WALL" in strict_rules
