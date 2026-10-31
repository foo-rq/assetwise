import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.analytics import AllocationRequest
from backend.main import app
from backend.planning import PlanningRequest, candidates, funded_weights, optimise, solve_weights
from backend.tests.simulated_history import SimulatedAsset, SimulatedHistory, build_simulated_history


def opposite_history():
    # Opposite returns have an exact equal-weight minimum with zero portfolio variance
    return SimulatedHistory(sessions=[0, 1, 2, 3, 4], assets=[
        SimulatedAsset(symbol="A", name="A", index_values=[100, 110, 99, 108.9, 98.01]),
        SimulatedAsset(symbol="B", name="B", index_values=[100, 90, 99, 89.1, 98.01]),
    ])


def test_funding_preserves_cash_and_scales_existing_candidate_correctly():
    assert funded_weights({"A": 70, "B": 20}, "C", 15) == {"A": 59.5, "B": 17, "C": 15}
    assert funded_weights({"A": 70, "B": 20}, "A", 40) == {"B": 40, "A": 40}
    with pytest.raises(ValueError):
        funded_weights({"A": 100}, "A", 15)


def test_optimizer_matches_hand_solution_and_fixed_cash():
    weights = solve_weights(opposite_history(), 20, 60)
    assert weights == pytest.approx({"A": 40, "B": 40}, abs=1e-6)
    result = optimise(opposite_history(), PlanningRequest(current={"weights": {"A": 80}}, cash_percent=20, max_position_percent=60))
    assert result.proposal.comparison.proposed.annualised_volatility < 1e-8
    assert len(result.sensitivity) == 2
    assert all(item.return_observations == 2 for item in result.sensitivity)


def test_infeasible_limits_are_not_relaxed():
    with pytest.raises(ValueError, match="Position limit"):
        solve_weights(opposite_history(), 0, 40)


def test_cash_only_is_valid_and_no_candidate_is_required():
    history = build_simulated_history()
    request = PlanningRequest(current={"weights": {}}, cash_percent=100)
    assert candidates(history, request).proposals == []
    assert optimise(history, request).proposal.weights == {}


def test_candidate_threshold_and_tradeoff_values():
    history = opposite_history()
    result = candidates(history, PlanningRequest(current={"weights": {"A": 100}}, target_percent=50, max_position_percent=100, cash_percent=0))
    assert [item.name for item in result.proposals] == ["B"]
    assert result.proposals[0].comparison.changes.annualised_volatility < -0.001


def test_solver_handles_identical_and_constant_paths():
    history = opposite_history()
    history.assets[1].index_values = history.assets[0].index_values.copy()
    assert sum(solve_weights(history, 10, 60).values()) == pytest.approx(90, abs=1e-6)
    for asset in history.assets:
        asset.index_values = [100] * 5
    assert sum(solve_weights(history, 0, 100).values()) == pytest.approx(100, abs=1e-6)


def test_real_demo_respects_limits_and_recomputes_same_results():
    result = optimise(build_simulated_history(), PlanningRequest(current={"weights": {"DEMO-EQ1": 70, "DEMO-EQ2": 20}}))
    assert max(result.proposal.weights.values()) <= 40
    assert sum(result.proposal.weights.values()) == pytest.approx(90, abs=1e-6)
    assert result.proposal.comparison.changes.annualised_volatility < 0
    for item in result.sensitivity:
        AllocationRequest(weights=item.weights)
        assert max(item.weights.values()) <= 40


@pytest.mark.parametrize("field,value", [("target_percent", 0), ("cash_percent", -1), ("max_position_percent", "40"), ("cash_percent", True)])
def test_invalid_planning_constraints(field, value):
    with pytest.raises(ValidationError):
        PlanningRequest(current={"weights": {}}, **{field: value})


@pytest.mark.parametrize("route", ["candidates", "optimise"])
def test_planning_endpoints(route):
    client = TestClient(app)
    assert client.post(f"/api/{route}", json={"current": {"weights": {}}}).status_code == 200
    assert client.post(f"/api/{route}", json={"current": {"weights": {"UNKNOWN": 10}}}).status_code == 422


def test_binding_position_cap():
    history = opposite_history()
    history.assets[1].index_values = [100] * 5
    weights = solve_weights(history, 0, 60)
    assert weights == pytest.approx({"A": 40, "B": 60}, abs=1e-5)


def test_failed_solver_is_never_returned_as_a_proposal(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("backend.planning.minimize", lambda *args, **kwargs: SimpleNamespace(success=False))
    with pytest.raises(ValueError, match="did not converge"):
        solve_weights(opposite_history(), 0, 100)
