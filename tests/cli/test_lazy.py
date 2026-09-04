"""The lazy ``graph`` mount: full help without the graph stack, real commands on dispatch."""

import importlib
import subprocess
import sys
import types
from pathlib import Path
from typing import ClassVar

import click
import pytest
import typer
from _support import invoke
from typer.main import get_group
from typer.testing import CliRunner

from auditor.cli.graph import graph_app
from auditor.cli.lazy import _ADOPTED, LazyGraphGroup, LazyGroup

_ROOT = Path(__file__).resolve().parents[2]
_HEAVY = (
    "auditor.cli.graph",
    "auditor.graph.build",
    # `render.py` imports the refine wire payload, so its chain is on every fast command's path
    "auditor.graph.refine.eval",
    "auditor.graph.refine.facts",
    "auditor.graph.refine.verify",
    "auditor.graph.resolve_edges",
    # the observer mount is the second lazy mount; its daemon and transport must stay off too
    "auditor.cli.observer",
    "auditor.observer.daemon",
    "auditor.observer.loop",
    "auditor.observer.routes",
    "auditor.observer.scheduling",
    "auditor.observer.server",
    "auditor.graph.scan",
    "numpy",
    "scipy",
    "networkx",
    "sklearn",
)
#: the observer modules that are deliberately on the fast path: `cli/render.py` imports the
#: wire payloads to render them, so the pin says which ones may cost, not only which may not
_EAGER = (
    "auditor.observer.payloads",
    "auditor.observer.budget",
)
_LOADED = f"print([m for m in {_HEAVY!r} if m in sys.modules], file=sys.stderr)"
_EAGER_PROBE = (
    "import sys, auditor.cli\n"
    f"print([m for m in {_EAGER!r} if m not in sys.modules], file=sys.stderr)\n"
)
_IMPORT_PROBE = f"import sys, auditor.cli\n{_LOADED}\n"
_HELP_PROBE = f"import sys\nfrom auditor.cli import app\napp(['--help'], standalone_mode=False)\n{_LOADED}\n"
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
    "flow",
    "unresolved",
    "refine",
    "eval",
    "refinements",
    "tuning",
    "log",
)


def _fail_to_import(name: str) -> None:
    raise ImportError(f"No module named {name!r}")


def _probe(source: str) -> str:
    """Run ``source`` in a fresh interpreter and return what it wrote to stderr."""
    run = subprocess.run(
        [sys.executable, "-c", source],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return run.stderr.strip()


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
    """A fresh interpreter: nothing under auditor.cli may pull numpy/scipy/scikit-learn/networkx in."""
    assert _probe(_IMPORT_PROBE) == "[]"


def test_the_eager_observer_modules_are_named_as_such():
    """`_HEAVY` says what may not load; without a companion nothing says what deliberately does.

    `auditor/cli/render.py` imports `DaemonStatus` and the budget payload to render them, so both
    modules are on every fast command's path and their cost is a decision, not an accident.
    """
    assert _probe(_EAGER_PROBE) == "[]"


def test_rendering_the_root_help_does_not_import_the_graph_stack():
    """Importing is only half of it — building and printing the whole help tree must stay cheap."""
    assert _probe(_HELP_PROBE) == "[]"


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


def test_the_mount_adopts_every_attribute_in_the_allowlist():
    """``_ADOPTED`` is a positive allowlist; a name that stops being copied is otherwise silent."""
    mount = LazyGraphGroup(name="graph")
    mount._load()
    reference = get_group(graph_app)
    for name in _ADOPTED:
        assert getattr(mount, name) == getattr(reference, name), name


def test_a_failing_deferred_import_surfaces_as_a_click_exception(
    monkeypatch: pytest.MonkeyPatch,
):
    """A broken graph dependency prints one line, and the next dispatch repeats it."""
    mount = LazyGraphGroup(name="graph")
    monkeypatch.setattr(importlib, "import_module", _fail_to_import)

    with pytest.raises(click.ClickException) as first:
        mount._load()
    assert "`auditr graph` is unavailable" in first.value.message
    assert not mount.commands

    monkeypatch.undo()
    with pytest.raises(click.ClickException) as second:
        mount._load()
    assert second.value is first.value


@pytest.mark.parametrize(
    ("module", "attribute", "expected"),
    [
        ("", "", "LazyGroup.module is not set"),
        ("auditor.cli.graph", "", "LazyGroup.attribute is not set"),
    ],
)
def test_an_unbound_lazy_group_names_the_class_var_it_is_missing(
    module: str, attribute: str, expected: str
):
    unbound: type[LazyGroup] = type(
        "_Unbound", (LazyGroup,), {"module": module, "attribute": attribute}
    )
    with pytest.raises(RuntimeError, match=expected):
        unbound(name="unbound")._load()
