import math
import random
import hashlib
import json

from fastapi.testclient import TestClient

from backend.main import app
from backend.tests.simulated_history import build_simulated_history
from backend.analytics import AllocationRequest, analyse_portfolio


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
    assert len({asset.symbol for asset in history.assets}) == 12

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


def test_demo_is_no_longer_a_public_product_route():
    with TestClient(app) as client:
        assert client.get("/demo/history").status_code == 404


def test_expansion_preserves_original_paths():
    # Fingerprints were captured from demo-v1 before adding any new assets
    # Protect every observation, not just the final value that could hide intermediate changes
    expected = {
        "DEMO-EQ1": "97832d140fe580969dee11a314b1fcf9c1ef152c00a1a46893cd74b4acf15e01",
        "DEMO-EQ2": "f9936a4e45501e93b6e53a08fda318f2181f5b26fa53b7168d63642f088752ef",
        "DEMO-BOND": "f3281bc8eee99fc39e80f0b5946b61863d0233768b61c989936d5a8db5d90d70",
        "DEMO-GOLD": "9f18b25e6224f1524dc680ff4dbd8afee94a87e537c58f617e204d9ed56b9ded",
    }
    for asset in build_simulated_history().assets:
        if asset.symbol in expected:
            fingerprint = hashlib.sha256(json.dumps(asset.index_values).encode()).hexdigest()
            assert fingerprint == expected[asset.symbol]


def test_expanded_universe_supports_full_portfolio_analysis():
    history = build_simulated_history()

    # Allocate 8% to each asset, leaving 4% cash without rounding or normalising inputs
    allocation = AllocationRequest(weights={asset.symbol: 8 for asset in history.assets})
    result = analyse_portfolio(history, allocation)
    assert result.cash_weight_percent == 4
    assert result.dataset_version == "demo-v2"

    # All 12 assets vary, so each of the 66 distinct pairs must have a usable correlation
    assert len(result.asset_correlations) == 66
    assert all(pair.value is not None and -1 <= pair.value <= 1 for pair in result.asset_correlations)
    assert math.isfinite(result.annualised_volatility)
    assert len({tuple(asset.index_values) for asset in history.assets}) == 12
