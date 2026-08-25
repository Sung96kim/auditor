"""Cross-command clean-error behavior: a bad target or a bad --format exits non-zero with a
one-line message, never a raw traceback."""

from pathlib import Path

import pytest
from _support import BROKEN_CONFIGS, assert_no_escape, invoke, write_broken_config

import auditor.cli
from auditor.database.base import Column
from auditor.database.ignores import IgnoresDB


def one_line(text: str) -> str:
    """``text`` with its wrapping undone: rich breaks a long line at 80 columns off a TTY."""
    return " ".join(text.split())


def test_bare_invocation_shows_help_and_exits_zero():
    """Regression: bare `auditor` prints help and exits 0 (not Typer's no-args exit 2, which
    `uv run` reports as PackageManagerExecutionFailed)."""
    result = invoke()
    assert result.exit_code == 0
    assert "Usage" in result.output and "scan" in result.output
    assert_no_escape(result)


@pytest.mark.parametrize("cmd", ["scan", "report", "manifest", "discover"])
def test_missing_target_fails_cleanly(cmd):
    result = invoke(cmd, "does/not/exist.py")
    assert result.exit_code == 1
    assert "no such file" in result.output
    assert_no_escape(result)


@pytest.mark.parametrize("cmd", [("scan", "src"), ("report", "src/web.py")])
def test_invalid_format_errors_cleanly(sample_repo, cmd):
    name, target = cmd
    result = invoke(name, str(sample_repo / target), "-f", "xml")
    assert result.exit_code == 1
    assert "unknown format" in result.output
    assert_no_escape(result)  # a clean error, not a raw stack trace


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
    assert_no_escape(result)


def test_an_unmigratable_index_prints_the_repair(monkeypatch):
    """A declaration SQLite cannot add to an identity table breaks every connect, so the CLI has
    to name the repair instead of printing a traceback the user cannot act on."""
    table = IgnoresDB.TABLES["ignores"]
    invoke("index", "list")  # create the index under the isolated home
    monkeypatch.setitem(
        IgnoresDB.TABLES,
        "ignores",
        table.model_copy(
            update={
                "cols": (*table.cols, Column(name="added", type="TEXT", not_null=True))
            }
        ),
    )
    result = invoke("index", "list")
    assert result.exit_code == 1
    assert "cannot be upgraded" in result.output
    assert "ignores.added" in result.output and "rm " in result.output
    assert_no_escape(result)


def _under_a_file(tmp_path: Path) -> Path:
    """A report path whose parent is a regular file."""
    (tmp_path / "notadir").write_text("")
    return tmp_path / "notadir" / "sub" / "report.json"


def _an_existing_directory(tmp_path: Path) -> Path:
    """A report path that is itself a directory."""
    (tmp_path / "adir").mkdir()
    return tmp_path / "adir"


@pytest.mark.parametrize("command", ["scan", "aggregate"])
@pytest.mark.parametrize(
    "make_output",
    [_under_a_file, _an_existing_directory],
    ids=["parent_is_a_file", "output_is_a_directory"],
)
def test_unwritable_output_path_fails_cleanly(
    sample_repo, tmp_path, command, make_output
):
    """A `-o` path that cannot be written exits 1 with one line, never a traceback."""
    result = invoke(command, str(sample_repo / "src"), "-o", str(make_output(tmp_path)))

    assert result.exit_code == 1
    assert "cannot write" in result.output
    assert_no_escape(result)


@pytest.mark.parametrize(
    "argv",
    [
        ["config", "show", "--config-json", '{"extends":"nope"}', "-r"],
        ["config", "check", "--config-json", '{"extends":"nope"}', "-r"],
        ["discover", "--config-json", '{"extends":"nope"}'],
    ],
)
def test_a_bad_profile_fails_on_one_line(tmp_path, argv):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    result = invoke(*argv, str(tmp_path))
    assert result.exit_code == 1
    assert_no_escape(result)
    assert "nope" in result.output


def test_a_bad_profile_flag_fails_on_one_line(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    (tmp_path / "a.py").write_text("x = 1\n")
    result = invoke("scan", str(tmp_path), "--profile", "nope", "--no-index")
    assert result.exit_code == 1
    assert_no_escape(result)
    assert "unknown profile 'nope'" in one_line(result.stderr)


@pytest.mark.parametrize("argv", [["crossfile"], ["graph", "build"]])
def test_a_bad_extends_in_the_repo_toml_fails_on_one_line(tmp_path, argv):
    """The same defect reached every command through the repo's own TOML, not just --profile, and
    `graph build` reaches the loader twice: once directly and once through the auto-scan."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nextends = "nope"\n'
    )
    result = invoke(*argv, str(tmp_path))
    assert result.exit_code == 1
    assert_no_escape(result)
    assert "unknown profile 'nope'" in one_line(result.stderr)


# Every command surface that reaches the config loader, by the route it reaches it: directly,
# through an audit, through a staged build, or through a server it never gets to start.
CONFIG_SURFACES = [
    ("config", "show", "-r"),
    ("config", "check", "-r"),
    ("crossfile",),
    ("discover",),
    ("graph", "build"),
    ("graph", "serve"),
    ("report",),
    ("scan", "--no-index"),
]


@pytest.mark.parametrize("kind", sorted(BROKEN_CONFIGS))
@pytest.mark.parametrize(
    "surface", CONFIG_SURFACES, ids=lambda s: "_".join(s).strip("-")
)
def test_a_config_that_cannot_be_used_is_one_line_on_every_surface(
    tmp_path, kind, surface
):
    """One error hierarchy, one guard per surface: an unknown profile, a cycle and unparseable
    TOML all exit 1 with a named line, and none of them escapes as an exception."""
    phrase = write_broken_config(tmp_path, kind)
    (tmp_path / "a.py").write_text("x = 1\n")
    target = str(tmp_path / "a.py") if surface[0] == "report" else str(tmp_path)

    result = invoke(*surface, target)

    assert_no_escape(result)
    assert result.exit_code == 1
    assert phrase in one_line(result.stderr)


def test_only_the_cli_edge_calls_load_config_directly():
    """Every command loads through `load_settings`, so a config failure is a line and not a
    traceback. A new command that calls the loader directly re-opens the bug. `helpers.py` is the
    one exception: it holds `load_settings`, which is where the call belongs."""
    offenders = [
        path.name
        for path in sorted(Path(auditor.cli.__file__).parent.glob("*.py"))
        if path.name != "helpers.py"
        and "load_config(" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
