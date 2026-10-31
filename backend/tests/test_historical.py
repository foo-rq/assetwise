from datetime import date, timedelta
from math import sqrt

import pytest
from fastapi.testclient import TestClient

from backend.analytics import AllocationRequest, analyse_portfolio
from backend.historical import build_historical_history
from backend.history import AssetHistory, History
from backend.main import app
from scripts.import_history import parse_prices


def test_archive_coverage_and_source_contract():
    history = build_historical_history()
    assert history.source == "historical"
    assert history.dates[0] == "2013-01-02"
    assert history.dates[-1] == "2017-11-10"
    assert len(history.dates) == 1226
    assert len(history.assets) == 46
    assert all(len(asset.index_values) == 1226 for asset in history.assets)
    assert history.dataset_version == "archive-2017-v2"
    assert "adjusted closes" in history.value_basis
    assert history.source_url == "https://finance.yahoo.com/"


def test_real_calendar_paths_use_same_hand_calculated_engine():
    history = History(source="historical", dataset_version="test", notice="Test fixture",
        value_basis="Fixture closes", time_basis="Trading dates",
        sessions=[0, 1, 2], dates=["2017-11-08", "2017-11-09", "2017-11-10"],
        assets=[AssetHistory(symbol="SPY", name="Fixture", index_values=[100, 110, 99])])
    result = analyse_portfolio(history, AllocationRequest(weights={"SPY": 50}))
    assert result.source == "historical"
    assert result.dates == history.dates
    assert result.session_returns == pytest.approx([0.05, -0.05])
    assert result.cumulative_return == pytest.approx(-0.0025)
    assert result.annualised_volatility == pytest.approx(sqrt(.02 * 252) / 2)
    assert result.maximum_drawdown == pytest.approx(.05)


@pytest.mark.parametrize("dates", [[], ["2017-11-10"] * 3, ["2017-11-10", "2017-11-09"]])
def test_historical_dates_cannot_be_missing_duplicated_or_unaligned(dates):
    history = build_historical_history().model_copy(update={"dates": dates})
    with pytest.raises(ValueError):
        analyse_portfolio(history, AllocationRequest(weights={"SPY": 100}))


def test_pinned_first_spy_return_and_cash():
    history = build_historical_history()
    spy = next(asset for asset in history.assets if asset.symbol == "SPY")
    result = analyse_portfolio(history, AllocationRequest(weights={"SPY": 100}))
    assert result.session_returns[0] == pytest.approx(spy.index_values[1] / spy.index_values[0] - 1)
    cash = analyse_portfolio(history, AllocationRequest(weights={}))
    assert cash.cash_weight_percent == 100
    assert cash.cumulative_return == cash.annualised_volatility == cash.maximum_drawdown == 0


def test_apple_split_does_not_create_a_false_portfolio_crash():
    # Apple's 7-for-1 split began trading on 9 June 2014, per its investor-relations record
    # This spot check catches an unadjusted split jump without certifying every dividend adjustment
    history = build_historical_history()
    apple = next(asset for asset in history.assets if asset.symbol == "AAPL")
    before = apple.index_values[history.dates.index("2014-06-06")]
    after = apple.index_values[history.dates.index("2014-06-09")]
    assert abs(after / before - 1) < .05


def test_historical_api_analysis_comparison_and_planning():
    with TestClient(app) as client:
        history = client.get("/api/history").json()
        assert history["source"] == "historical"
        current = {"weights": {"SPY": 60, "IEF": 30}}
        result = client.post("/api/analysis", json=current).json()
        assert result["return_observations"] == 1225
        comparison = client.post("/api/comparison", json={"current": current, "proposed": current}).json()
        assert all(value == 0 for value in comparison["changes"].values())
        limits = {"current": current, "cash_percent": 10, "max_position_percent": 40}
        candidates = client.post("/api/candidates", json=limits)
        assert candidates.status_code == 200
        assert candidates.json()["source"] == "historical"
        optimised = client.post("/api/optimise", json=limits)
        assert optimised.status_code == 200
        proposal = optimised.json()["proposal"]
        assert max(proposal["weights"].values()) <= 40
        assert sum(proposal["weights"].values()) == pytest.approx(90, abs=1e-6)
        assert proposal["comparison"]["proposed"]["source"] == "historical"
        assert client.post("/api/analysis", json={"weights": {"DEMO-EQ1": 50}}).status_code == 422
        assert client.get("/api/history?dataset=demo").status_code == 422
        assert client.get("/api/history?dataset=unknown").status_code == 422


def test_importer_rejects_source_defects_before_writing():
    rows = ["Date,Close"] + [
        f"{date(2013, 1, 2) + timedelta(days=index)},100" for index in range(252)
    ]
    assert len(parse_prices("\n".join(rows).encode())) == 252
    for corrupted in [rows + [rows[-1]], [rows[0]] + rows[:0:-1], rows[:-1] + ["2013-09-10,0"]]:
        with pytest.raises(ValueError):
            parse_prices("\n".join(corrupted).encode())
