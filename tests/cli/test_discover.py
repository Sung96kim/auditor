"""`auditor discover` — auditable files with their classified role."""

from _support import cli_json, git, invoke


def test_discover_reports_roles(sample_repo):
    payload = cli_json(invoke("discover", str(sample_repo)))
    roles = {f["role"] for f in payload}
    assert {"production", "test", "test_support", "script"} <= roles


def test_discover_honors_config_exclude(tmp_path):
    """discover lists only auditable files — so [tool.auditor] exclude must drop matches too
    (regression: discover used to ignore the config exclude that scan applies)."""
    root = tmp_path / "r"
    (root / "src").mkdir(parents=True)
    (root / "legacy").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nextends="base"\n'
        'exclude = ["legacy/**"]\n'
    )
    (root / "src" / "a.py").write_text("x = 1\n")
    (root / "legacy" / "old.py").write_text("x = 1\n")

    files = {f["file"] for f in cli_json(invoke("discover", str(root)))}
    # legacy/ excluded, matching what scan audits; pyproject.toml scanned for config secrets
    assert files == {"src/a.py", "pyproject.toml"}


def test_discover_skips_gitignored_and_migrations(tmp_path):
    """discover applies the same defaults as scan: git-ignored files and migration dirs are
    dropped on a whole-repo listing."""
    git(tmp_path, "init", "-q")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / "secret.py").write_text("y = 2\n")
    (tmp_path / ".gitignore").write_text("secret.py\n")
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0001.py").write_text("z = 3\n")

    files = {f["file"] for f in cli_json(invoke("discover", str(tmp_path)))}
    # secret.py (gitignored) + migrations/ both dropped; pyproject.toml scanned for config secrets
    assert files == {"src/a.py", "pyproject.toml"}


def test_discover_accepts_config_json(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "a.py").write_text("x = 1\n")
    res = invoke("discover", str(tmp_path), "--config-json", '{"exclude":["a.py"]}')
    assert res.exit_code == 0, res.output
    assert "a.py" not in res.output  # override-excluded


def test_discover_config_json_unknown_key_errors(tmp_path):
    """discover --config-json with an unknown key → exit 1, 'invalid config' in output."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    result = invoke("discover", str(tmp_path), "--config-json", '{"nope": 1}')
    assert result.exit_code == 1
    assert "invalid config" in " ".join(result.output.split())
