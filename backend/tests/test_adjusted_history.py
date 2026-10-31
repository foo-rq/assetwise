import json

import pytest

from backend.historical import build_historical_history
from scripts.import_adjusted_history import parse_adjusted


def response():
    return {"chart": {"error": None, "result": [{
        "meta": {"symbol": "SPY", "currency": "USD"},
        "timestamp": [1483401600, 1483488000, 1483574400],
        "indicators": {"adjclose": [{"adjclose": [90, 91, 92]}],
                       "quote": [{"close": [100, 101.111111111, 102.222222222]}]},
    }]}}


def test_adjusted_import_rejects_unexplained_return_changes_and_wrong_identity():
    data = response()
    assert len(parse_adjusted(json.dumps(data), "SPY")) == 3
    with pytest.raises(ValueError, match="symbol or currency"):
        parse_adjusted(json.dumps(data), "AAPL")
    data["chart"]["result"][0]["indicators"]["adjclose"][0]["adjclose"][1] = 95
    with pytest.raises(ValueError, match="Unexplained adjustment"):
        parse_adjusted(json.dumps(data), "SPY")


def test_adjusted_import_rejects_missing_and_duplicate_observations():
    for defect in ("duplicate", "missing", "nonfinite"):
        data = response()
        chart = data["chart"]["result"][0]
        if defect == "duplicate":
            chart["timestamp"][1] = chart["timestamp"][0]
        elif defect == "missing":
            chart["indicators"]["adjclose"][0]["adjclose"].pop()
        else:
            chart["indicators"]["quote"][0]["close"][1] = None
        with pytest.raises(ValueError):
            parse_adjusted(json.dumps(data), "SPY")


def test_distribution_amount_must_explain_the_adjustment_not_only_its_date():
    data = response()
    chart = data["chart"]["result"][0]
    chart["indicators"]["quote"][0]["close"] = [100, 99, 100]
    chart["indicators"]["adjclose"][0]["adjclose"] = [99, 99, 100]
    chart["events"] = {"dividends": {"event": {"date": chart["timestamp"][1], "amount": 1}}}
    assert len(parse_adjusted(json.dumps(data), "SPY")) == 3
    chart["events"]["dividends"]["event"]["amount"] = 2
    with pytest.raises(ValueError, match="Unexplained adjustment"):
        parse_adjusted(json.dumps(data), "SPY")


def test_corrected_spy_return_matches_closes_without_a_distribution():
    history = build_historical_history()
    prices = next(a.index_values for a in history.assets if a.symbol == "SPY")
    i = history.dates.index("2015-03-13")
    # No distribution or split occurred between these 2 closes, so adjustment factors cancel
    assert prices[i] / prices[i - 1] - 1 == pytest.approx(205.83 / 207.10 - 1, abs=1e-6)


def test_legacy_snapshot_cannot_be_labelled_as_corrected_data(tmp_path, monkeypatch):
    import backend.historical as historical

    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "manifest.json").write_text('{"dataset_version":"archive-2017-v1"}', encoding="utf-8")
    monkeypatch.setattr(historical, "Path", lambda *_: tmp_path / "loader.py")
    with pytest.raises(ValueError, match="corrected archive"):
        historical.build_historical_history.__wrapped__()
