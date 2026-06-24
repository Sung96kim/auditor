"""The root typer ``app`` and the stderr status console — the two things every command module
shares. Each command module registers its handler on ``app`` (or defines its own sub-app);
``cli/__init__`` is the composition root that imports every command module and mounts the
sub-apps. Kept dependency-free so it stays a safe leaf import for the command modules.
"""

import typer
from rich.console import Console

app = typer.Typer(add_completion=False, help="A deterministic codebase auditor.")


@app.callback(invoke_without_command=True)
def _main(ctx: typer.Context) -> None:
    """A deterministic codebase auditor."""
    # Show help and exit 0 on a bare ``auditor`` (no subcommand). Typer's no_args_is_help
    # exits 2, which wrappers like ``uv run`` report as a command failure.
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


# Goes to STDERR so it never corrupts the JSON/SARIF stdout that agents parse; rich auto-disables
# the spinner when stderr isn't a TTY (piped/captured output). The human summary uses its own
# stdout console in `cli.summary`.
_status = Console(stderr=True)
