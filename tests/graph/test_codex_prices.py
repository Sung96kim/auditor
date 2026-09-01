"""The shipped price table, and the estimate a Codex run's tokens buy."""

import pytest

from auditor.graph.refine.models import RunUsage
from auditor.graph.refine.prices import CODEX_PRICES, estimate, price_for, priced
from auditor.user_settings import CodexPrice

OVERRIDE = CodexPrice(input=99.0, output=99.0)


def test_the_shipped_table_names_the_models_the_readme_documents():
    assert set(CODEX_PRICES) == {
        "gpt-5.1-codex",
        "gpt-5.1-codex-mini",
        "gpt-5-codex",
        "gpt-5",
        "gpt-5-mini",
    }


@pytest.mark.parametrize(
    ("model", "overrides", "found"),
    [
        ("gpt-5.1-codex", {}, True),
        ("gpt-5.1-codex", {"gpt-5.1-codex": OVERRIDE}, True),
        ("o9-imaginary", {}, False),
        ("o9-imaginary", {"o9-imaginary": OVERRIDE}, True),
    ],
)
def test_a_user_price_beats_the_shipped_one_and_an_unknown_model_has_none(
    model, overrides, found
):
    assert priced(model, overrides) is found
    assert (price_for(model, overrides) is not None) is found


def test_the_user_s_own_price_is_the_one_that_is_used():
    assert price_for("gpt-5", {"gpt-5": OVERRIDE}) is OVERRIDE


def test_the_estimate_is_tokens_times_price_and_says_it_is_an_estimate():
    usage = RunUsage(input_tokens=1_000_000, output_tokens=100_000, num_turns=3)
    out = estimate(usage, price_for("gpt-5", {}))
    assert out.cost_usd == pytest.approx(1.25 + 1.0)
    assert out.cost_estimated is True
    assert out.num_turns == 3


def test_an_unpriced_model_records_zero_dollars_and_stays_estimated():
    """Spec 8.4: that run is bounded by `max_runs_per_day`, so a zero cannot widen the day."""
    out = estimate(RunUsage(input_tokens=5_000, output_tokens=5_000), None)
    assert out.cost_usd == 0.0
    assert out.cost_estimated is True
