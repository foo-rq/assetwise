import json
from math import expm1, log, log1p, sqrt
from statistics import NormalDist

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api import AnalysisRequest, analysis
from backend.comparison import ComparisonRequest, compare_portfolios
from backend.forecast import drawdown_screen, fit_forecast, forecast_comparison
from backend.main import app
from backend.profile import InvestorProfile
from backend.tests.test_forecast import sample_history
from scripts.verify_recommendations import window


def profile(**changes):
    return dict(experience="some", goal="growth", horizon="medium", tolerance="moderate",
                capacity="spare", horizon_years=9) | changes


def model(mean=(.08, .06), covariance=((.04, .015), (.015, .01)), count=1260):
    return dict(symbols=["A", "B"], expected=np.array(mean)/252,
                covariance=np.array(covariance)/252, count=count,
                start="2012-01-03", end="2017-01-03", model="fixture",
                validation_windows=2, validation_return_rmse={"fixture": .1},
                return_scenarios={"fixture": np.array(mean)/252},
                mean_shrinkage=0, covariance_shrinkage=0)


def test_deterministic_compounding_and_cash_have_exact_horizon_values():
    # Constant 10% compound growth provides a closed-form check independent of the uncertainty code
    fitted = model(mean=(log(1.1), 0), covariance=((0, 0), (0, 0)))
    result = forecast_comparison(fitted, {}, {"A": 100}, horizon_years=5)
    expected = 1.1**5-1
    assert result.current.projected_growth == 0
    assert result.current.projected_growth_range == (0, 0)
    assert result.current.drawdown_screen == 0
    assert result.proposed.projected_growth == pytest.approx(expected)
    assert result.proposed.projected_growth_range == pytest.approx((expected, expected))
    assert result.proposed.drawdown_screen == 0
    assert result.projected_relative_wealth_range == pytest.approx((expected, expected))


def test_period_changes_compounding_and_uncertainty_without_rescaling_annual_metrics():
    fitted = model()
    short = forecast_comparison(fitted, {}, {"A": 100}, horizon_years=.5)
    long = forecast_comparison(fitted, {}, {"A": 100}, horizon_years=5)
    assert long.proposed.expected_annual_return == short.proposed.expected_annual_return
    assert long.proposed.estimated_annual_volatility == short.proposed.estimated_annual_volatility
    assert long.return_change_range_90 == short.return_change_range_90
    assert log1p(long.proposed.projected_growth) == pytest.approx(10*log1p(short.proposed.projected_growth))
    # With 5 training years, log-return uncertainty uses H + H squared/5, not just square-root time
    half_width = lambda interval: (log1p(interval[1])-log1p(interval[0]))/2
    ratio = half_width(long.proposed.projected_growth_range)/half_width(short.proposed.projected_growth_range)
    assert ratio**2 == pytest.approx((5+25/5)/(.5+.25/5))
    assert "5 years" in long.assumptions
    assert "exclude inflation" in long.assumptions


def test_paired_wealth_uncertainty_cancels_shared_exposure():
    # The 2 tickers are identical claims here, so switching names cannot create relative wealth uncertainty
    fitted = model(mean=(.08, .08), covariance=((.04, .04), (.04, .04)))
    result = forecast_comparison(fitted, {"A": 100}, {"B": 100}, horizon_years=9)
    assert result.current.projected_growth_range[0] < result.current.projected_growth_range[1]
    assert result.projected_relative_wealth_range == (0, 0)
    same = forecast_comparison(fitted, {"A": 60}, {"A": 60}, horizon_years=9)
    assert same.projected_relative_wealth_range == (0, 0)


def test_missing_horizon_preserves_annual_analysis_without_inventing_a_period():
    result = forecast_comparison(model(), {"A": 100}, {"B": 100})
    assert result.horizon_years is None
    assert result.current.projected_growth is None
    assert result.proposed.projected_growth_range is None
    assert result.projected_relative_wealth_range is None
    assert result.projection_notice is None
    assert result.proposed.drawdown_screen is None
    assert result.proposed.expected_annual_return == pytest.approx(.06)


@pytest.mark.parametrize("years", [True, "9", float("nan"), float("inf"), 0, .24, 40.01])
def test_invalid_horizon_is_rejected_at_request_and_direct_calculation_boundaries(years):
    with pytest.raises(ValidationError):
        InvestorProfile(**profile(horizon_years=years))
    with pytest.raises(ValueError, match="Investment period"):
        forecast_comparison(model(), {}, {"A": 100}, horizon_years=years)


@pytest.mark.parametrize("limit", [True, "20", float("nan"), float("inf"), -1, 100.01])
def test_stated_loss_limit_rejects_invalid_values(limit):
    with pytest.raises(ValidationError):
        InvestorProfile(**profile(max_loss_percent=limit))


def test_profile_compatibility_and_valid_extreme_preferences():
    old = profile()
    del old["horizon_years"]
    restored = InvestorProfile(**old)
    assert restored.age == "private" and restored.existing == "no"
    assert restored.horizon_years is None and restored.max_loss_percent is None
    for years, limit in ((.25, 0), (40, 100)):
        explicit = InvestorProfile(**profile(horizon_years=years, max_loss_percent=limit))
        assert explicit.horizon_years == years and explicit.max_loss_percent == limit


def test_projection_overflow_is_explicitly_unavailable_without_clipping_or_infinity():
    result = forecast_comparison(model(mean=(30, .06)), {}, {"A": 100}, horizon_years=40)
    assert result.current.projected_growth == 0
    assert result.proposed.projected_growth is None
    assert result.proposed.projected_growth_range is None
    assert result.projected_relative_wealth_range is None
    assert "unavailable" in result.projection_notice
    assert result.proposed.expected_annual_return == 30
    json.dumps(result.model_dump(), allow_nan=False)


def test_analysis_and_preview_use_the_same_explicit_investment_period():
    history = sample_history()
    request = AnalysisRequest(weights={"A": 80}, profile=profile())
    report = analysis(request, history)
    preview = compare_portfolios(history, ComparisonRequest(current={"weights": request.weights},
        proposed={"weights": {"A": 60, "B": 20}}, profile=profile()))
    assert report.forecast.horizon_years == preview.forecast.horizon_years == 9
    assert report.forecast.current == preview.forecast.current
    assert request.weights == {"A": 80}


def test_future_observations_cannot_change_a_period_projection():
    history = sample_history()
    before = forecast_comparison(fit_forecast(window(history, 0, 300)), {}, {"A": 100}, horizon_years=9)
    for asset in history.assets:
        asset.index_values[300:] = [1e6]*205
    after = forecast_comparison(fit_forecast(window(history, 0, 300)), {}, {"A": 100}, horizon_years=9)
    assert before == after


def test_api_accepts_profile_context_and_returns_matching_period_projections():
    with TestClient(app) as client:
        report = client.post("/api/analysis", json={"weights": {"AAPL": 80}, "profile": profile()})
        preview = client.post("/api/comparison", json={"current": {"weights": {"AAPL": 80}},
            "proposed": {"weights": {"AAPL": 60, "SPY": 20}}, "profile": profile()})
    assert report.status_code == preview.status_code == 200
    left, right = report.json()["forecast"], preview.json()["forecast"]
    assert left["horizon_years"] == right["horizon_years"] == 9
    assert left["current"] == right["current"]
    assert right["projected_relative_wealth_range"] is not None


def test_drawdown_screen_uses_a_path_bound_not_a_terminal_loss_quantile():
    # With positive drift the terminal lower end can be profitable while the path can still fall from a peak
    result = forecast_comparison(model(mean=(.5, .06)), {}, {"A": 100}, horizon_years=5)
    assert result.proposed.projected_growth_range[0] > 0
    path_quantile = NormalDist().inv_cdf(1 - .025 / 4)
    expected_loss = -expm1(-path_quantile * .2 * sqrt(5))
    assert result.proposed.drawdown_screen == pytest.approx(expected_loss)
    assert result.proposed.drawdown_screen > .6


def test_drawdown_bound_includes_uncertain_negative_drift_and_exact_deterministic_loss():
    normal = NormalDist()
    lower_drift = .01 - normal.inv_cdf(.975) * .15 / sqrt(4)
    log_loss = normal.inv_cdf(1 - .025 / 4) * .15 * sqrt(3) - lower_drift * 3
    assert drawdown_screen(.01, .15, 3, 4) == pytest.approx(-expm1(-log_loss))
    assert drawdown_screen(log(.9), 0, 5, 4) == pytest.approx(1 - .9 ** 5)


def test_path_bound_increases_with_period_and_risk_without_a_positive_drift_safety_credit():
    assert 0 < drawdown_screen(.05, .1, 1, 5) < drawdown_screen(.05, .1, 9, 5) < 1
    assert drawdown_screen(.05, .1, 9, 5) < drawdown_screen(.05, .3, 9, 5) < 1
    assert drawdown_screen(1, .1, 9, 5) == drawdown_screen(10, .1, 9, 5)
    assert drawdown_screen(-1000, 1000, 40, 1) == 1


def test_cash_scaling_cannot_increase_the_modelled_drawdown_screen():
    # Cash scaling changes both variance drag and drift uncertainty, not only volatility
    for mean in (-.10, .02, .20):
        losses = []
        for invested in (0, .1, .3, .7, 1):
            volatility = invested * .3
            growth = invested * mean - volatility ** 2 / 2
            losses.append(drawdown_screen(growth, volatility, 9, 5))
        assert losses == sorted(losses)
