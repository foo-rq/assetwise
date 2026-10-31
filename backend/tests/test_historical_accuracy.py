"""Independent NumPy checks against the actual archive, rather than synthetic paths only"""

from math import expm1, sqrt
from statistics import NormalDist

import numpy as np
import pytest

from backend.analytics import AllocationRequest, analyse_portfolio
from backend.forecast import fit_forecast
from backend.historical import build_historical_history
from backend.planning import PlanningRequest, candidates
from backend.tests.test_profile_candidates import profile


@pytest.fixture(scope="module")
def archive():
    history = build_historical_history()
    prices = np.array([asset.index_values for asset in history.assets]).T
    returns = np.diff(prices, axis=0) / prices[:-1]
    return history, [asset.symbol for asset in history.assets], returns, np.cov(returns, rowvar=False, ddof=1)


def reference(archive, weights):
    history, symbols, returns, covariance = archive
    vector = np.array([weights.get(symbol, 0) / 100 for symbol in symbols])
    daily = returns @ vector
    path = np.r_[1, np.cumprod(1 + daily)]
    excess = daily - np.array(history.risk_free_returns)
    # Covariance gives a separate route to portfolio volatility, including cross-asset movement
    volatility = np.sqrt(vector @ covariance @ vector * 252)
    return dict(session_returns=daily, portfolio_index=path,
                cumulative_return=path[-1] - 1,
                annualised_arithmetic_return=daily.mean() * 252,
                annualised_volatility=volatility,
                maximum_drawdown=np.max(1 - path / np.maximum.accumulate(path)),
                sharpe_ratio=excess.mean() / excess.std(ddof=1) * np.sqrt(252)
                if volatility > 1e-12 else None)


def test_every_asset_and_mixed_portfolio_matches_independent_calculation(archive):
    history, symbols, returns, covariance = archive
    allocations = [{symbol: 100} for symbol in symbols] + [
        {}, {"AAPL": 50}, {"AAPL": 40, "MSFT": 30, "SPY": 30},
        {"SPY": 60, "IEF": 30}, {"QQQ": 30, "BND": 40, "GLD": 20},
        {"AAPL": 50, "BND": 30.01},
    ]
    for weights in allocations:
        actual = analyse_portfolio(history, AllocationRequest(weights=weights))
        for field, expected in reference(archive, weights).items():
            assert getattr(actual, field) == pytest.approx(expected, abs=1e-10), (weights, field)
        for pair in actual.asset_correlations:
            expected = np.corrcoef(returns[:, symbols.index(pair.asset_a)],
                                   returns[:, symbols.index(pair.asset_b)])[0, 1]
            assert pair.value == pytest.approx(expected, abs=1e-10)


@pytest.mark.parametrize("settings", [profile(), profile("growth"), profile("preserve"),
                                      profile("income"), profile("growth", horizon="short", horizon_years=1),
                                      profile(capacity="essential"), profile(tolerance="low"),
                                      profile(max_loss_percent=20)])
@pytest.mark.parametrize("weights", [{"AAPL": 100}, {"AAPL": 50}, {"SPY": 60, "IEF": 30}])
def test_actual_archive_recommendations_meet_their_claims(archive, settings, weights):
    history, symbols, _, _ = archive
    baseline = reference(archive, weights)
    model = fit_forecast(history)
    loss_limit = settings.get("max_loss_percent")
    protected = settings["goal"] == "preserve" or settings["capacity"] == "essential"
    before = np.array([weights.get(symbol, 0) / 100 for symbol in model["symbols"]])
    for target in (15, 30):
        result = candidates(history, PlanningRequest(current={"weights": weights},
                            target_percent=target, max_position_percent=100,
                            cash_percent=0, profile=settings))
        scores = []
        for proposal in result.proposals:
            expected = reference(archive, proposal.weights)
            if proposal.name in symbols:
                candidate_weight = proposal.weights[proposal.name]
                if loss_limit is None:
                    assert candidate_weight == target
                else:
                    # A binding loss budget can scale the whole funded mix into cash
                    assert 0 < candidate_weight <= target
                    scale = candidate_weight / target
                    pool = sum(weights.values()) if protected else 100
                    funding = (pool - target) / (pool - weights.get(proposal.name, 0))
                    for symbol, weight in weights.items():
                        if symbol != proposal.name:
                            assert proposal.weights[symbol] == pytest.approx(
                                weight * funding * scale, abs=1e-7)
            else:
                # Cash remains the unallocated percentage, including explicit 0% rows after liquidation
                assert proposal.name == "Cash reserve"
                cash = proposal.comparison.proposed.cash_weight_percent
                assert cash > 100 - sum(weights.values())
                if loss_limit is None:
                    assert any(cash == pytest.approx(value, abs=len(weights) * 1e-8)
                               for value in (target, 100))
            assert sum(proposal.weights.values()) <= 100 + 1e-8
            forecast = proposal.comparison.forecast
            assert forecast.horizon_years == settings["horizon_years"]
            after = np.array([proposal.weights.get(symbol, 0) / 100 for symbol in model["symbols"]])
            for metrics, allocation in ((forecast.current, before), (forecast.proposed, after)):
                # Check the path screen directly from covariance and mean, separate from the forecast helper
                variance = float(allocation @ model["covariance"] @ allocation) * 252
                log_growth = float(allocation @ model["expected"]) * 252 - variance / 2
                years = settings["horizon_years"]
                drift_floor = log_growth - NormalDist().inv_cdf(.975) * sqrt(variance / (model["count"] / 252))
                log_loss = NormalDist().inv_cdf(1 - .025 / 4) * sqrt(variance * years) + max(0, -drift_floor) * years
                assert metrics.drawdown_screen == pytest.approx(-expm1(-log_loss), abs=1e-12)
            outside_limit = loss_limit is not None and (
                baseline["maximum_drawdown"] > loss_limit / 100 or
                forecast.current.projected_growth_range[0] < -loss_limit / 100 or
                forecast.current.drawdown_screen > loss_limit / 100)
            reduction = 0 if outside_limit else .001
            assert result.minimum_reduction_percentage_points == reduction * 100
            assert forecast.proposed.estimated_annual_volatility <= forecast.current.estimated_annual_volatility - reduction + 1e-10
            for field in ("annualised_arithmetic_return", "annualised_volatility", "maximum_drawdown"):
                assert getattr(proposal.comparison.proposed, field) == pytest.approx(expected[field], abs=1e-10)
                assert getattr(proposal.comparison.changes, field) == pytest.approx(expected[field] - baseline[field], abs=1e-10)
            if protected:
                assert sum(proposal.weights.values()) <= sum(weights.values()) + 1e-8
                assert expected["maximum_drawdown"] <= baseline["maximum_drawdown"] + 1e-10
            if loss_limit is not None:
                limit = loss_limit / 100
                assert expected["maximum_drawdown"] <= limit + 1e-10
                assert forecast.proposed.projected_growth_range[0] >= -limit - 1e-10
                assert forecast.proposed.drawdown_screen <= limit + 1e-10
            if settings["goal"] == "growth" and not protected and not outside_limit:
                assert forecast.proposed.approximate_annual_log_growth >= forecast.current.approximate_annual_log_growth - 1e-10
            if settings["goal"] == "growth" and not protected:
                # Rank the worst estimator's compounded change over the user's period, not a probability
                variance_change = float(after @ model["covariance"] @ after -
                                        before @ model["covariance"] @ before) * 252
                model_growth_changes = [float((after - before) @ mean * 252) - variance_change / 2
                                        for mean in model["return_scenarios"].values()]
                expected_score = -min(model_growth_changes) * settings["horizon_years"]
                assert proposal.rank_score == pytest.approx(expected_score, abs=1e-10)
            elif protected:
                assert proposal.rank_score == pytest.approx(forecast.proposed.drawdown_screen, abs=1e-10)
            else:
                assert proposal.rank_score == pytest.approx(forecast.proposed.estimated_annual_volatility, abs=1e-10)
            scores.append(proposal.rank_score)
        assert scores == sorted(scores)
