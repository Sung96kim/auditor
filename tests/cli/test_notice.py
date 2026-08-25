"""One unknown-config-key block per CLI invocation, from the root callback rather than from each
command, and none at all from the commands that never load config."""

import click
import pytest
from _support import assert_no_escape, cli_json, invoke
from typer.core import TyperGroup
from typer.main import get_group

import auditor.config
import auditor.config_notice
from auditor.cli import app
from auditor.paths import user_config_path

# Commands that resolve a repo root, so the notice reports on that root.
WARNING_COMMANDS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("aggregate",),
        ("crossfile",),
        ("discover",),
        ("report",),
        ("scan",),
        ("config", "show"),
        ("graph", "build"),
        ("graph", "clusters"),
        ("graph", "concept"),
        ("graph", "export"),
        ("graph", "flow"),
        ("graph", "neighbors"),
        ("graph", "related"),
        ("graph", "search"),
        ("graph", "serve"),
        ("graph", "unresolved"),
        ("graph", "usages"),
        ("ignore", "add"),
        ("ignore", "clear"),
        ("ignore", "list"),
        ("ignore", "rm"),
        ("index", "add"),
        ("index", "forget"),
        ("index", "list"),
        ("plugins", "list"),
        ("rules", "list"),
    }
)
# Commands that print the unknown keys themselves, or never look at a repo config at all.
SILENT_COMMANDS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("init",),
        ("manifest",),
        ("version",),
        ("config", "check"),
        ("index", "repos"),
        ("malware", "install"),
        ("malware", "status"),
        ("malware", "update-dbs"),
        ("self", "update"),
    }
)


def _command_paths() -> set[tuple[str, ...]]:
    """Every dispatchable path in the CLI, walking the sub-apps the composition root mounts.

    The group check is `TyperGroup`, not `click.Group`: typer 0.26 vendors its own click core, so
    a mounted sub-app is not an instance of the stdlib class.
    """
    group = get_group(app)
    ctx = click.Context(group)
    out: set[tuple[str, ...]] = set()
    for name in group.list_commands(ctx):
        command = group.get_command(ctx, name)
        if isinstance(command, TyperGroup):
            sub = click.Context(command, parent=ctx)
            out |= {(name, child) for child in command.list_commands(sub)}
        else:
            out.add((name,))
    return out


def test_the_command_registry_is_fully_classified():
    """A new command has to be put in one bucket or the other, so it cannot ship undecided."""
    assert _command_paths() == WARNING_COMMANDS | SILENT_COMMANDS


@pytest.mark.parametrize(
    "argv",
    [
        ("discover", "{repo}"),
        ("scan", "--no-index", "{repo}"),
        ("crossfile", "{repo}"),
        ("report", "{repo}/a.py"),
        ("aggregate", "{repo}", "-o", "{repo}/AUDIT.md"),
        ("config", "show", "-r", "{repo}"),
        ("ignore", "list", "-r", "{repo}"),
        ("index", "list", "-r", "{repo}"),
        ("plugins", "list", "-r", "{repo}"),
        ("rules", "list", "-r", "{repo}"),
        ("graph", "clusters", "{repo}"),
        ("graph", "unresolved", "{repo}"),
    ],
)
def test_a_config_loading_command_warns_exactly_once(bad_config, argv):
    """Twelve of the twenty-six warning commands, one per command module plus both graph
    modules, so the property is not proved on `discover` alone."""
    result = invoke(*(part.format(repo=bad_config) for part in argv))
    assert result.exit_code == 0, result.output
    assert "unknown config key: bogus" in result.stderr
    assert result.stderr.count("unknown config key") == 1


def test_the_warning_survives_a_command_that_exits_non_zero(bad_config):
    """The notice flushes on context close, so a clean failure still reports the typo that may be
    the reason for it."""
    result = invoke("ignore", "rm", "nosuch", "-r", str(bad_config))
    assert result.exit_code == 1
    assert "unknown config key: bogus" in result.stderr


def test_a_user_settings_typo_is_reported_too(bad_config):
    user_config_path().parent.mkdir(parents=True, exist_ok=True)
    user_config_path().write_text('{"observer": {"runer": "claude"}}')
    result = invoke("discover", str(bad_config))
    assert "unknown config key: bogus" in result.stderr
    assert "unknown config key: observer.runer" in result.stderr


def test_a_repo_policy_that_cannot_be_read_still_reports_the_user_keys(bad_config):
    """The two sources are independent. Concatenating them inside one guard meant an unrelated
    repo's `extends` typo decided whether the user heard about their own."""
    (bad_config / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nextends = "nope"\n'
    )
    user_config_path().parent.mkdir(parents=True, exist_ok=True)
    user_config_path().write_text('{"observer": {"runer": "claude"}}')

    result = invoke("index", "list", "-r", str(bad_config))

    assert result.exit_code == 0, result.output
    assert "unknown config key: observer.runer" in result.stderr


def test_a_user_layer_that_cannot_be_read_still_reports_the_repo_keys(
    bad_config, monkeypatch
):
    """The other direction of the same guard."""

    def _boom(*args, **kwargs):
        raise OSError("unreadable")

    monkeypatch.setattr(auditor.config_notice, "unknown_user_keys", _boom)
    result = invoke("discover", str(bad_config))
    assert "unknown config key: bogus" in result.stderr


def test_a_command_that_never_loads_policy_stays_clean_on_a_bad_profile(bad_config):
    """`graph clusters` resolves a root but never loads policy, so the notice swallowing the
    profile error is the only thing between it and a traceback while the context closes."""
    (bad_config / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nextends = "nope"\n'
    )
    result = invoke("graph", "clusters", str(bad_config))
    assert result.exit_code == 0, result.output
    assert_no_escape(result)


def test_each_repo_gets_its_own_notice_in_one_process(bad_config, tmp_path):
    """Two invocations in one interpreter: the second must not inherit the first's report."""
    other = tmp_path / "other"
    other.mkdir()
    (other / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\n[tool.auditor]\nalso_bogus = 1\n'
    )

    first = invoke("index", "list", "-r", str(bad_config))
    second = invoke("index", "list", "-r", str(other))

    assert "unknown config key: bogus" in first.stderr
    assert "unknown config key: also_bogus" in second.stderr


def test_a_loaded_config_is_not_merged_a_second_time(bad_config, monkeypatch):
    """The loader already collected the unknown keys; the notice reads them off the settings
    instead of paying for the whole profile chain and both repo TOMLs again."""
    merges: list[str] = []
    monkeypatch.setattr(
        auditor.config_notice,
        "unknown_repo_keys",
        lambda *args, **kwargs: merges.append("merged") or [],
    )

    result = invoke("config", "show", "-r", str(bad_config))

    assert "unknown config key: bogus" in result.stderr
    assert merges == []


def test_config_check_owns_its_own_report(bad_config):
    """Its payload is the unknown keys, so a stderr block would say everything twice."""
    result = invoke("config", "check", "-r", str(bad_config), "--json")
    assert cli_json(result)["policy_unknown"] == ["bogus"]
    assert "unknown config key" not in result.stderr


def test_init_owns_its_own_report(bad_config):
    result = invoke("init", "--check", "-r", str(bad_config))
    assert result.exit_code == 0, result.output
    assert "unknown config key" not in result.stderr


def test_version_never_reads_a_repo_config(monkeypatch):
    """`auditr version` is the fast path: it must not pay for a config merge, ever."""

    def _boom(*args, **kwargs):
        raise AssertionError("version must not merge a repo config")

    monkeypatch.setattr(auditor.config, "merged_config_dict", _boom)
    result = invoke("version")
    assert result.exit_code == 0, result.output


def test_json_stdout_stays_pure_with_an_unknown_key(bad_config):
    result = invoke("discover", str(bad_config), "--json")
    payload = cli_json(result)  # parses => stdout is pure JSON
    assert isinstance(payload, list)
    assert "bogus" not in result.stdout
