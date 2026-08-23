import json
from pathlib import Path

from typer.testing import CliRunner

from auditor.cli import app

runner = CliRunner()


def test_cli_scan_build_related(graph_repo: Path):
    assert runner.invoke(app, ["scan", str(graph_repo), "-i"]).exit_code == 0
    built = runner.invoke(app, ["graph", "build", str(graph_repo)])
    assert built.exit_code == 0 and json.loads(built.stdout)["nodes"] >= 2
    rel = runner.invoke(app, ["graph", "related", "get_user", str(graph_repo)])
    assert rel.exit_code == 0 and "fetch_user" in rel.stdout
