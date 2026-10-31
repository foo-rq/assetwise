from datetime import date, timedelta

import numpy as np
import pytest

from backend.forecast import estimate_moments, fit_forecast, forecast_comparison
from backend.history import AssetHistory, History
from scripts.verify_recommendations import window


def sample_history():
    rng = np.random.default_rng(17)
    returns = rng.normal([.0002, .0005, .0003], [.008, .02, .012], (504, 3))
    prices = np.vstack((np.ones(3), np.cumprod(1 + returns, axis=0)))
    return History(source="historical", dataset_version="test", notice="Fixture",
                   value_basis="Fixture", time_basis="Fixture", sessions=list(range(505)),
                   dates=[str(date(2015, 1, 1) + timedelta(days=i)) for i in range(505)],
                   assets=[AssetHistory(symbol=s, name=s, index_values=prices[:, i].tolist())
                           for i, s in enumerate("ABC")])


def test_covariance_shrinkage_matches_direct_outer_product_calculation():
    history = sample_history()
    model = fit_forecast(history)
    prices = np.array([a.index_values for a in history.assets]).T
    returns = prices[1:] / prices[:-1] - 1
    centered = returns - returns.mean(axis=0)
    sample = np.cov(returns, rowvar=False, bias=True)
    target = np.eye(3) * np.trace(sample) / 3
    noise = sum(np.square(np.outer(row, row) - sample).sum() for row in centered) / len(returns) ** 2
    intensity = min(1, noise / np.square(sample - target).sum())
    np.testing.assert_allclose(model["covariance"], (1 - intensity) * sample + intensity * target)
    assert np.linalg.eigvalsh(model["covariance"]).min() >= 0
    assert 0 <= model["mean_shrinkage"] <= 1
    assert not np.allclose(model["expected"], returns.mean(axis=0))


def test_future_prices_cannot_change_a_cutoff_forecast():
    history = sample_history()
    before = fit_forecast(window(history, 0, 300))
    for asset in history.assets:
        asset.index_values[300:] = [1000000] * 205
    after = fit_forecast(window(history, 0, 300))
    np.testing.assert_array_equal(before["expected"], after["expected"])
    np.testing.assert_array_equal(before["covariance"], after["covariance"])
    assert after["end"] == history.dates[299]


def test_forecast_units_cash_and_paired_uncertainty():
    model = fit_forecast(sample_history())
    full = forecast_comparison(model, {}, {"A": 100})
    half = forecast_comparison(model, {}, {"A": 50})
    assert full.current.expected_annual_return == 0
    assert full.current.estimated_annual_volatility == 0
    assert half.proposed.expected_annual_return == pytest.approx(full.proposed.expected_annual_return / 2)
    assert half.proposed.estimated_annual_volatility == pytest.approx(full.proposed.estimated_annual_volatility / 2)
    same = forecast_comparison(model, {"A": 50}, {"A": 50})
    assert same.return_change_range_90 == (0, 0)
    assert same.return_change_model_range == (0, 0)
    assert half.return_change_model_range[0] == pytest.approx(full.return_change_model_range[0] / 2)
    assert full.return_change_range_90[0] < full.proposed.expected_annual_return < full.return_change_range_90[1]


def test_bad_or_insufficient_training_data_never_produces_a_forecast():
    history = sample_history()
    assert fit_forecast(window(history, 0, 252)) is None
    history.assets[0].index_values[3] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        fit_forecast(history)


def test_constant_prices_do_not_produce_singular_errors_or_fake_risk():
    history = sample_history()
    for asset in history.assets:
        asset.index_values = [100] * len(history.dates)
    result = forecast_comparison(fit_forecast(history), {"A": 100}, {"B": 100})
    assert result.current.estimated_annual_volatility == 0
    assert result.return_change_range_90 == (0, 0)


def test_singular_asset_class_proxies_remain_finite_without_inventing_returns():
    history = sample_history()
    for asset, symbol in zip(history.assets, ("SPY", "IEF", "GLD")):
        asset.symbol = symbol
        asset.index_values = [100] * len(history.dates)
    model = fit_forecast(history)
    assert model["model"] == "asset-classes-next-close-v2:shrinkage"
    np.testing.assert_array_equal(model["return_scenarios"]["asset_factors"], np.zeros(3))
    result = forecast_comparison(model, {"SPY": 100}, {"IEF": 100})
    assert result.proposed.expected_annual_return == 0
    assert result.return_change_model_range == (0, 0)


def test_model_choice_scores_only_completed_future_blocks_inside_training_history():
    history = sample_history()
    history.assets[0].symbol = "SPY"
    for asset in history.assets:
        asset.index_values += asset.index_values[1:] + [asset.index_values[-1]]
    history.dates += [str(date.fromisoformat(history.dates[-1]) + timedelta(days=i)) for i in range(1, 506)]
    history.sessions = list(range(len(history.dates)))
    prices = np.array([a.index_values for a in history.assets]).T
    returns = prices[1:] / prices[:-1] - 1
    errors = {"shrinkage": [], "market_factor": [], "equal_blend": []}
    for cutoff in (504, 756):
        forecasts, _, _, _ = estimate_moments(returns[:cutoff], 0)
        realised = returns[cutoff + 1:cutoff + 253].mean(axis=0) * 252
        for name in errors:
            errors[name].append(np.mean((forecasts[name] * 252 - realised) ** 2))
    expected = {name: np.sqrt(np.mean(values)) for name, values in errors.items()}
    model = fit_forecast(history)
    assert model["validation_windows"] == 2
    assert model["validation_return_rmse"] == pytest.approx(expected)
    assert model["model"] == "chronological-next-close-v3:" + min(expected, key=expected.get)


def test_analysis_and_preview_share_the_same_current_forecast():
    from backend.analytics import AllocationRequest
    from backend.api import analysis
    from backend.comparison import ComparisonRequest, compare_portfolios

    history = sample_history()
    allocation = AllocationRequest(weights={"A": 80})
    report = analysis(allocation, history)
    preview = compare_portfolios(history, ComparisonRequest(current=allocation, proposed={"weights": {"A": 60, "B": 20}}))
    assert report.forecast.current == preview.forecast.current
    assert report.forecast.as_of == preview.forecast.as_of


def test_asset_class_loadings_match_direct_linear_solve_and_completed_validation():
    history = sample_history()
    for asset, symbol in zip(history.assets, ("SPY", "IEF", "OTHER")):
        asset.symbol = symbol
        asset.index_values += asset.index_values[1:] + [asset.index_values[-1]]
    history.dates += [str(date.fromisoformat(history.dates[-1]) + timedelta(days=i)) for i in range(1, 506)]
    history.sessions = list(range(len(history.dates)))
    prices = np.array([a.index_values for a in history.assets]).T
    returns = prices[1:] / prices[:-1] - 1
    factors = [0, 1]
    _, covariance, _, _ = estimate_moments(returns)
    # Solve the factor system directly to check the production pseudoinverse and daily return units
    expected = np.linalg.solve(covariance[np.ix_(factors, factors)], returns.mean(axis=0)[factors])
    expected = covariance[:, factors] @ expected
    model = fit_forecast(history)
    np.testing.assert_allclose(model["return_scenarios"]["asset_factors"], expected)
    np.testing.assert_allclose(expected[factors], returns.mean(axis=0)[factors])
    errors = {name: [] for name in ("shrinkage", "asset_factors", "asset_blend")}
    for stop in (504, 756):
        forecasts, _, _, _ = estimate_moments(returns[:stop], factors=factors)
        actual = returns[stop + 1:stop + 253].mean(axis=0) * 252
        for name in errors:
            errors[name].append(np.mean((forecasts[name] * 252 - actual) ** 2))
    scores = {name: np.sqrt(np.mean(values)) for name, values in errors.items()}
    assert model["validation_return_rmse"] == pytest.approx(scores)
    assert model["model"] == "asset-classes-next-close-v2:" + min(scores, key=scores.get)
    before = fit_forecast(window(history, 0, 800))
    for asset in history.assets:
        asset.index_values[800:] = [1e6] * (len(history.dates) - 800)
    after = fit_forecast(window(history, 0, 800))
    np.testing.assert_array_equal(before["expected"], after["expected"])
    np.testing.assert_array_equal(before["return_scenarios"]["asset_factors"],
                                  after["return_scenarios"]["asset_factors"])


def test_validation_excludes_return_before_next_close_execution():
    history = sample_history()
    for asset in history.assets:
        asset.index_values += asset.index_values[1:254]
    history.dates += [str(date.fromisoformat(history.dates[-1])+timedelta(days=i)) for i in range(1,254)]
    history.sessions = list(range(len(history.dates)))
    before = fit_forecast(history)
    assert before["validation_windows"] == 1
    # Change only the return into the execution close, preserving every subsequent scored return
    for asset in history.assets:
        asset.index_values[505:] = [value*1.5 for value in asset.index_values[505:]]
    after = fit_forecast(history)
    assert after["validation_return_rmse"] == pytest.approx(before["validation_return_rmse"])
