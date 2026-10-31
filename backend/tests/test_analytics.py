from math import sqrt

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.analytics import AllocationRequest, analyse_portfolio
from backend.main import app
from backend.tests.simulated_history import SimulatedAsset, SimulatedHistory


def history_for(**paths):
    # Small invented paths let us verify the answers by hand without the random generator
    return SimulatedHistory(
        sessions=[0, 1, 2],
        assets=[
            SimulatedAsset(symbol=symbol, name=symbol, index_values=values)
            for symbol, values in paths.items()
        ],
    )


def test_single_asset_matches_hand_calculation():
    # Returns of +10% then -10% compound to -1%, despite an arithmetic mean of 0
    result = analyse_portfolio(
        history_for(A=[100, 110, 99]), AllocationRequest(weights={"A": 100})
    )
    assert result.session_returns == pytest.approx([0.1, -0.1])
    assert result.cumulative_return == pytest.approx(-0.01)
    assert result.annualised_arithmetic_return == pytest.approx(0, abs=1e-12)
    assert result.annualised_volatility == pytest.approx(sqrt(0.02 * 252))


def test_opposite_assets_cancel_risk():
    # Opposite returns cancel at equal weights, catching a weighted-volatility shortcut
    result = analyse_portfolio(
        history_for(A=[100, 110, 99], B=[100, 90, 99]),
        AllocationRequest(weights={"A": 50, "B": 50}),
    )
    assert result.session_returns == pytest.approx([0, 0], abs=1e-12)
    assert result.annualised_volatility == pytest.approx(0, abs=1e-12)


def test_identical_assets_do_not_reduce_risk():
    history = history_for(A=[100, 110, 99], B=[100, 110, 99])
    single = analyse_portfolio(history, AllocationRequest(weights={"A": 100}))
    split = analyse_portfolio(history, AllocationRequest(weights={"A": 50, "B": 50}))

    # Adding another identifier alone cannot manufacture diversification
    assert split.annualised_volatility == single.annualised_volatility


def test_cash_reduces_exposure_without_renormalising_weights():
    history = history_for(A=[100, 110, 99])
    half = analyse_portfolio(history, AllocationRequest(weights={"A": 50}))
    cash = analyse_portfolio(history, AllocationRequest(weights={}))
    assert half.cash_weight_percent == 50
    assert half.session_returns == pytest.approx([0.05, -0.05])
    assert half.cumulative_return == pytest.approx(-0.0025)
    assert half.annualised_volatility == pytest.approx(sqrt(0.02 * 252) / 2)

    # Cash-only is a valid portfolio with zero return and volatility
    assert cash.cash_weight_percent == 100
    assert cash.cumulative_return == cash.annualised_volatility == 0


@pytest.mark.parametrize("weights", [
    {"A": -1}, {"A": 101}, {"A": 60, "B": 41},
    {"A": float("nan")}, {"A": float("inf")}, {"A": True}, {"A": "50"},
])
def test_invalid_weights_are_rejected(weights):
    with pytest.raises(ValidationError):
        AllocationRequest(weights=weights)


@pytest.mark.parametrize("path", [[100, 110], [100, 0, 99], [100, -1, 99], [100, float("nan"), 99]])
def test_invalid_held_history_is_rejected(path):
    # Never replace missing or invalid values with invented observations
    with pytest.raises(ValueError):
        analyse_portfolio(history_for(A=path), AllocationRequest(weights={"A": 100}))


def test_missing_sessions_and_duplicate_assets_are_rejected():
    history = history_for(A=[100, 110, 99])
    history.sessions = [0, 2, 3]
    with pytest.raises(ValueError, match="consecutive"):
        analyse_portfolio(history, AllocationRequest(weights={"A": 100}))
    history.sessions = [0, 1]
    with pytest.raises(ValueError, match="At least 3"):
        analyse_portfolio(history, AllocationRequest(weights={"A": 100}))
    history.sessions = [0, 1, 2]
    history.assets.append(history.assets[0])
    with pytest.raises(ValueError, match="unique"):
        analyse_portfolio(history, AllocationRequest(weights={"A": 100}))


def test_api_preserves_labels_and_rejects_unknown_assets():
    with TestClient(app) as client:
        response = client.post("/api/analysis", json={"weights": {"AAPL": 60}})
        unknown = client.post("/api/analysis", json={"weights": {"UNKNOWN": 100}})
        excessive = client.post("/api/analysis", json={"weights": {"DEMO-EQ1": 101}})
        typo = client.post("/api/analysis", json={"weight": {}})

    # Check the real HTTP response so provenance survives serialization
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "historical"
    assert body["dataset_version"] == "archive-2017-v2"
    assert body["cash_weight_percent"] == 40
    assert body["return_observations"] == 1225
    assert body["dates"][-1] == "2017-11-10"
    assert unknown.status_code == excessive.status_code == typo.status_code == 422


@pytest.mark.parametrize("values, expected", [
    ([100, 80, 90], [0, 0.2, 0.1]),
    ([100, 120, 90, 120, 132], [0, 0, 0.25, 0, 0]),
    ([100, 110, 120], [0, 0, 0]),
])
def test_drawdowns_include_initial_losses_and_recovery(values, expected):
    # Set the shared sessions explicitly for these longer hand-verifiable paths
    history = history_for(A=values)
    history.sessions = list(range(len(values)))
    result = analyse_portfolio(history, AllocationRequest(weights={"A": 100}))
    assert result.portfolio_index == pytest.approx([value / 100 for value in values])
    assert result.drawdowns == pytest.approx(expected)
    assert result.maximum_drawdown == pytest.approx(max(expected))
    assert result.cumulative_return == pytest.approx(result.portfolio_index[-1] - 1)


def test_cash_path_and_drawdown():
    history = history_for(A=[100, 80, 100])
    half = analyse_portfolio(history, AllocationRequest(weights={"A": 50}))
    cash = analyse_portfolio(history, AllocationRequest(weights={}))

    # Rebalancing at half exposure gives 1, 0.9, 1.0125 rather than a buy-and-hold path
    assert half.portfolio_index == pytest.approx([1, 0.9, 1.0125])
    assert half.maximum_drawdown == pytest.approx(0.1)
    assert cash.portfolio_index == [1, 1, 1]
    assert cash.drawdowns == [0, 0, 0]
    assert cash.asset_correlations == []


@pytest.mark.parametrize("second, expected", [([100, 110, 99], 1), ([100, 90, 99], -1)])
def test_correlations_use_returns_and_ignore_weight_sizes(second, expected):
    result = analyse_portfolio(
        history_for(A=[100, 110, 99], B=second),
        AllocationRequest(weights={"B": 10, "A": 60}),
    )

    # The pair is ordered by identifier and excludes the 30% cash remainder
    assert len(result.asset_correlations) == 1
    pair = result.asset_correlations[0]
    assert (pair.asset_a, pair.asset_b) == ("A", "B")
    assert pair.value == pytest.approx(expected)
    assert pair.unavailable_reason is None


def test_constant_asset_correlation_is_unavailable_and_zero_weights_are_excluded():
    history = history_for(A=[100, 110, 99], B=[100, 100, 100], UNUSED=[])
    result = analyse_portfolio(history, AllocationRequest(weights={"A": 50, "B": 50, "UNUSED": 0}))

    # Missing history for an unheld asset must not prevent analysis of the actual portfolio
    assert len(result.asset_correlations) == 1
    assert result.asset_correlations[0].value is None
    assert "constant returns" in result.asset_correlations[0].unavailable_reason


def test_api_returns_drawdown_path_and_correlation_pairs():
    with TestClient(app) as client:
        response = client.post("/api/analysis", json={"weights": {"AAPL": 50, "SPY": 50}})
    assert response.status_code == 200
    body = response.json()

    # Path and drawdown include session 0, while returns begin with the first change
    assert len(body["portfolio_index"]) == len(body["drawdowns"]) == 1226
    assert body["maximum_drawdown"] == max(body["drawdowns"])
    assert len(body["asset_correlations"]) == 1
