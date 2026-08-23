"""Deferred sub-app mounting. Importing ``auditor.cli.graph`` pulls in numpy, scikit-learn and
networkx (~0.65 s measured), a cost every fast command would otherwise pay just to build the help
tree, so a ``LazyGroup`` mount resolves its commands only when one is actually dispatched.
"""

import importlib
from typing import ClassVar

import typer
from typer.core import TyperCommand, TyperGroup
from typer.main import get_group

GRAPH_HELP = "Build + query the semantic code graph."

# Everything `typer.main.get_group` sets that belongs to the sub-app rather than to the mount.
# Excluded on purpose: `name`, `help`, `hidden`, `deprecated`, `context_settings` and the rich
# settings, which the `add_typer` call owns.
_ADOPTED = (
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
    # Documentary: the detector does not flag importlib, kept to mark the one sanctioned deferred import.
    mod = importlib.import_module(module)  # auditor: skip: PY-STYLE-INLINE-IMPORT
    return get_group(getattr(mod, attribute))


class LazyGroup(TyperGroup):
    """Typer group that adopts ``attribute``'s commands and options from ``module`` on first use."""

    module: ClassVar[str] = ""
    attribute: ClassVar[str] = ""
    _loaded: bool = False

    def _load(self) -> None:
        if self._loaded:
            return
        if not self.module:
            raise RuntimeError("LazyGroup.module is not set")
        if not self.attribute:
            raise RuntimeError("LazyGroup.attribute is not set")
        self._loaded = True
        mounted = _mounted(self.module, self.attribute)
        self.commands.update(mounted.commands)
        for name in _ADOPTED:
            setattr(self, name, getattr(mounted, name))

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


lazy_graph_app = typer.Typer(no_args_is_help=True, help=GRAPH_HELP)
