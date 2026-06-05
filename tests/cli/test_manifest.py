"""`auditor manifest` — the AST manifest for one Python file, with clean errors on bad input."""

from _support import cli_json, invoke


def test_manifest_valid_python(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text("class A:\n    def m(self):\n        return 1\n")
    result = cli_json(invoke("manifest", str(f)))
    assert any(e["symbol"] == "A" for e in result)


def test_manifest_on_sample_repo(sample_repo):
    payload = cli_json(invoke("manifest", str(sample_repo / "src" / "models.py")))
    assert "OpportunityRecord" in {e["symbol"] for e in payload}


def test_manifest_rejects_non_python(tmp_path):
    f = tmp_path / "x.ts"
    f.write_text("const x = 1;\n")
    result = invoke("manifest", str(f))
    assert result.exit_code == 1
    assert "Python-only" in result.output  # clean error, not a raw traceback


def test_manifest_handles_unparseable_python(tmp_path):
    f = tmp_path / "broken.py"
    f.write_text("def f(:\n")
    result = invoke("manifest", str(f))
    assert result.exit_code == 1
    assert "could not parse" in result.output
