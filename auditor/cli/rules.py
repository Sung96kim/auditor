"""``auditor rules list`` — enumerate every registered detector rule."""

from typing import Annotated

import typer

from auditor.cli.helpers import _echo_json, _fail
from auditor.registry import REGISTRY

rules_app = typer.Typer(no_args_is_help=True, help="Inspect detector rules.")


def _known_standards() -> set[str]:
    return {
        ref.split(":", 1)[0]
        for rid in REGISTRY.rule_ids()
        for ref in REGISTRY.detector(rid).standard_refs
    }


@rules_app.command("list")
def rules_list(
    category: Annotated[
        str | None, typer.Option("-c", "--category", help="Filter by category.")
    ] = None,
    standard: Annotated[
        str | None, typer.Option("-s", "--standard", help="bandit | owasp coverage.")
    ] = None,
    framework: Annotated[
        str | None,
        typer.Option("-f", "--framework", help="Filter by framework (e.g. pytest)."),
    ] = None,
) -> None:
    """List every registered detector rule."""
    if category is not None and category not in REGISTRY.categories():
        _fail(
            f"unknown category {category!r}; choose from {sorted(REGISTRY.categories())}"
        )
    if standard is not None and standard not in (known := _known_standards()):
        _fail(f"unknown standard {standard!r}; choose from {sorted(known)}")
    if framework is not None and framework not in REGISTRY.frameworks():
        _fail(
            f"unknown framework {framework!r}; choose from {sorted(REGISTRY.frameworks())}"
        )
    rows = []
    for rid in sorted(REGISTRY.rule_ids()):
        det = REGISTRY.detector(rid)
        if category and str(det.category) != category:
            continue
        if framework and getattr(det, "framework", None) != framework:
            continue
        refs = list(det.standard_refs)
        if standard and not any(r.startswith(f"{standard}:") for r in refs):
            continue
        rows.append(
            {
                "rule_id": rid,
                "category": str(det.category),
                "framework": getattr(det, "framework", None),
                "default_severity": det.default_severity.value,
                "verdict_kind": det.verdict_kind.value,
                "standard_refs": refs,
                "source": REGISTRY.source_of("detector", rid),
            }
        )
    _echo_json(rows)
