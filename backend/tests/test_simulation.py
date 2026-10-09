import math
import random

from fastapi.testclient import TestClient

from backend.main import app
from backend.simulation import build_simulated_history


def test_history_is_repeatable_and_does_not_change_global_randomness():
    # Other code must not affect the scenario or have its random state changed by it
    state_before = random.getstate()
    first = build_simulated_history()
    second = build_simulated_history()

    # Check both isolation from other random code and identical results on repeated calls
    assert random.getstate() == state_before
    assert first == second

    # A caller changing one response must not corrupt later responses
    first.assets[0].index_values[0] = 0
    assert build_simulated_history() == second


def test_history_has_aligned_valid_paths_with_gains_and_losses():
    history = build_simulated_history()

    # Consecutive sessions expose missing observations and the set catches duplicate identifiers
    assert history.sessions == list(range(253))
    assert len({asset.symbol for asset in history.assets}) == 4

    for asset in history.assets:
        values = asset.index_values

        # Each series needs the same starting index and one value for every shared session
        assert len(values) == len(history.sessions)
        assert values[0] == 100

        # Zero breaks return division, while NaN and infinity would contaminate later metrics
        assert all(math.isfinite(value) and value > 0 for value in values)

        # Losses must be possible even for the quieter fictional bond fund
        changes = [after / before - 1 for before, after in zip(values, values[1:])]
        assert min(changes) < 0 < max(changes)


def test_api_identifies_fictional_data_and_returns_stable_results():
    # Exercise HTTP serialization too, so the check covers what the browser actually receives
    with TestClient(app) as client:
        response = client.get("/demo/history")
        repeated = client.get("/demo/history")

    assert response.status_code == 200
    body = response.json()

    # Refreshing must not silently replace the inputs used for an allocation comparison
    assert body == repeated.json()

    # Keep provenance and the warning in the public response, not only in Python comments
    assert body["source"] == "simulated"
    assert body["dataset_version"] == "demo-v1"
    assert "not market history" in body["notice"]

    # Real tickers must not accidentally appear beside these invented price paths
    assert all(asset["symbol"].startswith("DEMO-") for asset in body["assets"])
    assert len(body["sessions"]) == 253
