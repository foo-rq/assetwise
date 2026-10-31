from math import expm1, sqrt
from statistics import NormalDist

import numpy as np
import pytest

import backend.planning as planning
from backend.forecast import fit_forecast
from backend.historical import build_historical_history
from backend.planning import PlanningRequest, candidates
from backend.profile import InvestorProfile
from backend.tests.test_forecast import sample_history


def profile(goal="learn", **overrides):
    return dict(experience="new", age="private", goal=goal, horizon="long",
                horizon_years=9,
                tolerance="high", capacity="spare", existing="no") | overrides


def test_growth_suggestions_preserve_compounded_growth_and_reduce_estimated_risk():
    history = build_historical_history()
    result = candidates(history, PlanningRequest(current={"weights": {"AAPL": 100}},
                        max_position_percent=100, cash_percent=0, profile=profile("growth")))
    assert result.proposals
    assert result.evaluated == len(history.assets)
    assert "compounded growth" in result.criteria
    for proposal in result.proposals:
        forecast = proposal.comparison.forecast
        assert forecast.horizon_years == 9
        assert forecast.proposed.approximate_annual_log_growth >= forecast.current.approximate_annual_log_growth - 1e-12
        assert forecast.proposed.estimated_annual_volatility <= forecast.current.estimated_annual_volatility - .001


def test_lower_arithmetic_return_can_improve_compounded_growth(monkeypatch):
    history = sample_history()
    model = fit_forecast(history)
    # The lower-return asset cuts enough variance to improve the portfolio's compounded estimate
    model["expected"] = np.array([.10, .08, .08]) / 252
    model["covariance"] = np.diag([.40 ** 2, .10 ** 2, .12 ** 2]) / 252
    model["return_scenarios"] = {"fixture": model["expected"]}
    monkeypatch.setattr(planning, "fit_forecast", lambda _: model)
    result = candidates(history, PlanningRequest(current={"weights": {"A": 100}},
                        max_position_percent=100, cash_percent=0, profile=profile("growth")))
    proposal = next(item for item in result.proposals if item.name == "B")
    before, after = proposal.comparison.forecast.current, proposal.comparison.forecast.proposed
    assert after.expected_annual_return == pytest.approx(.85 * .10 + .15 * .08)
    assert after.expected_annual_return < before.expected_annual_return
    expected_variance = .85 ** 2 * .40 ** 2 + .15 ** 2 * .10 ** 2
    expected_growth = .097 - expected_variance / 2
    assert after.approximate_annual_log_growth == pytest.approx(expected_growth)
    assert expected_growth > .10 - .40 ** 2 / 2
    assert proposal.rank_score == pytest.approx(-9 * (expected_growth - .02))


def test_growth_prices_estimator_disagreement_without_disabling_changes(monkeypatch):
    history = sample_history()
    model = fit_forecast(history)
    optimistic = np.array([.0002, .001, .001])
    model["expected"] = optimistic
    model["return_scenarios"] = {"first": optimistic}
    monkeypatch.setattr(planning, "fit_forecast", lambda _: model)
    request = PlanningRequest(current={"weights": {"A": 100}}, target_percent=15,
                              max_position_percent=100, cash_percent=0, profile=profile("growth"))
    initial = {p.name: p for p in candidates(history, request).proposals}
    assert "B" in initial
    model["return_scenarios"]["second"] = np.array([.0002, -.001, .001])
    updated = {p.name: p for p in candidates(history, request).proposals}
    assert updated.keys() == initial.keys()
    assert updated["B"].rank_score > initial["B"].rank_score
    # A positive score is valid when another estimator expects a compounded-growth reduction
    assert updated["B"].rank_score > 0
    assert updated["C"].rank_score == initial["C"].rank_score
    assert updated["B"].comparison.forecast.return_change_model_range[0] < 0
    # Learning comparisons may show return trade-offs rather than enforce the growth constraint
    request.profile.goal = "learn"
    assert "B" in {p.name for p in candidates(history, request).proposals}


def test_protection_does_not_spend_existing_cash_or_increase_drawdown():
    result = candidates(build_historical_history(), PlanningRequest(
        current={"weights": {"AAPL": 80}}, max_position_percent=100, cash_percent=0,
        profile=profile("preserve")))
    assert result.proposals
    for proposal in result.proposals:
        assert proposal.comparison.proposed.cash_weight_percent >= 20
        assert proposal.comparison.changes.maximum_drawdown <= 1e-12


def test_cash_protection_funds_targets_only_from_invested_holdings():
    from backend.planning import funded_weights
    assert funded_weights({"AAPL": 80}, "SPY", 30, preserve_cash=True) == {"AAPL": 50, "SPY": 30}
    with pytest.raises(ValueError):
        funded_weights({"AAPL": 10}, "SPY", 30, preserve_cash=True)


def test_short_horizon_keeps_the_growth_goal_and_income_limit_is_explicit():
    request = PlanningRequest(current={"weights": {"AAPL": 100}}, max_position_percent=100,
                              cash_percent=0, profile=profile("growth", horizon="short", horizon_years=1))
    result = candidates(build_historical_history(), request)
    assert "compounded growth" in result.criteria
    assert result.proposals
    assert sum(result.proposals[0].weights.values()) > 0
    for proposal in result.proposals:
        forecast = proposal.comparison.forecast
        assert forecast.horizon_years == 1
        assert forecast.proposed.approximate_annual_log_growth >= forecast.current.approximate_annual_log_growth - 1e-12
    request.profile.goal = "income"
    assert "income payments are not forecast" in candidates(build_historical_history(), request).criteria


@pytest.mark.parametrize("years", [.25, 3])
def test_explicit_preservation_protects_cash_over_short_periods(years):
    result = candidates(sample_history(), PlanningRequest(current={"weights": {"A": 80}},
                        max_position_percent=100, cash_percent=0,
                        profile=profile("preserve", horizon_years=years)))
    assert result.proposals
    assert "past losses" in result.criteria
    for proposal in result.proposals:
        assert proposal.comparison.forecast.horizon_years == years
        assert proposal.comparison.proposed.cash_weight_percent >= 20
        assert proposal.comparison.changes.maximum_drawdown <= 1e-12


@pytest.mark.parametrize("goal", ["growth", "preserve"])
@pytest.mark.parametrize("capacity", ["spare", "essential"])
def test_nonbinding_loss_budget_does_not_change_the_stated_objective(goal, capacity):
    history = build_historical_history()
    request = PlanningRequest(current={"weights": {"AAPL": 100}}, target_percent=30,
                              max_position_percent=100, cash_percent=0,
                              profile=profile(goal, capacity=capacity))
    without_budget = candidates(history, request).proposals
    request.profile.max_loss_percent = 100
    with_budget = candidates(history, request).proposals
    assert without_budget
    assert [(item.weights, item.rank_score) for item in with_budget] == [
        (item.weights, item.rank_score) for item in without_budget]
    if goal == "preserve" or capacity == "essential":
        # A numeric budget constrains the choices without quietly switching protection to growth
        for proposal in with_budget:
            assert proposal.rank_score == proposal.comparison.forecast.proposed.drawdown_screen


@pytest.mark.parametrize("goal", ["growth", "preserve", "income"])
def test_saved_profile_without_exact_period_requests_one_before_recommending(goal):
    legacy = profile(goal)
    del legacy["horizon_years"]
    settings = InvestorProfile(**legacy)
    assert settings.horizon_years is None
    assert settings.max_loss_percent is None
    result = candidates(sample_history(), PlanningRequest(current={"weights": {"A": 100}},
                        max_position_percent=100, cash_percent=0, profile=settings))
    assert result.proposals == []
    assert result.evaluated == 0
    assert "investment period" in result.criteria


def test_learning_without_a_period_keeps_annual_comparisons_without_inventing_a_projection():
    result = candidates(sample_history(), PlanningRequest(current={"weights": {"A": 100}},
                        max_position_percent=100, cash_percent=0,
                        profile=profile(horizon="unsure", horizon_years=None)))
    assert result.proposals
    for proposal in result.proposals:
        forecast = proposal.comparison.forecast
        assert forecast.horizon_years is None
        assert forecast.current.projected_growth is None
        assert forecast.proposed.projected_growth_range is None
        assert forecast.proposed.drawdown_screen is None
        assert forecast.projected_relative_wealth_range is None


def test_a_loss_budget_needs_an_exact_period_even_for_learning():
    result = candidates(sample_history(), PlanningRequest(current={"weights": {"A": 100}},
                        max_position_percent=100, cash_percent=0,
                        profile=profile(horizon_years=None, max_loss_percent=20)))
    assert result.proposals == []
    assert result.evaluated == 0
    assert "investment period" in result.criteria


def test_exact_period_and_loss_limit_supersede_old_categories():
    result = candidates(sample_history(), PlanningRequest(current={"weights": {"A": 100}},
                        max_position_percent=100, cash_percent=0,
                        profile=profile("growth", horizon="short", horizon_years=9,
                                        tolerance="low", max_loss_percent=100)))
    assert "compounded growth" in result.criteria
    assert "retain cash" not in result.criteria


@pytest.mark.parametrize("annual_mean,loss_limit,years,covariance_scale", [
    (1, .12, .25, .4), (.02, .89, 9, 1)])
def test_explicit_loss_budget_filters_observed_and_modelled_losses_independently(
        monkeypatch, annual_mean, loss_limit, years, covariance_scale):
    history = sample_history()
    model = fit_forecast(history)
    model["expected"] = np.full(3, annual_mean / 252)
    model["covariance"] *= covariance_scale
    model["return_scenarios"] = {"fixture": model["expected"]}
    monkeypatch.setattr(planning, "fit_forecast", lambda _: model)
    request = PlanningRequest(current={"weights": {"A": 100}}, max_position_percent=100,
                              cash_percent=0, profile=profile(horizon_years=years))
    unrestricted = candidates(history, request).proposals
    prices = np.array([asset.index_values for asset in history.assets]).T
    returns = prices[1:] / prices[:-1] - 1

    def losses(allocation):
        weights = np.array([allocation.get(symbol, 0) / 100 for symbol in model["symbols"]])
        variance = float(weights @ model["covariance"] @ weights * 252)
        log_growth = float(weights @ model["expected"] * 252) - variance / 2
        # Calculate the adverse endpoint independently, including uncertainty in the fitted mean
        lower = expm1(years * log_growth - NormalDist().inv_cdf(.95) *
                      sqrt(variance * (years + years ** 2 / 2)))
        path = np.r_[1, np.cumprod(1 + returns @ weights)]
        drawdown = np.max(1 - path / np.maximum.accumulate(path))
        # Split the 5% tail budget equally between mean uncertainty and the Brownian path bound
        drift_floor = log_growth - NormalDist().inv_cdf(.975) * sqrt(variance / 2)
        path_bound = -expm1(-(NormalDist().inv_cdf(1 - .025 / 4) * sqrt(variance * years)
                             + max(0, -drift_floor) * years))
        return lower, drawdown, path_bound

    allowed = []
    for proposal in unrestricted:
        lower, drawdown, path_bound = losses(proposal.weights)
        assert proposal.comparison.forecast.proposed.drawdown_screen == pytest.approx(path_bound, abs=1e-12)
        if annual_mean == 1:
            assert lower > 0
            assert path_bound < loss_limit
        else:
            assert drawdown < loss_limit
            assert lower >= -loss_limit
        if lower >= -loss_limit and max(drawdown, path_bound) <= loss_limit:
            allowed.append(tuple(sorted(proposal.weights.items())))
    assert 0 < len(allowed) < len(unrestricted)
    request.profile.max_loss_percent = loss_limit * 100
    restricted = candidates(history, request).proposals
    original_choices = {tuple(sorted(item.weights.items())) for item in unrestricted}
    retained = {tuple(sorted(item.weights.items())) for item in restricted} & original_choices
    assert retained == set(allowed)
    for proposal in restricted:
        lower, drawdown, path_bound = losses(proposal.weights)
        assert lower >= -loss_limit - 1e-12
        assert drawdown <= loss_limit + 1e-12
        assert path_bound <= loss_limit + 1e-12
    # The extra cash choice should sit at the budget boundary, rather than default to full liquidation
    adaptive = [item for item in restricted if item.name == "Cash reserve"
                and 0 < item.weights["A"] < 100 and item.weights["A"] != 85]
    assert len(adaptive) == 1
    lower, drawdown, path_bound = losses({"A": adaptive[0].weights["A"] + .0001})
    assert lower < -loss_limit or max(drawdown, path_bound) > loss_limit


def test_zero_loss_budget_can_offer_full_cash_with_clear_zero_forecasts():
    result = candidates(sample_history(), PlanningRequest(current={"weights": {"A": 80}},
                        max_position_percent=100, cash_percent=0,
                        profile=profile("preserve", max_loss_percent=0)))
    assert len(result.proposals) == 1
    proposal = result.proposals[0]
    assert proposal.name == "Cash reserve"
    assert proposal.weights == {"A": 0}
    assert proposal.comparison.proposed.cash_weight_percent == 100
    assert proposal.comparison.proposed.maximum_drawdown == 0
    assert proposal.comparison.forecast.proposed.expected_annual_return == 0
    assert proposal.comparison.forecast.proposed.estimated_annual_volatility == 0
    assert proposal.comparison.forecast.proposed.projected_growth == 0
    assert proposal.comparison.forecast.proposed.projected_growth_range == (0, 0)
    assert proposal.comparison.forecast.proposed.drawdown_screen == 0


def test_breached_loss_limit_takes_priority_over_the_old_growth_estimate(monkeypatch):
    history = sample_history()
    model = fit_forecast(history)
    model["expected"] = np.array([.10, .08, .08]) / 252
    model["covariance"] = np.diag([.40 ** 2, .10 ** 2, .12 ** 2]) / 252
    model["return_scenarios"] = {"fixture": model["expected"]}
    monkeypatch.setattr(planning, "fit_forecast", lambda _: model)
    result = candidates(history, PlanningRequest(current={"weights": {"A": 100}},
                        max_position_percent=100, cash_percent=0,
                        profile=profile("growth", max_loss_percent=2)))
    assert result.proposals
    assert "within your limit" in result.criteria
    assert result.minimum_reduction_percentage_points == 0
    for proposal in result.proposals:
        forecast = proposal.comparison.forecast
        assert forecast.proposed.approximate_annual_log_growth < forecast.current.approximate_annual_log_growth
        assert forecast.proposed.projected_growth_range[0] >= -.02 - 1e-12
        assert forecast.proposed.drawdown_screen <= .02 + 1e-12
        assert proposal.comparison.proposed.maximum_drawdown <= .02 + 1e-12


@pytest.mark.parametrize("target,cap", [(15, 85), (30, 70), (30, 69.99)])
def test_cash_choices_honour_exact_cash_and_position_limits(target, cap):
    result = candidates(sample_history(), PlanningRequest(current={"weights": {"A": 100}},
                        target_percent=target, max_position_percent=cap, cash_percent=target,
                        profile=profile()))
    if cap < 100 - target:
        assert result.proposals == []
    else:
        assert len(result.proposals) == 1
        proposal = result.proposals[0]
        assert proposal.name == "Cash reserve"
        assert proposal.weights == {"A": 100 - target}
        assert proposal.comparison.proposed.cash_weight_percent == target


def test_recommendation_generation_does_not_change_caller_state():
    history = sample_history()
    request = PlanningRequest(current={"weights": {"A": 80, "B": 20}},
                              max_position_percent=100, cash_percent=0, profile=profile("preserve"))
    original_history, original_request = history.model_dump(), request.model_dump()
    assert candidates(history, request).proposals
    assert history.model_dump() == original_history
    assert request.model_dump() == original_request
