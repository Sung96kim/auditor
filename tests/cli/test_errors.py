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


@pytest.mark.parametrize(
    "layout",
    [
        "home_is_a_file",  # $AUDITOR_HOME already exists as a regular file
        "home_under_a_file",  # a parent of $AUDITOR_HOME is a regular file
        "repos_is_a_file",  # the home is fine, repos/ is not a directory
    ],
)
def test_init_fails_cleanly_on_an_unusable_home(tmp_path, monkeypatch, layout):
    """init was the first command to let an OSError escape as a typer traceback."""
    blocker = tmp_path / "blocked"
    blocker.write_text("")
    home = {
        "home_is_a_file": blocker,
        "home_under_a_file": blocker / "home",
        "repos_is_a_file": tmp_path / "home",
    }[layout]
    args = ["init", "--root", str(tmp_path)]
    if layout == "repos_is_a_file":
        home.mkdir()
        (home / "repos").write_text("")
        args.append("--repo")
    monkeypatch.setenv("AUDITOR_HOME", str(home))
    result = invoke(*args)
    assert result.exit_code == 1
    assert "cannot write the auditor home" in " ".join(result.output.split())
    assert "Traceback" not in result.output
