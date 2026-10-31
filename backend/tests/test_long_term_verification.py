import pytest

from scripts.verify_long_term import performance


def test_daily_rebalancing_and_untraded_holdings_have_different_paths():
    dates = ["2020-01-29", "2020-01-30", "2020-01-31", "2020-02-03"]
    prices = {"A": [100, 200, 100, 200]}
    weights = {"A": 50}
    assert performance(dates, prices, weights, "hold")["cumulative_return"] == pytest.approx(.5)
    assert performance(dates, prices, weights, "daily")["cumulative_return"] == pytest.approx(.6875)
    assert performance(dates, prices, weights, "monthly")["cumulative_return"] == pytest.approx(.5)


def test_month_end_rebalance_changes_only_the_following_return():
    dates = ["2020-01-30", "2020-01-31", "2020-02-03"]
    prices = {"A": [100, 200, 100]}
    assert performance(dates, prices, {"A": 50}, "monthly")["cumulative_return"] == pytest.approx(.125)
    assert performance(dates, prices, {"A": 50}, "hold")["cumulative_return"] == pytest.approx(0)


def test_initial_trades_cost_money_but_retaining_existing_holdings_does_not():
    dates = ["2020-01-29", "2020-01-30", "2020-01-31"]
    prices = {"A": [100, 100, 100], "B": [100, 100, 100]}
    unchanged = performance(dates, prices, {"A": 100}, "hold", .001)
    changed = performance(dates, prices, {"A": 70, "B": 30}, "hold", .001, {"A": 100})
    assert unchanged["cumulative_return"] == 0
    # Sell A and buy B using post-fee wealth: W = 1 - .001 * (1 - .7W + .3W)
    expected_wealth = .999 / .9996
    assert changed["cumulative_return"] == pytest.approx(expected_wealth - 1)
    assert changed["maximum_drawdown"] == pytest.approx(1 - expected_wealth)


def test_missing_prices_are_rejected_instead_of_filling_or_dropping_sessions():
    with pytest.raises(AssertionError):
        performance(["2020-01-29", "2020-01-30", "2020-01-31"],
                    {"A": [100, float("nan"), 101]}, {"A": 100})


@pytest.mark.parametrize("cadence", ["daily", "monthly", "quarterly", "semiannual", "annual", "hold"])
def test_all_cash_preserves_value_and_charges_only_actual_initial_sales(cadence):
    dates = ["2020-01-30", "2020-01-31", "2020-02-03"]
    unchanged = performance(dates, {}, {}, cadence, .001)
    assert all(value == 0 for value in unchanged.values())
    # Selling an existing position incurs one sale fee; holding the resulting cash incurs no more trades
    liquidated = performance(dates, {}, {}, cadence, .001, {"A": 100})
    assert liquidated["cumulative_return"] == pytest.approx(-.001)
    assert liquidated["maximum_drawdown"] == pytest.approx(.001)


def test_real_outcome_deflates_total_wealth_and_exposes_cash_purchasing_power_loss():
    from scripts.verify_investor_outcomes import real_performance

    dates = ["2020-01-02", "2020-07-02", "2021-01-02"]
    nominal = performance(dates, {"A": [100, 150, 200]}, {"A": 100}, "hold")
    growing = real_performance(nominal, 1.25, 1)
    assert growing["real_cumulative_return"] == pytest.approx(.6)
    assert growing["real_cagr"] == pytest.approx(.6)
    assert growing["terminal_real_per_10000"] == pytest.approx(16000)
    cash = real_performance(performance(dates, {}, {}), 1.25, 1)
    assert cash["real_cumulative_return"] == pytest.approx(-.2)
    assert cash["terminal_real_per_10000"] == pytest.approx(8000)
    with pytest.raises(ValueError):
        real_performance(nominal, float("nan"), 1)


def test_investor_audit_keeps_all_cash_no_change_and_charges_comparator_purchases(tmp_path, monkeypatch):
    import json
    import scripts.verify_investor_outcomes as outcomes

    frozen = tmp_path / "frozen.json"
    frozen.write_text(json.dumps({"decisions": [{"portfolio": "all_cash", "goal": "growth",
                                                "original": {}, "proposals": []}]}))
    cpi = tmp_path / "cpi.json"
    cpi.write_text(json.dumps({"series": "CUUR0000SA0", "decision_date": "2017-11-10",
        "verified_on": "2026-10-29", "known_at_decision_month": "2017-09",
        "evaluation_start_month": "2017-11", "evaluation_end_month": "2026-09",
        "observations": [{"month": month, "value": value, "released": released, "url": "https://example.invalid/fixture"}
                         for month, value, released in (("2017-09", 100, "2017-10-13"),
                                                        ("2017-11", 100, "2017-12-13"),
                                                        ("2026-09", 125, "2026-10-14"))]}))
    for symbol in ("SPY", "EFA", "EEM", "IEF", "LQD", "GLD", "VNQ"):
        (tmp_path / f"{symbol}.json").write_text("{}")
    # Flat fixture prices isolate cash inclusion, inflation and initial benchmark fees from market changes
    monkeypatch.setattr(outcomes, "parse_adjusted", lambda *args, **kwargs:
                        {"2017-11-13": 100, "2018-01-02": 100, "2026-09-30": 100})
    result = outcomes.audit(frozen, tmp_path / "output", tmp_path, cpi, "growth")
    assert result["summary"]["monthly"]["decisions"] == 1
    assert result["summary"]["monthly"]["no_change"] == 1
    assert result["summary"]["monthly"]["real_positive"] == 0
    row = next(r for r in result["rows"] if r["cadence"] == "monthly")
    assert row["outcomes"]["recommendation"]["terminal_real_per_10000"] == pytest.approx(8000)
    # The purchase fee also reduces the dollars actually bought: W + .001W = 1
    assert row["outcomes"]["SPY"]["terminal_real_per_10000"] == pytest.approx(8000 / 1.001)


@pytest.mark.parametrize("cadence,dates", [
    ("quarterly", ["2020-03-30", "2020-03-31", "2020-04-01"]),
    ("semiannual", ["2020-06-29", "2020-06-30", "2020-07-01"]),
    ("annual", ["2020-12-30", "2020-12-31", "2021-01-04"]),
])
def test_calendar_trade_applies_only_after_the_period_end(cadence, dates):
    prices = {"A": [100, 200, 100]}
    assert performance(dates, prices, {"A": 50}, cadence)["cumulative_return"] == pytest.approx(.125)
    # A monthly boundary inside the larger period must not trigger the same trade
    inside = ["2020-01-30", "2020-01-31", "2020-02-03"]
    assert performance(inside, prices, {"A": 50}, cadence)["cumulative_return"] == pytest.approx(0)


def test_rebalance_fees_match_the_actual_post_fee_trades():
    from scripts.verify_long_term import wealth_after_trading
    import numpy as np

    holdings = np.array([.8, .1])
    target = np.array([.4, .4])
    # W = 1 - .1 * ((.8 - .4W) + (.4W - .1)) = .93
    remaining = wealth_after_trading(1.0, holdings, target, .1)
    assert remaining == pytest.approx(.93)
    assert remaining + .1 * np.abs(target * remaining - holdings).sum() == pytest.approx(1.0)


def test_final_close_does_not_charge_an_unneeded_maintenance_trade():
    dates = ["2020-01-29", "2020-01-30", "2020-01-31"]
    # The last price move changes weights but there is no subsequent session to maintain
    result = performance(dates, {"A": [100, 100, 200]}, {"A": 50}, "daily", .01)
    assert result["cumulative_return"] == pytest.approx(.5)


def test_first_day_gain_does_not_hide_the_initial_fee_drawdown():
    dates = ["2020-01-29", "2020-01-30", "2020-01-31"]
    result = performance(dates, {"A": [100, 110, 120]}, {"A": 100}, "hold", .01, {})
    assert result["cumulative_return"] == pytest.approx(1.2 / 1.01 - 1)
    assert result["maximum_drawdown"] == pytest.approx(1 - 1 / 1.01)
