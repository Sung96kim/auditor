"""`auditor crossfile` — recompute cross-file findings from the index."""

from pathlib import Path

import pytest
from _support import cli_json, invoke


def _dead_symbol_repo(root: Path, *, skip: bool) -> Path:
    """A repo with exactly one dead module-level symbol, optionally judged with a directive."""
    (root / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (root / "pkg").mkdir()
    directive = "  # auditor: skip: PY-DEAD-SYMBOL (kept on purpose)" if skip else ""
    (root / "pkg" / "a.py").write_text(
        f"def _unused_helper():{directive}\n    return 1\n"
    )
    return root


def _dead_symbols(payload: dict) -> int:
    return sum(
        1
        for f in payload["files"]
        for finding in f["findings"]
        if finding["rule_id"] == "PY-DEAD-SYMBOL"
    )


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


def test_crossfile_counts_the_dead_symbol_scan_reports(tmp_path):
    """With nothing suppressed, both commands see the same one finding."""
    repo = _dead_symbol_repo(tmp_path, skip=False)
    assert invoke("scan", str(repo), "--incremental").exit_code == 0

    scanned = _dead_symbols(cli_json(invoke("scan", str(repo), "-i", "-f", "json")))
    assert scanned == 1
    assert cli_json(invoke("crossfile", str(repo)))["cross_file_findings"] == scanned


@pytest.mark.parametrize("suppress", ["skip_directive", "persistent_ignore"])
def test_crossfile_count_matches_scan_after_suppression(tmp_path, suppress):
    """`crossfile` counts what `scan` reports, not what `scan` suppresses."""
    repo = _dead_symbol_repo(tmp_path, skip=suppress == "skip_directive")
    assert invoke("scan", str(repo), "--incremental").exit_code == 0
    if suppress == "persistent_ignore":
        added = invoke("ignore", "add", "PY-DEAD-SYMBOL", "--root", str(repo))
        assert added.exit_code == 0, added.output

    scanned = _dead_symbols(cli_json(invoke("scan", str(repo), "-i", "-f", "json")))
    assert scanned == 0
    assert cli_json(invoke("crossfile", str(repo)))["cross_file_findings"] == scanned
