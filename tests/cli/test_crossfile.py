"""`auditor crossfile` — recompute cross-file findings from the index."""

from _support import cli_json, invoke


def test_crossfile_recomputes_from_index(sample_repo):
    src = str(sample_repo / "src")
    assert invoke("scan", src, "--incremental").exit_code == 0
    payload = cli_json(invoke("crossfile", src))
    assert "cross_file_findings" in payload


def test_crossfile_exempts_pyproject_entry_points(tmp_path):
    """Standalone `crossfile` must exempt entry-point symbols the way `scan` does."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[project.scripts]\nx = "pkg.a:_main"\n'
    )
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("def _main():\n    return 1\n")
    assert invoke("scan", str(tmp_path), "--incremental").exit_code == 0
    assert cli_json(invoke("crossfile", str(tmp_path)))["cross_file_findings"] == 0
