"""Evaluate frozen 2017 decisions as investor wealth outcomes, including no-change decisions"""

import argparse
import calendar
import hashlib
import json
from datetime import date
from math import isfinite
from pathlib import Path
from statistics import median

from backend.analytics import AllocationRequest
from scripts.import_adjusted_history import parse_adjusted
from scripts.verify_long_term import performance


ROOT = Path(__file__).resolve().parents[1]
POLICIES = (("daily", 0), ("monthly", .001), ("hold", 0))


def real_performance(nominal, inflation_factor, years):
    # Deflate terminal wealth, not the percentage return; CPI is retrospective outcome evidence only
    if not isfinite(inflation_factor) or not isfinite(years) or inflation_factor <= 0 or years <= 0:
        raise ValueError("Inflation factor and holding period must be positive")
    wealth = (1 + nominal["cumulative_return"]) / inflation_factor
    return {**nominal, "real_cumulative_return": wealth - 1,
            "real_cagr": wealth ** (1 / years) - 1,
            "terminal_nominal_per_10000": (1 + nominal["cumulative_return"]) * 10000,
            "terminal_real_per_10000": wealth * 10000}


def audit(input_path, output, prices_path, cpi_path, goal):
    raw = input_path.read_bytes()
    frozen = json.loads(raw)
    decisions = [d for d in frozen["decisions"] if d.get("goal", "growth") == goal]
    names = [d["portfolio"] for d in decisions]
    if not decisions or len(set(names)) != len(names):
        raise ValueError("Use one frozen decision per portfolio for the selected goal")
    provenance = json.loads(cpi_path.read_text(encoding="utf-8"))
    if provenance["series"] != "CUUR0000SA0":
        raise ValueError("Expected unadjusted all-items US CPI-U")
    observations = {row["month"]: row for row in provenance["observations"]}
    if any(not isfinite(row["value"]) or row["value"] <= 0 for row in observations.values()):
        raise ValueError("CPI observations must be finite and positive")
    start_cpi = observations[provenance["evaluation_start_month"]]
    end_cpi = observations[provenance["evaluation_end_month"]]
    known = observations[provenance["known_at_decision_month"]]
    if known["released"] > provenance["decision_date"] or end_cpi["released"] > provenance["verified_on"]:
        raise ValueError("CPI publication dates exceed the declared information dates")
    inflation = end_cpi["value"] / start_cpi["value"]
    year, month = map(int, end_cpi["month"].split("-"))
    last_day = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
    execution = "2017-11-13"
    if provenance["decision_date"] != "2017-11-10" or start_cpi["month"] != execution[:7]:
        raise ValueError("This audit requires the frozen November 2017 decision and matching CPI month")

    proxies = ("SPY", "EFA", "EEM", "IEF", "LQD", "GLD", "VNQ")
    equal = {symbol: 100 / len(proxies) for symbol in proxies[:-1]}
    equal[proxies[-1]] = 100 - sum(equal.values())
    benchmarks = {"SPY": {"SPY": 100}, "60_40_SPY_IEF": {"SPY": 60, "IEF": 40},
                  "equal_7_classes": equal}
    symbols = set(proxies)
    for d in decisions:
        original = AllocationRequest(weights=d["original"]).weights
        proposed = d["proposals"][0]["weights"] if d["proposals"] else original
        symbols.update(original)
        symbols.update(AllocationRequest(weights=proposed).weights)
        if d["proposals"]:
            forecast_date = d["proposals"][0].get("forecast", {}).get("as_of")
            if forecast_date is not None and forecast_date != provenance["decision_date"]:
                raise ValueError("A proposal was fitted at a different decision date")
    paths, sources = {}, {}
    for symbol in sorted(symbols):
        # The importer validates source identity, adjusted prices and corporate-action consistency
        source_path = prices_path / f"{symbol}.json"
        source = source_path.read_bytes()
        paths[symbol] = parse_adjusted(source, symbol, end=last_day)
        sources[symbol] = {"path": str(source_path.resolve()), "sha256": hashlib.sha256(source).hexdigest()}
    dates = [day for day in paths["SPY"] if execution <= day <= last_day]
    if not dates or dates[0] != execution or dates[-1][:7] != end_cpi["month"]:
        raise ValueError("Prices do not cover the execution date and final CPI month")
    for symbol, path in paths.items():
        if [day for day in path if execution <= day <= last_day] != dates:
            raise ValueError(f"Unmatched evaluation calendar for {symbol}")
    prices = {symbol: [path[day] for day in dates] for symbol, path in paths.items()}
    years = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days / 365.25
    rows = []
    for cadence, fee in POLICIES:
        for d in decisions:
            original = d["original"]
            proposed = d["proposals"][0]["weights"] if d["proposals"] else original
            allocations = {"unchanged": original, "recommendation": proposed, **benchmarks}
            outcomes = {}
            for name, weights in allocations.items():
                # Every alternative pays for its transition from the same existing portfolio
                nominal = performance(dates, prices, weights, cadence, fee, original)
                outcomes[name] = real_performance(nominal, inflation, years)
            before, after = outcomes["unchanged"], outcomes["recommendation"]
            delta = {key: after[key] - before[key] for key in before}
            lower = delta["annualised_volatility"] <= -.001
            strict_joint = lower and delta["annualised_arithmetic_return"] >= -1e-12 and delta["cumulative_return"] >= -1e-12
            rows.append({"portfolio": d["portfolio"], "cadence": cadence, "fee_per_dollar_traded": fee,
                         "actionable": bool(d["proposals"]), "original": original, "proposed": proposed,
                         "outcomes": outcomes, "changes_vs_unchanged": delta,
                         "lower_volatility": lower,
                         "wealth_and_lower_volatility": lower and delta["cumulative_return"] >= -1e-12,
                         "strict_joint_improvement": strict_joint})
    summary = {}
    for cadence, fee in POLICIES:
        selected = [r for r in rows if r["cadence"] == cadence]
        summary[cadence] = {
            "decisions": len(selected), "actionable": sum(r["actionable"] for r in selected),
            "no_change": sum(not r["actionable"] for r in selected), "fee_per_dollar_traded": fee,
            "nominal_positive": sum(r["outcomes"]["recommendation"]["cumulative_return"] > 0 for r in selected),
            "real_positive": sum(r["outcomes"]["recommendation"]["real_cumulative_return"] > 0 for r in selected),
            "unchanged_real_positive": sum(r["outcomes"]["unchanged"]["real_cumulative_return"] > 0 for r in selected),
            "issued_real_positive": sum(r["actionable"] and r["outcomes"]["recommendation"]["real_cumulative_return"] > 0 for r in selected),
            "beats_unchanged_wealth": sum(r["changes_vs_unchanged"]["cumulative_return"] > 1e-12 for r in selected),
            "lower_volatility": sum(r["lower_volatility"] for r in selected),
            "wealth_and_lower_volatility": sum(r["wealth_and_lower_volatility"] for r in selected),
            "strict_joint_improvement": sum(r["strict_joint_improvement"] for r in selected),
            "median_real_return": median(r["outcomes"]["recommendation"]["real_cumulative_return"] for r in selected),
            "worst_real_return": min(r["outcomes"]["recommendation"]["real_cumulative_return"] for r in selected),
            "worst_drawdown": max(r["outcomes"]["recommendation"]["maximum_drawdown"] for r in selected),
            "benchmark_comparisons": {name: {
                "benchmark_real_positive": sum(r["outcomes"][name]["real_cumulative_return"] > 0 for r in selected),
                "recommendation_beats_terminal_wealth": sum(r["outcomes"]["recommendation"]["cumulative_return"] > r["outcomes"][name]["cumulative_return"] + 1e-12 for r in selected),
                "recommendation_lower_drawdown": sum(r["outcomes"]["recommendation"]["maximum_drawdown"] < r["outcomes"][name]["maximum_drawdown"] - 1e-12 for r in selected)
            } for name in benchmarks}
        }
    report = {"input": str(input_path.resolve()), "input_sha256": hashlib.sha256(raw).hexdigest(),
              "goal": goal, "execution": dates[0], "end": dates[-1], "return_observations": len(dates) - 1,
              "inflation_factor": inflation, "cpi": provenance, "sources": sources,
              "summary": summary, "rows": rows,
              "limitations": "Retrospective selected-universe development evidence, not an independent forecast test; monthly CPI approximates the purchasing-power endpoints; USD nominal/US real values; dividends reinvested; cash earns 0%; taxes and FX excluded; 10bp trading costs are assumed; drawdowns and volatility remain nominal because only CPI endpoints are supplied"}
    output.mkdir(parents=True, exist_ok=True)
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (output / "report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return report


def render_report(report):
    total = report["summary"]["daily"]["decisions"]
    lines = ["# Investor outcomes from frozen 2017 decisions", "",
             f"Execution: {report['execution']}. Evaluation ends {report['end']}, matching the latest CPI month used. All {total} starting decisions remain in the report, including no change.", "",
             "Positive nominal wealth, positive purchasing-power growth and outperforming the existing portfolio answer different questions. The earlier simultaneous risk/return rule is retained as a separate diagnostic.", "",
             "| Policy | Nominal growth | Real growth | Unchanged real growth | Better terminal wealth than unchanged | Wealth retained with lower volatility | Old joint rule |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    labels = {"daily": "Daily maintenance, no fees", "monthly": "Monthly maintenance, 0.1% per dollar traded", "hold": "Hold after the initial trade, no fees"}
    for policy, values in report["summary"].items():
        counts = [values[k] for k in ("nominal_positive", "real_positive", "unchanged_real_positive", "beats_unchanged_wealth", "wealth_and_lower_volatility", "strict_joint_improvement")]
        lines.append(f"| {labels[policy]} | " + " | ".join(f"{value}/{total}" for value in counts) + " |")
    lines += ["", "Each benchmark pays any initial sale/purchase costs from the same original allocation. SPY is an equity reference, 60/40 SPY/IEF is a transparent stock/bond reference and equal weights across SPY/EFA/EEM/IEF/LQD/GLD/VNQ is a simple diversification reference. They are not matched-risk recommendations for every investor.", "",
              "| Monthly-cost comparison | Benchmark portfolios with real growth | Recommendations with higher terminal wealth | Recommendations with lower drawdown |",
              "|---|---:|---:|---:|"]
    for name, values in report["summary"]["monthly"]["benchmark_comparisons"].items():
        lines.append(f"| {name} | {values['benchmark_real_positive']}/{total} | {values['recommendation_beats_terminal_wealth']}/{total} | {values['recommendation_lower_drawdown']}/{total} |")
    lines += ["", "## Every starting portfolio under monthly maintenance and assumed costs", "",
              "Amounts below start from USD 10,000. Real terminal wealth uses November 2017 purchasing power. Drawdown is the worst nominal peak-to-trough loss along the path.", "",
              "| Starting portfolio | Action | Nominal terminal value | Real terminal value | Real CAGR | Drawdown | Real gain over unchanged |",
              "|---|---|---:|---:|---:|---:|---:|"]
    for row in report["rows"]:
        if row["cadence"] != "monthly":
            continue
        outcome = row["outcomes"]["recommendation"]
        lines.append(f"| {row['portfolio']} | {'Change' if row['actionable'] else 'No change'} | {outcome['terminal_nominal_per_10000']:,.2f} | {outcome['terminal_real_per_10000']:,.2f} | {outcome['real_cagr']:.2%} | {outcome['maximum_drawdown']:.2%} | {row['changes_vs_unchanged']['terminal_real_per_10000']:,.2f} |")
    lines += ["", "## Inflation sources and interpretation", "",
              f"Inflation adjustment: terminal nominal wealth divided by {report['inflation_factor']:.12f}. CPI measures a month as a whole, not a specific trading date. November 2017 CPI was published after the decision and is used only for retrospective evaluation. The last CPI observation known at the decision is recorded separately and never substituted for the correct outcome base month.", ""]
    for row in report["cpi"]["observations"]:
        lines.append(f"- [{row['month']} CPI-U: {row['value']:.3f}, published {row['released']}]({row['url']})")
    lines += ["", report["limitations"], "",
              "A favorable realised outcome does not establish predictive accuracy. All comparisons use the same inspected market history. The original portfolio's outcome, simple benchmark outcomes and path losses are included so a rising market alone is not mistaken for forecasting skill.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "docs/forward-next-close-v2/frozen.json")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/investor-outcomes-2017-v1")
    parser.add_argument("--prices", type=Path, default=ROOT / "docs/walk-forward-corrected-v2")
    parser.add_argument("--cpi", type=Path, default=ROOT / "docs/investor-outcomes-cpi-source.json")
    parser.add_argument("--goal", default="growth")
    args = parser.parse_args()
    audit(args.input, args.output, args.prices, args.cpi, args.goal)
