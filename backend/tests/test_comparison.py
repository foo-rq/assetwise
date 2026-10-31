import pytest
from fastapi.testclient import TestClient

from backend.comparison import ComparisonRequest, compare_portfolios
from backend.main import app
from backend.tests.simulated_history import SimulatedAsset, SimulatedHistory


def compare(current, proposed):
    # Opposite session returns cancel at equal weights, making the expected risk change clear
    history = SimulatedHistory(sessions=[0, 1, 2], assets=[
        SimulatedAsset(symbol="A", name="A", index_values=[100, 110, 99]),
        SimulatedAsset(symbol="B", name="B", index_values=[100, 90, 99]),
    ])
    request = ComparisonRequest(current={"weights": current}, proposed={"weights": proposed})
    result = compare_portfolios(history, request)

    # Previewing must not mutate either submitted allocation
    assert request.current.weights == current
    assert request.proposed.weights == proposed
    return result


def test_diversification_can_reduce_risk():
    result = compare({"A": 100}, {"A": 50, "B": 50})
    assert result.proposed.annualised_volatility == pytest.approx(0, abs=1e-12)
    assert result.changes.annualised_volatility == pytest.approx(-result.current.annualised_volatility)
    assert result.changes.maximum_drawdown == pytest.approx(-0.1)


def test_concentration_can_increase_risk():
    result = compare({"A": 50, "B": 50}, {"A": 100})
    assert result.changes.annualised_volatility > 0
    assert result.changes.maximum_drawdown == pytest.approx(0.1)


@pytest.mark.parametrize("weights", [{}, {"A": 70, "B": 20}])
def test_identical_allocations_have_no_change(weights):
    result = compare(weights, weights)
    assert all(value == 0 for value in result.changes.model_dump().values())
    assert result.current == result.proposed


def test_cash_and_return_changes_use_percentage_points():
    # Returns of -10% then 11.111% give a 140% annualised arithmetic result in this tiny sample
    result = compare_portfolios(
        SimulatedHistory(sessions=[0, 1, 2], assets=[
            SimulatedAsset(symbol="B", name="B", index_values=[100, 90, 100]),
        ]),
        ComparisonRequest(current={"weights": {"B": 100}}, proposed={"weights": {"B": 50}}),
    )
    assert result.proposed.cash_weight_percent == 50
    assert result.proposed.annualised_arithmetic_return == pytest.approx(0.7)
    assert result.changes.annualised_arithmetic_return == pytest.approx(-0.7)
    assert result.proposed.annualised_volatility == pytest.approx(result.current.annualised_volatility / 2)


def test_api_uses_one_historical_basis():
    response = TestClient(app).post("/api/comparison", json={
        "current": {"weights": {"AAPL": 70, "SPY": 20}},
        "proposed": {"weights": {"AAPL": 59.5, "SPY": 17, "IEF": 15}},
    })
    assert response.status_code == 200
    result = response.json()
    for field in ("source", "dataset_version", "return_observations", "rebalancing", "cash_assumption"):
        assert result["current"][field] == result["proposed"][field]
    assert result["current"]["source"] == "historical"
    assert result["proposed"]["cash_weight_percent"] == 8.5


@pytest.mark.parametrize("side", ["current", "proposed"])
@pytest.mark.parametrize("weights", [{"UNKNOWN": 10}, {"DEMO-EQ1": 101}, {"DEMO-EQ1": "50"}])
def test_api_rejects_invalid_allocations_on_either_side(side, weights):
    payload = {"current": {"weights": {}}, "proposed": {"weights": {}}}
    payload[side] = {"weights": weights}
    assert TestClient(app).post("/api/comparison", json=payload).status_code == 422
