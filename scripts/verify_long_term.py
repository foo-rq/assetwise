"""Freeze the November 2017 app suggestions and measure their later outcomes"""

import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
from scipy.optimize import brentq

from backend.analytics import AllocationRequest, analyse_portfolio
from backend.historical import build_historical_history
from backend.planning import PlanningRequest, candidates


OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "forward-profile-horizon-v2"
RANKING_VERSION = "horizon-growth-path-risk-v2"
PLANNING_YEARS = 9.0
GOALS = ("learn", "growth", "preserve")
PORTFOLIOS = {
    "single_stock": {"AAPL": 100},
    "stock_concentration": {"AAPL": 40, "MSFT": 30, "SPY": 30},
    "stocks_and_bonds": {"SPY": 60, "IEF": 30},
    "stock_and_cash": {"AAPL": 50},
}


def wealth_after_trading(wealth, holdings, target, fee):
    # Fees come from the same portfolio, so target purchases depend on the wealth left after fees
    if fee == 0:
        return wealth
    return brentq(lambda remaining: remaining + fee * float(
        np.abs(target * remaining - holdings).sum()) - wealth, 0, wealth, xtol=1e-14)


def performance(dates, prices, weights, cadence="daily", fee=0, initial_weights=None):
    symbols = list(weights)
    target = np.array([weights[symbol] / 100 for symbol in symbols] +
                      [1 - sum(weights.values()) / 100])
    periods = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}
    assert cadence in ("daily", "hold", *periods) and 0 <= fee < 1
    assert len(dates) > 2 and dates == sorted(set(dates))
    assert np.all(target >= -1e-12) and np.all(np.isfinite(target))
    # All-cash portfolios still need a row per date so the simulator can charge liquidation costs
    values = (np.array([prices[symbol] for symbol in symbols], dtype=float).T if symbols else
              np.empty((len(dates), 0)))
    assert values.shape == (len(dates), len(symbols))
    assert np.all(np.isfinite(values)) and np.all(values > 0)
    returns = np.column_stack((values[1:] / values[:-1] - 1, np.zeros(len(dates) - 1)))
    # Charge the recommendation's initial trades, but not a second purchase of existing holdings
    initial_weights = weights if initial_weights is None else initial_weights
    traded_symbols = sorted(set(weights) | set(initial_weights))
    original = np.array([initial_weights.get(s, 0) / 100 for s in traded_symbols])
    initial_target = np.array([weights.get(s, 0) / 100 for s in traded_symbols])
    assert np.all(np.isfinite(original)) and np.all(original >= 0) and original.sum() <= 1 + 1e-12
    initial_wealth = wealth_after_trading(1.0, original, initial_target, fee)
    holdings = target * initial_wealth
    path = [1.0]
    for index, daily in enumerate(returns, 1):
        holdings *= 1 + daily
        wealth = float(holdings.sum())
        # Trade after the close, so the new weights apply only to the next session
        # Calendar boundaries use observed trading dates, including holidays and year changes
        period = periods.get(cadence)
        rebalance = cadence == "daily" or (period is not None and
                    index < len(dates) - 1 and
                    (int(dates[index][:4]), (int(dates[index][5:7]) - 1) // period) !=
                    (int(dates[index + 1][:4]), (int(dates[index + 1][5:7]) - 1) // period))
        if rebalance and index < len(dates) - 1:
            wealth = wealth_after_trading(wealth, holdings[:-1], target[:-1], fee)
            holdings = target * wealth
        path.append(wealth)
    path = np.array(path)
    daily = path[1:] / path[:-1] - 1
    years = (datetime.fromisoformat(dates[-1]) - datetime.fromisoformat(dates[0])).days / 365.25
    return dict(cumulative_return=float(path[-1] - 1), cagr=float(path[-1] ** (1 / years) - 1),
                annualised_arithmetic_return=float(daily.mean() * 252),
                annualised_volatility=float(daily.std(ddof=1) * math.sqrt(252)),
                # A gain on the first day cannot erase the loss already paid to enter the portfolio
                maximum_drawdown=max(1 - initial_wealth,
                    float(np.max(1 - path / np.maximum.accumulate(path)))))


def download(symbol):
    # Pin the requested endpoint to the last completed US session before 28 October 2026
    endpoint = int(datetime(2026, 10, 28, tzinfo=timezone.utc).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1=1510272000&period2={endpoint}&interval=1d&events=div%2Csplits")
    raw = urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read()
    (OUTPUT / f"{symbol}.json").write_bytes(raw)
    chart = json.loads(raw)["chart"]["result"][0]
    if chart["meta"].get("currency") != "USD":
        raise ValueError(f"Unexpected price currency for {symbol}")
    data = {}
    for stamp, price in zip(chart["timestamp"], chart["indicators"]["adjclose"][0]["adjclose"], strict=True):
        day = datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
        if "2017-11-10" < day < "2026-10-28":
            if price is None or not math.isfinite(price) or price <= 0 or day in data:
                raise ValueError(f"Invalid or duplicate adjusted price: {symbol} {day}")
            data[day] = price
    if not data:
        raise ValueError(f"No later prices for {symbol}")
    return symbol, data, dict(url=url, sha256=hashlib.sha256(raw).hexdigest(),
                             first=min(data), last=max(data), observations=len(data))


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    history = build_historical_history()
    assert history.dates[-1] == "2017-11-10"
    # Cover every supported starting asset so a few Apple-heavy examples cannot dominate the verdict
    portfolios = dict(PORTFOLIOS)
    for asset in history.assets:
        weights = {asset.symbol: 100}
        if weights not in portfolios.values():
            portfolios[f"{asset.symbol}_only"] = weights
    decisions = []
    for name, weights in portfolios.items():
        for goal in GOALS:
            profile = dict(experience="some", age="private", goal=goal, horizon="medium", horizon_years=PLANNING_YEARS,
                           tolerance="high", capacity="spare", existing="yes")
            unique = {}
            for target in (15, 30):
                result = candidates(history, PlanningRequest(current={"weights": weights},
                                    target_percent=target, max_position_percent=100,
                                    cash_percent=0, profile=profile))
                for proposal in result.proposals:
                    previous = unique.get(proposal.name)
                    if previous is None or proposal.rank_score < previous.rank_score:
                        unique[proposal.name] = proposal
            chosen = sorted(unique.values(), key=lambda item: item.rank_score)[:3]
            decisions.append(dict(portfolio=name, goal=goal, original=weights, profile=profile,
                                  proposals=[dict(symbol=p.name, weights=p.weights,
                                                  training_changes=p.comparison.changes.model_dump(),
                                                  forecast=p.comparison.forecast.model_dump() if p.comparison.forecast else None)
                                             for p in chosen]))
        print(f"Frozen recommendations for {name}", flush=True)
    # Persist the decisions before fetching any later outcomes
    frozen = dict(ranking_version=RANKING_VERSION, training_end=history.dates[-1], dataset_version=history.dataset_version,
                  training_sha256=hashlib.sha256(history.model_dump_json().encode()).hexdigest(),
                  decisions=decisions)
    (OUTPUT / "frozen.json").write_text(json.dumps(frozen, indent=2) + "\n", encoding="utf-8")
    symbols = sorted({s for decision in decisions for p in
                      [dict(weights=decision["original"])] + decision["proposals"] for s in p["weights"]})
    with ThreadPoolExecutor(max_workers=4) as pool:
        downloaded = list(pool.map(download, symbols))
    sources = {s: metadata for s, _, metadata in downloaded}
    observations = {s: data for s, data, _ in downloaded}
    dates = sorted(next(iter(observations.values())))
    for symbol, data in observations.items():
        if sorted(data) != dates:
            raise ValueError(f"Unmatched sessions for {symbol}; do not silently discard missing prices")
    assert dates[0] == "2017-11-13"
    prices = {s: [data[d] for d in dates] for s, data in observations.items()}
    results = []
    horizons = {"1_year": "2018-11-13", "3_years": "2020-11-13",
                "5_years": "2022-11-13", "latest": dates[-1]}
    for horizon, end in horizons.items():
        stop = sum(day <= end for day in dates)
        selected_dates = dates[:stop]
        selected_prices = {s: p[:stop] for s, p in prices.items()}
        for cadence, fee in (("daily", 0), ("monthly", 0), ("hold", 0), ("monthly", .001)):
            for decision in decisions:
                baseline = performance(selected_dates, selected_prices, decision["original"], cadence, fee)
                for rank, proposal in enumerate(decision["proposals"], 1):
                    after = performance(selected_dates, selected_prices, proposal["weights"], cadence,
                                        fee, decision["original"])
                    changes = {k: after[k] - baseline[k] for k in baseline}
                    lower = changes["annualised_volatility"] <= -.001
                    goal = decision["goal"]
                    rule = lower and (changes["maximum_drawdown"] <= 1e-12 if goal == "preserve"
                                      else changes["cumulative_return"] >= -1e-12
                                      if goal == "growth" else True)
                    results.append(dict(portfolio=decision["portfolio"], goal=goal, rank=rank,
                                        symbol=proposal["symbol"], weights=proposal["weights"],
                                        horizon=horizon, start=selected_dates[0], end=selected_dates[-1],
                                        cadence=cadence, fee_per_dollar_traded=fee, baseline=baseline,
                                        proposed=after, changes=changes, forecast=proposal["forecast"], lower_volatility=lower,
                                        retained_rule=rule,
                                        legacy_three_metric_rule=lower and changes["cumulative_return"] >= -1e-12 and
                                        changes["annualised_arithmetic_return"] >= -1e-12))
    # Cross-check the independent daily simulator against the app on its original archive
    original_prices = {a.symbol: a.index_values for a in history.assets}
    for weights in portfolios.values():
        expected = analyse_portfolio(history, AllocationRequest(weights=weights))
        actual = performance(history.dates, original_prices, weights)
        for field in ("cumulative_return", "annualised_volatility", "maximum_drawdown",
                      "annualised_arithmetic_return"):
            assert math.isclose(actual[field], getattr(expected, field), abs_tol=1e-10)
    report = dict(ranking_version=RANKING_VERSION, training_end=history.dates[-1], training_sha256=frozen["training_sha256"],
                  execution=dates[0], first_return=dates[1],
                  latest_available=dates[-1], requested_end="2026-10-27", sources=sources,
                  decisions=decisions, results=results,
                  assumptions="Adjusted closes with reinvested distributions; USD nominal; cash earns 0%; no taxes; monthly fee sensitivity is an assumed 10 basis points per dollar bought or sold, not observed fees; fixed 2017 recommendations; selected surviving universe; overlapping illustrative portfolios are not independent trials")
    (OUTPUT / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(execution=dates[0], latest=dates[-1], symbols=len(symbols),
                         decisions=len(decisions), comparisons=len(results)), indent=2))


if __name__ == "__main__":
    main()
