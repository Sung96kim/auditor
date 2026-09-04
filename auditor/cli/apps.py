"""The root typer ``app`` — every command module registers its handler on it (or defines its own
sub-app); ``cli/__init__`` is the composition root that imports every command module and mounts the
sub-apps. Depends only on ``cli.helpers`` and the notice, so it stays a safe import for the command
modules, which already import ``cli.helpers``. Shared consoles live in ``cli.console``.
"""

import typer

from auditor.cli.helpers import flush_config_notice
from auditor.config_notice import NOTICE

app = typer.Typer(add_completion=False, help="A deterministic codebase auditor.")


@app.callback(invoke_without_command=True)
def _main(ctx: typer.Context) -> None:
    """A deterministic codebase auditor."""
    # Show help and exit 0 on a bare ``auditor`` (no subcommand). Typer's no_args_is_help
    # exits 2, which wrappers like ``uv run`` report as a command failure.
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)
    NOTICE.reset()
    # closes after the command's own output, on every exit path including a clean `fail()`
    ctx.call_on_close(flush_config_notice)
