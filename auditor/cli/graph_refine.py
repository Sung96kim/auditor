"""``auditr graph unresolved`` — the deterministic refinement queue.

``cli/graph.py`` calls :func:`register` at the bottom of its module; this module never imports it
back, so the ``graph`` sub-app stays a one-way dependency.
"""

from pathlib import Path
from typing import Any

import typer

from auditor.cli.helpers import open_index, present, run
from auditor.cli.options import GraphTarget, QueueCallForm, QueueLimit, QueueReason
from auditor.cli.render import render_graph_unresolved
from auditor.discovery import find_root


async def _unresolved_rows(
    root: Path,
    *,
    reasons: list[str] | None,
    call_forms: list[str] | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Read the queue in drain order. The call-form filter is applied after the read so the limit
    always counts rows the caller actually sees."""
    async with await open_index(root) as index:
        rows = await index.graph.unresolved(reasons=reasons)
    if call_forms:
        rows = [r for r in rows if r["call_form"] in call_forms]
    return rows[:limit]


def graph_unresolved(
    target: GraphTarget = Path("."),
    reason: QueueReason = None,
    call_form: QueueCallForm = None,
    limit: QueueLimit = 50,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Facts the deterministic resolver could not place, worst-first."""
    root = find_root(target)
    present(
        run(
            _unresolved_rows(root, reasons=reason, call_forms=call_form, limit=limit),
            "reading queue…",
        ),
        render_graph_unresolved,
        as_json=json_,
    )


def register(app: typer.Typer) -> None:
    """Mount this module's commands onto the ``graph`` sub-app."""
    app.command("unresolved")(graph_unresolved)
