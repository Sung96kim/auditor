"""``auditr graph unresolved`` — the deterministic refinement queue.

``cli/graph.py`` calls :func:`register` at the bottom of its module; this module never imports it
back, so the ``graph`` sub-app stays a one-way dependency.
"""

from functools import partial
from pathlib import Path
from typing import Any

import typer

from auditor.cli.helpers import open_index, present, run
from auditor.cli.options import (
    GraphTarget,
    QueueCallForm,
    QueueExternal,
    QueueLimit,
    QueueReason,
)
from auditor.cli.render import render_graph_unresolved
from auditor.discovery import find_root
from auditor.graph.model import (
    QUEUE_ROW_LIMIT,
    CallForm,
    UnresolvedReason,
    capped_row,
)


async def _unresolved_rows(
    root: Path,
    *,
    reasons: list[UnresolvedReason] | None,
    call_forms: list[CallForm] | None,
    limit: int,
    external: bool,
) -> list[dict[str, Any]]:
    """Read the queue in drain order. Both filters and the limit are pushed into the query, so
    the limit always counts rows the caller actually sees and a big queue never lands whole."""
    async with await open_index(root) as index:
        rows = await index.graph.unresolved(
            reasons=[r.value for r in reasons] if reasons else None,
            call_forms=[c.value for c in call_forms] if call_forms else None,
            limit=limit,
            external=external,
        )
    return [capped_row(r) for r in rows]


def graph_unresolved(
    target: GraphTarget = Path("."),
    reason: QueueReason = None,
    call_form: QueueCallForm = None,
    limit: QueueLimit = QUEUE_ROW_LIMIT,
    external: QueueExternal = True,
    json_: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Facts the deterministic resolver could not place, worst-first."""
    root = find_root(target)
    present(
        run(
            _unresolved_rows(
                root,
                reasons=reason,
                call_forms=call_form,
                limit=limit,
                external=external,
            ),
            "reading queue…",
        ),
        partial(
            render_graph_unresolved,
            filtered=bool(reason or call_form or not external),
        ),
        as_json=json_,
    )


def register(app: typer.Typer) -> None:
    """Mount this module's commands onto the ``graph`` sub-app."""
    app.command("unresolved")(graph_unresolved)
