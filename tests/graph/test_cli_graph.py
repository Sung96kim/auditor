import json

from typer.testing import CliRunner

from auditor.cli import app

runner = CliRunner()


def _write_repo(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n'
        "[tool.auditor.graph]\nenabled=true\nname_similarity_threshold=0.2\n"
    )
    (tmp_path / "m.py").write_text(
        "def get_user(uid):\n    return db.fetch(uid)\n\n"
        "def fetch_user(uid):\n    return db.fetch(uid)\n"
    )


def test_cli_scan_build_related(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    _write_repo(tmp_path)
    assert runner.invoke(app, ["scan", str(tmp_path), "-i"]).exit_code == 0
    built = runner.invoke(app, ["graph", "build", str(tmp_path)])
    assert built.exit_code == 0 and json.loads(built.stdout)["nodes"] >= 2
    rel = runner.invoke(app, ["graph", "related", "get_user", str(tmp_path)])
    assert rel.exit_code == 0 and "fetch_user" in rel.stdout
