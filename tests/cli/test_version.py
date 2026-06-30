"""`auditor version` — prints the installed auditr version."""

from _support import invoke

from auditor import __version__


def test_version_prints_version():
    result = invoke("version")
    assert result.exit_code == 0, result.output
    assert "auditr" in result.output
    assert __version__ in result.output
