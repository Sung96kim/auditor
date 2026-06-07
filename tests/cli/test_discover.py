"""`auditor discover` — auditable files with their classified role."""

from _support import cli_json, invoke


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
    assert files == {"src/a.py"}  # legacy/ excluded, matching what scan audits
