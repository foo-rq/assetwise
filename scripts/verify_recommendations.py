"""Evaluate frozen historical suggestions on later prices without changing app data"""

import json
from pathlib import Path

from backend.analytics import AllocationRequest, analyse_portfolio
from backend.historical import build_historical_history
from backend.planning import PlanningRequest, candidates


def window(history, start, stop):
    # Keep one starting close, then match each reference rate to its following close
    return history.model_copy(update={
        "dates": history.dates[start:stop], "sessions": list(range(stop - start)),
        "risk_free_returns": history.risk_free_returns[start:stop - 1],
        "assets": [asset.model_copy(update={"index_values": asset.index_values[start:stop]})
                   for asset in history.assets],
    })


def main():
    history = build_historical_history()
    portfolios = {"single_stock": {"AAPL": 100},
                  "stock_concentration": {"AAPL": 40, "MSFT": 30, "SPY": 30},
                  "stocks_and_bonds": {"SPY": 60, "IEF": 30},
                  "stock_and_cash": {"AAPL": 50}}
    rows, decisions = [], []
    for year in (2015, 2016, 2017):
        first = next(i for i, day in enumerate(history.dates) if day.startswith(str(year)))
        stop = max(i for i, day in enumerate(history.dates) if day.startswith(str(year))) + 1
        train = window(history, 0, first)
        # Freeze the choice at the prior year-end, execute at the next close and start scoring after it
        later = window(history, first, stop)
        assert train.dates[-1] < later.dates[0] < later.dates[1]
        for portfolio, weights in portfolios.items():
            baseline = analyse_portfolio(later, AllocationRequest(weights=weights))
            for goal in ("learn", "growth", "preserve"):
                profile = dict(experience="some", age="private", goal=goal, horizon="long",
                               tolerance="high", capacity="spare", existing="yes")
                unique = {}
                for target in (15, 30):
                    result = candidates(train, PlanningRequest(current={"weights": weights},
                                        target_percent=target, max_position_percent=100,
                                        cash_percent=0, profile=profile))
                    for proposal in result.proposals:
                        # Mirror the UI's best target per asset, then retain its first 3 ranked alternatives
                        old = unique.get(proposal.name)
                        if old is None or proposal.rank_score < old.rank_score:
                            unique[proposal.name] = proposal
                chosen = sorted(unique.values(), key=lambda item: item.rank_score)[:3]
                decisions.append(dict(year=year, portfolio=portfolio, goal=goal,
                                      proposals=len(chosen)))
                for rank, proposal in enumerate(chosen, 1):
                    after = analyse_portfolio(later, AllocationRequest(weights=proposal.weights))
                    change = {field: getattr(after, field) - getattr(baseline, field) for field in
                              ("annualised_volatility", "annualised_arithmetic_return",
                               "maximum_drawdown", "cumulative_return")}
                    lower = change["annualised_volatility"] <= -.001
                    kept_rule = lower and (change["maximum_drawdown"] <= 1e-12 if goal == "preserve"
                                          else (change["annualised_arithmetic_return"] >= -1e-12 and
                                                change["cumulative_return"] >= -1e-12)
                                          if goal == "growth" else True)
                    rows.append(dict(year=year, portfolio=portfolio, goal=goal, rank=rank,
                                     symbol=proposal.name, weights=proposal.weights,
                                     forecast=proposal.comparison.forecast.model_dump() if proposal.comparison.forecast else None,
                                     training_end=train.dates[-1], execution_date=later.dates[0],
                                     first_scored_return=later.dates[1], test_end=later.dates[-1],
                                     test_returns=after.return_observations,
                                     cash_change=after.cash_weight_percent - baseline.cash_weight_percent,
                                     lower_volatility=lower, retained_preference_rule=kept_rule,
                                     later_changes=change))
        print(f"Finished held-out period {year}", flush=True)
    summary = {"decisions": len(decisions), "no_change": sum(x["proposals"] == 0 for x in decisions),
               "evaluated_suggestions": len(rows),
               "lower_volatility": sum(x["lower_volatility"] for x in rows),
               "retained_preference_rule": sum(x["retained_preference_rule"] for x in rows),
               "lower_cumulative_return": sum(x["later_changes"]["cumulative_return"] < 0 for x in rows),
               "increased_cash": sum(x["cash_change"] > .01 for x in rows),
               "by_goal": {goal: {"tested": sum(x["goal"] == goal for x in rows),
                    "retained_rule": sum(x["goal"] == goal and x["retained_preference_rule"] for x in rows)}
                    for goal in ("learn", "growth", "preserve")}}
    output = Path(__file__).resolve().parents[1] / "docs" / "forecast-chronological-check.json"
    output.write_text(json.dumps(dict(summary=summary, decisions=decisions, results=rows,
        limitations="Selected surviving assets, 4 illustrative portfolios, overlapping scenarios, daily rebalancing, no costs/taxes, zero-return cash and unverified price adjustments; 2017 ends 10 November; not a suitability or profitability validation"), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
