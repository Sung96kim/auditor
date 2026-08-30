"""Deferred sub-app mounting. Importing ``auditor.cli.graph`` pulls in numpy, scikit-learn and
networkx (~0.65 s measured), a cost every fast command would otherwise pay just to build the help
tree, so a ``LazyGroup`` mount resolves its commands only when one is actually dispatched.
"""

import importlib
from typing import ClassVar

import click
import typer
from typer.core import TyperCommand, TyperGroup
from typer.main import get_group

GRAPH_HELP = "Build + query the semantic code graph."
OBSERVER_HELP = "Start, stop and inspect the background observer daemon."

# Everything `typer.main.get_group` sets that belongs to the sub-app rather than to the mount.
# Excluded on purpose: `name`, `help`, `hidden`, `deprecated`, `context_settings` and the rich
# settings, which the `add_typer` call owns.
_ADOPTED: tuple[str, ...] = (
    "callback",
    "params",
    "epilog",
    "short_help",
    "options_metavar",
    "add_help_option",
    "no_args_is_help",
    "invoke_without_command",
    "subcommand_metavar",
    "_result_callback",
)


def _mounted(module: str, attribute: str) -> TyperGroup:
    """Import ``module`` and return ``attribute``'s Typer sub-app as a resolved click group."""
    # The one sanctioned deferred import in the package; everything else imports at module level.
    mod = importlib.import_module(module)
    return get_group(getattr(mod, attribute))


class LazyGroup(TyperGroup):
    """Typer group that adopts ``attribute``'s commands and options from ``module`` on first use."""

    module: ClassVar[str] = ""
    attribute: ClassVar[str] = ""
    _loaded: bool = False
    _failure: click.ClickException | None = None

    def _load(self) -> None:
        """Mount the sub-app once, recording completion only after every attribute is adopted.

        A failed import is cached and re-raised, so a second dispatch reports the same error
        instead of presenting an empty group as a working one.
        """
        if self._failure is not None:
            raise self._failure
        if self._loaded:
            return
        if not self.module:
            raise RuntimeError("LazyGroup.module is not set")
        if not self.attribute:
            raise RuntimeError("LazyGroup.attribute is not set")
        try:
            mounted = _mounted(self.module, self.attribute)
        except ImportError as exc:
            # `self.name` is the mount name click gave the group, so the message names the command.
            self._failure = click.ClickException(
                f"`auditr {self.name}` is unavailable: {exc}"
            )
            raise self._failure from exc
        self.commands.update(mounted.commands)
        for name in _ADOPTED:
            setattr(self, name, getattr(mounted, name))
        self._loaded = True

    def parse_args(self, ctx: typer.Context, args: list[str]) -> list[str]:
        self._load()
        return super().parse_args(ctx, args)

    def get_command(
        self, ctx: typer.Context, cmd_name: str
    ) -> TyperCommand | TyperGroup | None:
        self._load()
        return super().get_command(ctx, cmd_name)

    def list_commands(self, ctx: typer.Context) -> list[str]:
        self._load()
        return super().list_commands(ctx)


class LazyGraphGroup(LazyGroup):
    """The ``auditr graph`` mount; ``auditor.cli.graph`` is imported on the first subcommand."""

    module: ClassVar[str] = "auditor.cli.graph"
    attribute: ClassVar[str] = "graph_app"


# `no_args_is_help` belongs to `graph_app` and is adopted on load; only `help` is the mount's own,
# so the root help tree renders without paying for the graph import.
lazy_graph_app = typer.Typer(help=GRAPH_HELP)


class LazyObserverGroup(LazyGroup):
    """The ``auditr observer`` mount; `auditor.cli.observer` reaches the daemon and the store."""

    module: ClassVar[str] = "auditor.cli.observer"
    attribute: ClassVar[str] = "observer_app"


lazy_observer_app = typer.Typer(help=OBSERVER_HELP)
