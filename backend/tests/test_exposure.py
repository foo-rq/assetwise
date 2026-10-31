import pytest

from backend.exposure import portfolio_exposure


def test_direct_and_indirect_exposure_are_added_once():
    result = portfolio_exposure({"AAPL": 50, "SPY": 50})
    apple = next(item for item in result["overlaps"] if item["symbol"] == "AAPL")
    indirect = 50 * 8962735168 / 243299575525
    assert apple["total_percent"] == pytest.approx(50 + indirect)
    assert apple["routes"] == pytest.approx({"AAPL": 50, "SPY": indirect})
    assert sum(result["sectors"].values()) + result["unclassified_percent"] == pytest.approx(100)


def test_two_funds_share_securities_without_direct_holding():
    result = portfolio_exposure({"SPY": 40, "QQQ": 40})
    assert {item["symbol"] for item in result["overlaps"]} >= {"AAPL", "MSFT"}
    assert result["cash_percent"] == 20
    assert result["unclassified_percent"] > 0
    assert all(source["complete"] for source in result["sources"])


def test_uncovered_fund_is_unknown_not_zero_sector_exposure():
    result = portfolio_exposure({"VTI": 100, "AAPL": 0})
    assert result["unclassified_percent"] == 100
    assert result["sectors"] == {}
    assert result["overlaps"] == []


def test_cash_only_has_no_duplicate_or_sector_exposure():
    result = portfolio_exposure({})
    assert result["cash_percent"] == 100
    assert result["unclassified_percent"] == 0


def test_imported_schedules_reconcile_and_spy_equities_have_sectors():
    from backend.exposure import load_exposures
    data = load_exposures()
    expected = {"SPY": 505, "QQQ": 107, "XLY": 83, "XLP": 34, "XLE": 32,
                "XLF": 67, "XLV": 62, "XLI": 68, "XLB": 25, "XLRE": 33, "XLK": 73, "XLU": 28}
    for symbol, count in expected.items():
        fund = data["funds"][symbol]
        assert len(fund["holdings_value_usd"]) == count
        assert sum(fund["holdings_percent"].values()) == pytest.approx(
            sum(fund["holdings_value_usd"].values()) / fund["net_assets_usd"] * 100)
    assert data["funds"]["SPY"]["holdings_percent"].keys() <= data["sectors"].keys()
    assert data["funds"]["SPY"]["holdings_value_usd"]["ppgindustriesinc"] == 314305352


def test_wrapped_company_names_preserve_distinct_share_classes():
    from scripts.import_exposures import holdings
    rows = holdings([(1, "Charter Communications, Inc."),
                     (2, "Class A (b) | 10 | 200"),
                     (3, "Another company Class B | 20 | 300")], 1, 3)
    assert rows == [("Charter Communications, Inc. Class A", 200),
                    ("Another company Class B", 300)]
    with pytest.raises(ValueError, match="Missing company"):
        holdings([(1, "Class A | 10 | 200")], 1, 1)
