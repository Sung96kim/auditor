"""``auditor rules list`` — enumerate every registered detector rule."""

from typing import Annotated

import typer

from auditor.cli.helpers import _echo_json
from auditor.registry import REGISTRY

rules_app = typer.Typer(no_args_is_help=True, help="Inspect detector rules.")


@rules_app.command("list")
def rules_list(
    category: Annotated[
        str | None, typer.Option("-c", "--category", help="Filter by category.")
    ] = None,
    standard: Annotated[
        str | None, typer.Option("-s", "--standard", help="bandit | owasp coverage.")
    ] = None,
) -> None:
    """List every registered detector rule."""
    rows = []
    for rid in sorted(REGISTRY.rule_ids()):
        det = REGISTRY.detector(rid)
        if category and str(det.category) != category:
            continue
        refs = list(det.standard_refs)
        if standard and not any(r.startswith(f"{standard}:") for r in refs):
            continue
        rows.append(
            {
                "rule_id": rid,
                "category": str(det.category),
                "default_severity": det.default_severity.value,
                "verdict_kind": det.verdict_kind.value,
                "standard_refs": refs,
                "source": REGISTRY.source_of("detector", rid),
            }
        )
    _echo_json(rows)
