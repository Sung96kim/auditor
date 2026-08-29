"""Spec 8.4's day ceilings as one value the assessment and the loop both read.

The window is a rolling 24 hours ending at a caller supplied ``now``, so a run at 23:59 and a run
at 00:01 are gated identically and a laptop that changed timezone does not change the answer.
"""

from pydantic import BaseModel, ConfigDict

from auditor.graph.refine.models import Spend
from auditor.user_settings import BudgetConfig

#: spec 8.4's per-repo-per-day window, as a rolling span rather than a calendar day
DAY_SECONDS = 86_400.0


def window_start(now: float) -> float:
    """The oldest run a day ceiling counts, given the clock the caller is using."""
    return now - DAY_SECONDS


class BudgetState(BaseModel):
    """How much of this repo's day is left, and whether this runner has been measured here.

    ``priced`` is false for a model with no entry in the price table: spec 8.4 bounds it by
    ``max_runs_per_day`` instead, and every fraction rule then reads remaining runs.
    """

    model_config = ConfigDict(frozen=True)

    spent_usd: float = 0.0
    runs: int = 0
    max_cost_usd_per_day: float = 0.0
    max_runs_per_day: int = 0
    low_budget_fraction: float = 0.0
    priced: bool = True
    evaluated: bool = False

    @property
    def remaining_fraction(self) -> float:
        """The share of the day's ceiling still unspent, never below zero.

        A ceiling of zero leaves nothing: that is a user who set the budget to zero, not one who
        set no budget, and reading it as unlimited is the one wrong direction.
        """
        used, ceiling = (
            (self.spent_usd, self.max_cost_usd_per_day)
            if self.priced
            else (float(self.runs), float(self.max_runs_per_day))
        )
        if ceiling <= 0.0:
            return 0.0
        return max(0.0, (ceiling - used) / ceiling)

    @property
    def low(self) -> bool:
        """Whether spec 8.6's low budget rule applies: strictly under the configured fraction."""
        return self.remaining_fraction < self.low_budget_fraction

    @property
    def exhausted(self) -> bool:
        return self.remaining_fraction <= 0.0


def budget_state(
    spend: Spend, *, config: BudgetConfig, priced: bool = True, evaluated: bool = False
) -> BudgetState:
    """One window's spend against this user's ceilings (spec 8.4).

    ``evaluated`` is ``bool(TierPolicy.measured)`` for the runner and model about to be used, the
    same notion of "measured" the activation gate reads (P6).
    """
    return BudgetState(
        spent_usd=spend.cost_usd,
        runs=spend.runs,
        max_cost_usd_per_day=config.max_cost_usd_per_day,
        max_runs_per_day=config.max_runs_per_day,
        low_budget_fraction=config.low_budget_fraction,
        priced=priced,
        evaluated=evaluated,
    )
