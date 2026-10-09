from math import sqrt

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.analytics import AllocationRequest, analyse_portfolio
from backend.main import app
from backend.simulation import SimulatedAsset, SimulatedHistory


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
        response = client.post("/demo/analysis", json={"weights": {"DEMO-EQ1": 60}})
        unknown = client.post("/demo/analysis", json={"weights": {"AAPL": 100}})
        excessive = client.post("/demo/analysis", json={"weights": {"DEMO-EQ1": 101}})
        typo = client.post("/demo/analysis", json={"weight": {}})

    # Check the real HTTP response so provenance survives serialization
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "simulated"
    assert body["dataset_version"] == "demo-v1"
    assert body["cash_weight_percent"] == 40
    assert body["return_observations"] == 252
    assert "not market history" in body["notice"]
    assert unknown.status_code == excessive.status_code == typo.status_code == 422
