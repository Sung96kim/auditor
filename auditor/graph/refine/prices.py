"""The shipped Codex price table and the estimate it feeds (spec 8.4, spec 21).

Codex reports tokens and never dollars, so every Codex run's cost is derived here and stamped
``cost_estimated``. A model this table does not name is not an error: spec 8.4 bounds it by
``max_runs_per_day`` instead.
"""

from collections.abc import Mapping
from types import MappingProxyType

from auditor.graph.refine.models import RunUsage
from auditor.user_settings import CodexPrice

#: USD per million tokens, from OpenAI's published API pricing, read 2026-09-01. Deliberately
#: small: a model here that is repriced silently over-reports, and one that is absent degrades
#: to the run ceiling, which is the safe direction. Reviewed each release (spec 21).
CODEX_PRICES: Mapping[str, CodexPrice] = MappingProxyType(
    {
        "gpt-5.1-codex": CodexPrice(input=1.25, output=10.0),
        "gpt-5.1-codex-mini": CodexPrice(input=0.25, output=2.0),
        "gpt-5-codex": CodexPrice(input=1.25, output=10.0),
        "gpt-5": CodexPrice(input=1.25, output=10.0),
        "gpt-5-mini": CodexPrice(input=0.25, output=2.0),
    }
)
PER_MILLION = 1_000_000.0


def price_for(model: str, overrides: Mapping[str, CodexPrice]) -> CodexPrice | None:
    """This model's price, the user's own first, or ``None`` when nothing prices it."""
    return overrides.get(model) or CODEX_PRICES.get(model)


def priced(model: str, overrides: Mapping[str, CodexPrice]) -> bool:
    """Whether a day of runs on this model can be bounded in dollars at all (spec 8.4)."""
    return price_for(model, overrides) is not None


def estimate(usage: RunUsage, price: CodexPrice | None) -> RunUsage:
    """``usage`` with the derived dollar cost filled in, always marked estimated.

    An unpriced model keeps ``cost_usd`` at zero and the day is bounded by runs instead, so a
    zero here never widens a dollar ceiling.
    """
    if price is None:
        return usage.model_copy(update={"cost_usd": 0.0, "cost_estimated": True})
    cost = (
        usage.input_tokens * price.input + usage.output_tokens * price.output
    ) / PER_MILLION
    return usage.model_copy(update={"cost_usd": cost, "cost_estimated": True})
