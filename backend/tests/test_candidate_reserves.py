"""Check complete asset/cash choices against the same loss budget"""

from copy import deepcopy
from math import log1p, sqrt
from statistics import NormalDist
from unittest.mock import patch

import numpy as np
import pytest

import backend.planning as planning
from backend.forecast import fit_forecast
from backend.tests.test_forecast import sample_history
from backend.tests.test_profile_candidates import profile


def previous_choices(history, request):
    # Disable only the added mixtures to retain the earlier search as an exact control
    with patch.object(planning, "budgeted_choices", return_value=[]):
        return planning.candidates(history, request)


@pytest.fixture
def setup(monkeypatch):
    history = sample_history()
    model = fit_forecast(history)
    # Positive drift makes the path bound binding, giving an independent closed-form cash fraction
    model["expected"] = np.array([.4, .6, .3]) / 252
    model["covariance"] = np.diag([.2 ** 2, .1 ** 2, .15 ** 2]) / 252
    model["return_scenarios"] = {"fixture": model["expected"]}
    monkeypatch.setattr(planning, "fit_forecast", lambda _: model)
    request = planning.PlanningRequest(current={"weights": {"A": 100}}, target_percent=30,
        max_position_percent=100, cash_percent=0,
        profile=profile("growth", horizon_years=1, max_loss_percent=20))
    return history, model, request


def test_repaired_mix_reaches_independently_calculated_bound_and_improves_growth(setup):
    history, model, request = setup
    before = previous_choices(history, request)
    after = planning.candidates(history, request)
    mixture = next(item for item in after.proposals if item.name == "B")
    # With positive lower drift, the bound is 1 - exp(-z * volatility * sqrt(years))
    sigma = sqrt(.7 ** 2 * .2 ** 2 + .3 ** 2 * .1 ** 2)
    quantile = NormalDist().inv_cdf(1 - .025 / 4)
    scale = -log1p(-.2) / (quantile * sigma)
    assert mixture.weights == pytest.approx({"A": 70 * scale, "B": 30 * scale}, abs=1e-5)
    assert mixture.comparison.forecast.proposed.drawdown_screen == pytest.approx(.2, abs=1e-7)
    assert after.proposals[0].rank_score < before.proposals[0].rank_score
    assert after.proposals[0].comparison.forecast.proposed.projected_growth > (
        before.proposals[0].comparison.forecast.proposed.projected_growth)


def test_all_previous_feasible_choices_are_retained_exactly(setup):
    history, model, request = setup
    before = previous_choices(history, request)
    after = planning.candidates(history, request)
    assert before.proposals
    payloads = [item.model_dump() for item in after.proposals]
    assert all(item.model_dump() in payloads for item in before.proposals)
    # The original 30% mix does not fit this loss budget but its cash-scaled copy can qualify
    assert "B" not in {item.name for item in before.proposals}
    assert "B" in {item.name for item in after.proposals}


@pytest.mark.parametrize("limit", [None, 100])
def test_no_limit_and_nonbinding_limit_leave_complete_response_unchanged(setup, limit):
    history, model, request = setup
    request.profile.max_loss_percent = limit
    assert planning.candidates(history, request).model_dump() == previous_choices(history, request).model_dump()


def test_zero_loss_does_not_name_a_zero_weight_asset_addition(setup):
    history, model, request = setup
    request.profile.max_loss_percent = 0
    proposals = planning.candidates(history, request).proposals
    assert len(proposals) == 1
    assert proposals[0].name == "Cash reserve"
    assert proposals[0].weights == {"A": 0}


def test_preservation_cash_floor_and_position_cap_apply_to_added_mixes(setup):
    history, model, request = setup
    request.current.weights = {"A": 60, "C": 10}
    request.profile.goal = "preserve"
    request.profile.max_loss_percent = 10
    request.cash_percent = 40
    request.max_position_percent = 25
    proposals = planning.candidates(history, request).proposals
    assert proposals
    assert any(item.name != "Cash reserve" for item in proposals)
    for item in proposals:
        assert max(item.weights.values()) <= 25
        assert item.comparison.proposed.cash_weight_percent >= 40
        assert item.comparison.changes.maximum_drawdown <= 1e-12
        assert item.comparison.forecast.proposed.drawdown_screen <= .1 + 1e-12


def test_search_does_not_mutate_request_history_or_fitted_model(setup):
    history, model, request = setup
    before_history = history.model_dump()
    before_request = request.model_dump()
    before_model = deepcopy(model)
    assert planning.candidates(history, request).proposals
    assert history.model_dump() == before_history
    assert request.model_dump() == before_request
    for key in ("expected", "covariance"):
        np.testing.assert_array_equal(model[key], before_model[key])
    np.testing.assert_array_equal(model["return_scenarios"]["fixture"],
                                  before_model["return_scenarios"]["fixture"])
