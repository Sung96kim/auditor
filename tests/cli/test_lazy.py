"""The lazy ``graph`` mount: full help without the graph stack, real commands on dispatch."""

import subprocess
import sys
import types
from pathlib import Path
from typing import ClassVar

import pytest
import typer
from _support import invoke
from typer.testing import CliRunner

from auditor.cli.lazy import LazyGroup

_ROOT = Path(__file__).resolve().parents[2]
_HEAVY = ("auditor.cli.graph", "auditor.graph.build", "networkx", "sklearn")
_PROBE = f"import sys, auditor.cli\nprint([m for m in {_HEAVY!r} if m in sys.modules])"
_SUBCOMMANDS = (
    "build",
    "serve",
    "export",
    "related",
    "neighbors",
    "concept",
    "clusters",
    "search",
    "usages",
)


@pytest.fixture
def scratch_group(monkeypatch: pytest.MonkeyPatch) -> type[LazyGroup]:
    """A LazyGroup bound to an in-memory sub-app that has a callback option and an epilog."""
    sub_app = typer.Typer(
        no_args_is_help=True, help="Scratch.", epilog="Scratch epilog."
    )

    @sub_app.callback()
    def _flavored(flavor: str = typer.Option("vanilla")) -> None:
        typer.echo(f"flavor={flavor}")

    @sub_app.command()
    def taste() -> None:
        typer.echo("tasted")

    stub = types.ModuleType("auditor_lazy_scratch")
    stub.sub_app = sub_app
    monkeypatch.setitem(sys.modules, "auditor_lazy_scratch", stub)

    class _ScratchGroup(LazyGroup):
        module: ClassVar[str] = "auditor_lazy_scratch"
        attribute: ClassVar[str] = "sub_app"

    return _ScratchGroup


def test_root_help_lists_graph_with_its_help_text():
    result = invoke("--help")
    assert result.exit_code == 0, result.output
    assert "graph" in result.output
    assert "semantic code graph" in result.output


def test_importing_the_cli_does_not_import_the_graph_stack():
    """A fresh interpreter: nothing under auditor.cli may pull numpy/scikit-learn/networkx in."""
    probe = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert probe.stdout.strip() == "[]"


def test_graph_help_lists_the_real_subcommands():
    result = invoke("graph", "--help")
    assert result.exit_code == 0, result.output
    for name in _SUBCOMMANDS:
        assert name in result.output, name


@pytest.mark.parametrize(
    ("command", "flag"), [("build", "--rebuild"), ("export", "--format")]
)
def test_dispatching_a_graph_subcommand_resolves_the_real_command(
    command: str, flag: str
):
    result = invoke("graph", command, "--help")
    assert result.exit_code == 0, result.output
    assert flag in result.output


def test_unknown_graph_subcommand_still_fails():
    assert invoke("graph", "nope").exit_code == 2


def test_the_mount_adopts_the_sub_apps_callback_and_epilog(
    scratch_group: type[LazyGroup],
):
    """``parse_args`` runs before any command lookup, so a callback option must load with it."""
    root = typer.Typer()
    root.add_typer(
        typer.Typer(no_args_is_help=True, help="Scratch."),
        name="scratch",
        cls=scratch_group,
    )
    runner = CliRunner()

    shown = runner.invoke(root, ["scratch", "--help"])
    assert shown.exit_code == 0, shown.output
    assert "--flavor" in shown.output
    assert "Scratch epilog." in shown.output

    ran = runner.invoke(root, ["scratch", "--flavor", "mint", "taste"])
    assert ran.exit_code == 0, ran.output
    assert "flavor=mint" in ran.output
    assert "tasted" in ran.output


def test_an_unbound_lazy_group_names_the_missing_attribute():
    with pytest.raises(RuntimeError, match="LazyGroup.module is not set"):
        LazyGroup(name="unbound")._load()
