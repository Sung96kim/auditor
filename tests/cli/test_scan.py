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
    # auto-detected: find_root(sub) == sub, so the path is "a.py" (+ pyproject.toml, scanned clean)
    auto = cli_json(invoke("scan", str(sub), "-f", "json"))
    assert [r["file"] for r in auto["files"]] == ["a.py", "pyproject.toml"]
    # pinned to the outer root: the path becomes "sub/a.py"
    pinned = cli_json(invoke("scan", str(sub), "--root", str(tmp_path), "-f", "json"))
    assert [r["file"] for r in pinned["files"]] == ["sub/a.py", "sub/pyproject.toml"]


# --- scope / profile flags ---------------------------------------------------------------


def test_scan_exclude_glob(sample_repo):
    src = str(sample_repo / "src")
    full = cli_json(invoke("scan", src, "--no-index", "-f", "json"))
    excluded = cli_json(
        invoke("scan", src, "--no-index", "-f", "json", "-x", "**/integrations.py")
    )
    assert any(f["file"].endswith("integrations.py") for f in full["files"])
    assert not any(f["file"].endswith("integrations.py") for f in excluded["files"])


def test_scan_rule_filter(sample_repo):
    src = str(sample_repo / "src")
    full = cli_json(invoke("scan", src, "--no-index", "-f", "json"))
    all_rules = {x["rule_id"] for f in full["files"] for x in f["findings"]}
    target = next(r for r in all_rules if r == "PY-SEC-DANGEROUS-EVAL")
    filtered = cli_json(
        invoke("scan", src, "--no-index", "-f", "json", "--rule", target)
    )
    kept = {x["rule_id"] for f in filtered["files"] for x in f["findings"]}
    assert kept == {target}  # only the requested rule survives


def test_scan_unknown_rule_suggests(sample_repo):
    # a near-miss rule id gets a "Did you mean …?" hint
    res = invoke("scan", str(sample_repo / "src"), "--rule", "PY-SEC-DANGEROUS-EVL")
    assert res.exit_code == 1
    out = " ".join(res.output.split())  # collapse rich's line-wrapping
    assert "unknown rule" in out
    assert "Did you mean 'PY-SEC-DANGEROUS-EVAL'?" in out


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
    base_oop = {
        (x["rule_id"], x["severity"])
        for f in base["files"]
        for x in f["findings"]
        if x["rule_id"].startswith("PY-OOP-")
    }
    strict_rules = {x["rule_id"] for f in strict["files"] for x in f["findings"]}
    # at the base floor the opinionated oop rules stay off — only the auto
    # dataclass rule and the always-on suggestion-tier nudges may surface
    assert not any(
        rule != "PY-OOP-DATACLASS-IN-PYDANTIC" and severity != "suggestion"
        for rule, severity in base_oop
    ), base_oop
    assert "PY-OOP-CONSTRUCTOR-WALL" in strict_rules


# --- gitignore + migration soft-skip -----------------------------------------------------


def _gitignore_repo(root, *, respect: bool | None = None):
    """A git repo: tracked a.py (eval) + a git-ignored ignored.py (eval)."""
    git(root, "init", "-q")
    toml = '[project]\nname="x"\nversion="0"\n[tool.auditor]\nextends="strict"\n'
    if respect is not None:
        toml += f"respect_gitignore={'true' if respect else 'false'}\n"
    (root / "pyproject.toml").write_text(toml)
    (root / "a.py").write_text("def f(x):\n    eval(x)\n")
    (root / "ignored.py").write_text("def g(x):\n    eval(x)\n")
    (root / ".gitignore").write_text("ignored.py\n")
    return root


def test_scan_skips_gitignored_by_default(tmp_path):
    _gitignore_repo(tmp_path)
    payload = cli_json(invoke("scan", str(tmp_path), "--no-index", "-f", "json"))
    # ignored.py skipped; pyproject.toml is scanned (clean) for config secrets
    assert [r["file"] for r in payload["files"]] == ["a.py", "pyproject.toml"]


def test_include_gitignored_flag(tmp_path):
    _gitignore_repo(tmp_path)
    payload = cli_json(
        invoke(
            "scan", str(tmp_path), "--no-index", "--include-gitignored", "-f", "json"
        )
    )
    assert {r["file"] for r in payload["files"]} == {"a.py", "ignored.py", "pyproject.toml"}


def test_respect_gitignore_config_toggle(tmp_path):
    _gitignore_repo(tmp_path, respect=False)  # [tool.auditor] respect_gitignore=false
    payload = cli_json(invoke("scan", str(tmp_path), "--no-index", "-f", "json"))
    assert {r["file"] for r in payload["files"]} == {"a.py", "ignored.py", "pyproject.toml"}


def test_scan_soft_skips_migrations_until_targeted(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nextends="strict"\n'
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "real.py").write_text("def f(x):\n    eval(x)\n")
    mig = tmp_path / "app" / "migrations"
    mig.mkdir()
    (mig / "0001_init.py").write_text("def f(x):\n    eval(x)\n")

    whole = cli_json(invoke("scan", str(tmp_path), "--no-index", "-f", "json"))
    # migrations dropped; pyproject.toml scanned (clean) for config secrets
    assert [r["file"] for r in whole["files"]] == ["app/real.py", "pyproject.toml"]

    targeted = cli_json(invoke("scan", str(mig), "--no-index", "-f", "json"))
    assert [r["file"] for r in targeted["files"]] == ["app/migrations/0001_init.py"]


# --- --config-json -----------------------------------------------------------------------


def test_scan_config_json_changes_severity(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "a.py").write_text("def f(x):\n    eval(x)\n")
    payload = cli_json(
        invoke(
            "scan",
            str(tmp_path),
            "--no-index",
            "-f",
            "json",
            "--config-json",
            '{"rules":{"PY-SEC-DANGEROUS-EVAL":{"severity":"low"}}}',
        )
    )
    sev = next(
        x["severity"]
        for fl in payload["files"]
        for x in fl["findings"]
        if x["rule_id"] == "PY-SEC-DANGEROUS-EVAL"
    )
    assert sev == "low"


def test_scan_config_json_bad_json_errors(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    res = invoke("scan", str(tmp_path), "--config-json", "{nope")
    assert res.exit_code == 1
    assert "invalid --config-json" in res.output


def test_scan_config_json_unknown_key_errors(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    res = invoke("scan", str(tmp_path), "--config-json", '{"nope": 1}')
    assert res.exit_code == 1
    assert "invalid config" in " ".join(res.output.split())


def test_scan_config_json_activates_greenlet_rule(tmp_path):
    # the dogfood as a regression test
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "m.py").write_text(
        "import sqlalchemy\n"
        "from sqlalchemy.ext.asyncio import AsyncSession\n"
        "async def f(session):\n"
        "    user = User()\n    session.add(user)\n"
        "    await session.commit()\n    return user.email\n"
    )
    off = cli_json(invoke("scan", str(tmp_path), "--no-index", "-f", "json"))
    on = cli_json(
        invoke(
            "scan",
            str(tmp_path),
            "--no-index",
            "-f",
            "json",
            "--config-json",
            '{"sqlalchemy":{"expire_on_commit":true}}',
        )
    )
    rid = "SA-GREENLET-ATTR-AFTER-COMMIT"
    assert rid not in {x["rule_id"] for fl in off["files"] for x in fl["findings"]}
    assert rid in {x["rule_id"] for fl in on["files"] for x in fl["findings"]}


# --- new gap-fill tests -------------------------------------------------------------------


def test_vs_base_no_recognizable_branch(tmp_path):
    """--vs-base on a repo with no main/master/develop and no diff_base config → exit 1."""
    git(tmp_path, "init", "-b", "feature")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "a.py").write_text("x = 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    result = invoke("scan", str(tmp_path), "--vs-base")
    assert result.exit_code == 1
    assert "no base branch found" in result.output


def test_since_unresolvable_ref(tmp_path):
    """--since <bogus-ref> in a real git repo → exit 1, 'could not be resolved' in output."""
    _diff_repo(tmp_path)
    result = invoke("scan", str(tmp_path), "--since", "no-such-ref-xyz")
    assert result.exit_code == 1
    assert "could not be resolved" in result.output


def test_fail_on_invalid_severity(sample_repo):
    """--fail-on with an unrecognised severity name → exit 1, 'unknown severity' in output."""
    result = invoke("scan", str(sample_repo / "src"), "--fail-on", "critical")
    assert result.exit_code == 1
    assert "unknown severity" in result.output


def test_scan_config_json_non_object_errors(tmp_path):
    """--config-json '[1,2]' (a JSON array, not an object) → exit 1, 'must be a JSON object'."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    result = invoke("scan", str(tmp_path), "--config-json", "[1, 2]")
    assert result.exit_code == 1
    assert "must be a JSON object" in result.output


def test_scan_verbose_smoke(tmp_path):
    """-v verbose flag: scan with --no-index -v exits cleanly (smoke test)."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "a.py").write_text("x = 1\n")
    result = invoke("scan", str(tmp_path), "--no-index", "-v")
    assert result.exit_code == 0


def test_scan_warns_when_resolve_packages_set_but_no_env(tmp_path):
    """resolve_packages misconfiguration warning surfaces without -v (verbosity 0 = WARNING)."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nresolve_packages = ["atmo"]\n'
    )
    (tmp_path / "m.py").write_text("x = 1\n")
    result = invoke("scan", str(tmp_path), "--no-index")  # no -v
    assert result.exit_code == 0, result.output
    assert "resolve_packages" in result.stderr  # warning surfaced on stderr without -v
