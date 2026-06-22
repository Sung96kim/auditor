"""Cross-command clean-error behavior: a bad target or a bad --format exits non-zero with a
one-line message, never a raw traceback."""

import pytest
from _support import invoke


def test_bare_invocation_shows_help_and_exits_zero():
    """Regression: bare `auditor` prints help and exits 0 (not Typer's no-args exit 2, which
    `uv run` reports as PackageManagerExecutionFailed)."""
    result = invoke()
    assert result.exit_code == 0
    assert "Usage" in result.output and "scan" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("cmd", ["scan", "report", "manifest", "discover"])
def test_missing_target_fails_cleanly(cmd):
    result = invoke(cmd, "does/not/exist.py")
    assert result.exit_code == 1
    assert "no such file" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("cmd", [("scan", "src"), ("report", "src/web.py")])
def test_invalid_format_errors_cleanly(sample_repo, cmd):
    name, target = cmd
    result = invoke(name, str(sample_repo / target), "-f", "xml")
    assert result.exit_code == 1
    assert "unknown format" in result.output
    assert "Traceback" not in result.output  # clean error, not a raw stack trace
