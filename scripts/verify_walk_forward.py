"""Evaluate the unchanged recommendation rule at successive historical decision dates"""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

from backend.historical import build_historical_history
from backend.planning import PlanningRequest, candidates
from scripts.import_adjusted_history import parse_adjusted
from scripts.verify_long_term import PORTFOLIOS, performance
from scripts.verify_recommendations import window


OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "walk-forward-horizon-v2"
RANKING_VERSION = "horizon-growth-path-risk-v2"
# These retrospective labels group test outcomes only; they never enter forecast inputs or selection
EVALUATION_CONTEXT = {
    2020: "COVID shock",
    2021: "Pandemic recovery",
    2022: "Inflation and rate increases",
    2023: "Continued monetary tightening",
}


def download(symbol):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           "?period1=1356998400&period2=1793145600&interval=1d&events=div%2Csplits%2CcapitalGains")
    raw = urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read()
    (OUTPUT / f"{symbol}.json").write_bytes(raw)
    prices = parse_adjusted(raw, symbol, end="2026-10-27")
    return symbol, prices, dict(url=url, sha256=hashlib.sha256(raw).hexdigest())


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    archive = build_historical_history()
    with ThreadPoolExecutor(max_workers=4) as pool:
        downloaded = list(pool.map(download, [a.symbol for a in archive.assets]))
    paths = {s: p for s, p, _ in downloaded}
    dates = list(paths["SPY"])
    if any(list(p) != dates for p in paths.values()):
        raise ValueError("Later prices must share a complete calendar")
    # Extended prices are research inputs only; each model receives its earlier training slice
    history = archive.model_copy(update=dict(dates=dates, sessions=list(range(len(dates))),
        risk_free_returns=[], assets=[a.model_copy(update={"index_values": list(paths[a.symbol].values())})
                                      for a in archive.assets]))
    portfolios = {**PORTFOLIOS, "tech_fund": {"QQQ": 100}, "volatile_stock": {"NVDA": 100},
                  "balanced": {"SPY": 50, "BND": 30, "GLD": 20},
                  "international": {"VEA": 40, "VWO": 20, "SPY": 40}}
    # Include every supported starting asset, preserving the original illustrative cases for comparison
    for asset in archive.assets:
        weights = {asset.symbol: 100}
        if weights not in portfolios.values():
            portfolios[f"{asset.symbol}_only"] = weights
    decisions = []
    for year in range(2016, 2025):
        stop = next(i for i, day in enumerate(dates) if day.startswith(str(year + 1)))
        train = window(history, max(0, stop - 1261), stop)
        for name, weights in portfolios.items():
            unique = {}
            for target in (15, 30):
                request = PlanningRequest(current={"weights": weights}, target_percent=target,
                    max_position_percent=100, cash_percent=0,
                    profile=dict(experience="some", age="private", goal="growth", horizon="medium", horizon_years=9.0,
                                 tolerance="high", capacity="spare", existing="yes"))
                for proposal in candidates(train, request).proposals:
                    old = unique.get(proposal.name)
                    if old is None or proposal.rank_score < old.rank_score:
                        unique[proposal.name] = proposal
            chosen = sorted(unique.values(), key=lambda p: p.rank_score)[:3]
            decisions.append(dict(year=year, portfolio=name, original=weights,
                training_end=train.dates[-1], execution=dates[stop],
                proposals=[dict(symbol=p.name, weights=p.weights,
                                forecast=p.comparison.forecast.model_dump()) for p in chosen]))
        print(f"Frozen {year} decisions", flush=True)
    # No later outcome is scored until every selection and forecast has been written
    frozen = dict(ranking_version=RANKING_VERSION,
                  sources={s: metadata for s, _, metadata in downloaded}, decisions=decisions)
    (OUTPUT / "frozen.json").write_text(json.dumps(frozen, indent=2) + "\n", encoding="utf-8")
    rows = []
    for decision in decisions:
        start = dates.index(decision["execution"])
        test_dates = dates[start:start + 253]
        if len(test_dates) != 253:
            raise ValueError("A full 252-return evaluation is required")
        prices = {a.symbol: a.index_values[start:start + 253] for a in history.assets}
        for cadence, fee in (("daily", 0), ("monthly", .001), ("hold", 0)):
            baseline = performance(test_dates, prices, decision["original"], cadence, fee)
            for rank, proposal in enumerate(decision["proposals"], 1):
                after = performance(test_dates, prices, proposal["weights"], cadence, fee, decision["original"])
                delta = {key: after[key] - baseline[key] for key in baseline}
                lower = delta["annualised_volatility"] <= -.001
                success = lower and delta["annualised_arithmetic_return"] >= -1e-12 and delta["cumulative_return"] >= -1e-12
                rows.append(dict(year=decision["year"], portfolio=decision["portfolio"], rank=rank,
                    symbol=proposal["symbol"], cadence=cadence, fee=fee, lower_volatility=lower,
                    success=success, changes=delta, start=test_dates[0], end=test_dates[-1]))
    result = dict(ranking_version=RANKING_VERSION, decisions=decisions, results=rows,
        evaluation_context=EVALUATION_CONTEXT,
        limitations="One-year stress diagnostic for a nine-year planning profile, not a completed-horizon accuracy measure; selected surviving assets and overlapping inspected development cases; zero-yield cash; no taxes; each dated fit uses earlier observations only")
    (OUTPUT / "results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(decisions=len(decisions), comparisons=len(rows))))


if __name__ == "__main__":
    main()
