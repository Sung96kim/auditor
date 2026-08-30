"""Spec 8.4's day ceilings: the fraction rule, the low bar, and the rolling window."""

import pytest

from auditor.graph.refine.models import RunnerKind, Spend
from auditor.observer.budget import (
    DAY_SECONDS,
    budget_state,
    priced_runner,
    window_start,
)
from auditor.user_settings import BudgetConfig, CodexPrice, RunnerConfig

_PRICE = CodexPrice(input=1.0, output=2.0)


def _config(**over) -> BudgetConfig:
    return BudgetConfig(**{"max_cost_usd_per_day": 2.0, "max_runs_per_day": 40, **over})


@pytest.mark.parametrize(
    ("spend", "priced", "fraction"),
    [
        (Spend(cost_usd=0.0, runs=0), True, 1.0),
        (Spend(cost_usd=1.0, runs=5), True, 0.5),
        (Spend(cost_usd=1.6, runs=5), True, 0.2),
        (Spend(cost_usd=3.0, runs=5), True, 0.0),
        (Spend(cost_usd=3.0, runs=10), False, 0.75),
        (Spend(cost_usd=0.0, runs=40), False, 0.0),
    ],
)
def test_remaining_fraction_reads_dollars_when_priced_and_runs_when_not(
    spend, priced, fraction
):
    """Spec 8.4: an unpriced model is bounded by runs, and every fraction rule follows it."""
    state = budget_state(spend, config=_config(), priced=priced)
    assert state.remaining_fraction == pytest.approx(fraction)


@pytest.mark.parametrize(
    ("fraction", "low"), [(0.26, False), (0.25, False), (0.24, True), (0.0, True)]
)
def test_low_is_strictly_below_the_configured_fraction(fraction, low):
    assert (
        budget_state(Spend(cost_usd=2.0 * (1.0 - fraction)), config=_config()).low
        is low
    )


def test_a_zero_ceiling_leaves_nothing():
    state = budget_state(Spend(), config=_config(max_cost_usd_per_day=0.0))
    assert (state.remaining_fraction, state.low, state.exhausted) == (0.0, True, True)


def test_the_window_is_a_rolling_day_ending_at_the_injected_now():
    """The literal, not the constant: importing the length from the thing under test leaves only
    a sign flip to catch."""
    now = 1_000_000.0
    assert window_start(now) == now - 86_400.0
    assert window_start(now + 3_600.0) - window_start(now) == 3_600.0


def test_the_day_the_window_rolls_is_the_day_retention_counts():
    """Two homes differing in type is how a rolling window and a cutoff drift apart."""
    assert DAY_SECONDS == 86_400.0


@pytest.mark.parametrize(
    ("kind", "codex_model", "prices", "priced"),
    [
        (RunnerKind.CLAUDE, "", {}, True),
        (RunnerKind.FAKE, "", {}, False),
        (RunnerKind.NONE, "", {}, False),
        (RunnerKind.CODEX, "gpt-5", {}, False),
        (RunnerKind.CODEX, "", {"gpt-5": _PRICE}, False),
        (RunnerKind.CODEX, "gpt-5", {"gpt-5": _PRICE}, True),
    ],
    ids=[
        "claude",
        "fake",
        "none",
        "codex-unpriced",
        "codex-other-model",
        "codex-priced",
    ],
)
def test_only_a_runner_that_reports_a_cost_is_bounded_in_dollars(
    kind, codex_model, prices, priced
):
    """M2: `max_runs_per_day` is the ceiling for a run that costs nothing the ledger can see."""
    runner = RunnerConfig(codex_model=codex_model, codex_prices=prices)
    assert priced_runner(kind, runner) is priced
