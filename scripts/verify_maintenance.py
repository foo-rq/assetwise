"""Compare fixed recommendations under realistic maintenance without selecting a winning schedule"""

import hashlib
import json
from pathlib import Path
from statistics import mean

from scripts.import_adjusted_history import parse_adjusted
from scripts.verify_long_term import performance


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/maintenance-20261029"
CADENCES = ("daily", "monthly", "quarterly", "semiannual", "annual", "hold")
FEES = (0, .001)


def read(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    inputs = ("docs/candidate-reserves-v1/frozen.json", "docs/profile-horizons-v2/frozen.json",
              "docs/reserves-across-dates-v1/frozen.json")
    hashes = {name: digest(ROOT / name) for name in inputs}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    # Commit the comparison rules before scoring; no schedule will be selected from its later return
    protocol = dict(inputs=hashes, cadences=CADENCES, fees=FEES,
        cases={"fixed_2017": 784, "across_dates": 459},
        measures="All cases, wealth versus unchanged and previous recommendations, drawdown breaches and fee sensitivity",
        accounting="Exact post-fee target trades; calendar-close maintenance affects subsequent returns only",
        limitations="Previously inspected selected-universe development cases; no new untouched validation; cash earns 0%; taxes excluded")
    (OUTPUT / "protocol.json").write_text(json.dumps(protocol, indent=2) + "\n", encoding="utf-8")
    current, previous, across = (read(name) for name in inputs)
    source_metadata = read("docs/candidate-reserves-v1/results.json")["sources"]
    observations = {}
    for symbol, metadata in source_metadata.items():
        path = ROOT / "docs/walk-forward-corrected-v2" / f"{symbol}.json"
        assert digest(path) == metadata["sha256"] == across["sources"][symbol]["sha256"]
        observations[symbol] = parse_adjusted(path.read_bytes(), symbol, end="2026-10-27")
    dates = sorted(observations["SPY"])
    assert all(sorted(values) == dates for values in observations.values())
    key = lambda d: (d["portfolio"], d["profile"]["horizon_years"], d["profile"]["max_loss_percent"])
    earlier = {key(d): d for d in previous["decisions"]}
    assert len(earlier) == len(current["decisions"]) == 784
    cases = []
    for d in current["decisions"]:
        years = int(d["profile"]["horizon_years"])
        end = min(f"{2017 + years}-11-13", dates[-1])
        cases.append(dict(cohort="fixed_2017", portfolio=d["portfolio"], period=years,
            limit=d["profile"]["max_loss_percent"], start="2017-11-13", end=end,
            original=d["original"], previous=earlier[key(d)]["proposed"], current=d["proposed"]))
    for d in across["decisions"]:
        start = dates.index(d["execution"])
        assert d["training_end"] < dates[start]
        cases.append(dict(cohort="across_dates", portfolio=d["portfolio"], period=d["evaluation_year"],
            limit=20, start=dates[start], end=dates[start + 252], original=d["original"],
            previous=d["choices"]["production"]["weights"], current=d["choices"]["candidate_reserves"]["weights"]))
    assert len(cases) == 1243
    rows = []
    for number, case in enumerate(cases, 1):
        selected = [day for day in dates if case["start"] <= day <= case["end"]]
        symbols = set(case["original"]) | set(case["previous"]) | set(case["current"])
        prices = {s: [observations[s][day] for day in selected] for s in symbols}
        for cadence in CADENCES:
            for fee in FEES:
                outcomes = {name: performance(selected, prices, case[name], cadence, fee, case["original"])
                            for name in ("original", "previous", "current")}
                rows.append(dict(**case, cadence=cadence, fee=fee, outcomes=outcomes))
        if number % 100 == 0:
            print(f"Evaluated {number}/{len(cases)} fixed decisions", flush=True)
    summaries = []
    for cohort in ("fixed_2017", "across_dates"):
        periods = sorted({r["period"] for r in rows if r["cohort"] == cohort})
        for period in (None, *periods):
            for cadence in CADENCES:
                for fee in FEES:
                    group = [r for r in rows if r["cohort"] == cohort and r["cadence"] == cadence
                             and r["fee"] == fee and (period is None or r["period"] == period)]
                    budgeted = [r for r in group if r["limit"] is not None]
                    delta = [r["outcomes"]["current"]["cumulative_return"] -
                             r["outcomes"]["previous"]["cumulative_return"] for r in group]
                    summaries.append(dict(cohort=cohort, period=period, cadence=cadence, fee=fee,
                        cases=len(group), budgeted=len(budgeted),
                        higher_wealth=sum(d > 1e-12 for d in delta), lower_wealth=sum(d < -1e-12 for d in delta),
                        mean_wealth_change=mean(delta),
                        breaches={name: sum(r["outcomes"][name]["maximum_drawdown"] > r["limit"] / 100 + 1e-12
                                           for r in budgeted) for name in ("original", "previous", "current")},
                        beats_original=sum(r["outcomes"]["current"]["cumulative_return"] >
                                           r["outcomes"]["original"]["cumulative_return"] + 1e-12 for r in group)))
    assert all(digest(ROOT / name) == value for name, value in hashes.items())
    report = dict(protocol=protocol, sources=source_metadata,
        code_sha256={name: digest(ROOT / name) for name in ("scripts/verify_maintenance.py", "scripts/verify_long_term.py")},
        summaries=summaries, rows=rows)
    (OUTPUT / "results.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    lines = ["# Fixed-choice maintenance sensitivity", "", protocol["limitations"], "",
        "Every schedule uses the same frozen choices. Fees are assumed, not observed. No-change cases remain included.", "",
        "| Cohort | Schedule | Cost | Cases | Higher wealth vs previous | Lower wealth vs previous | Mean change | Budgeted cases | Previous breaches | Current breaches | Beats unchanged |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for s in summaries:
        if s["period"] is None:
            lines.append(f'| {s["cohort"]} | {s["cadence"]} | {s["fee"]:.1%} | {s["cases"]} | '
                f'{s["higher_wealth"]} | {s["lower_wealth"]} | {s["mean_wealth_change"]:.3%} | '
                f'{s["budgeted"]} | {s["breaches"]["previous"]} | {s["breaches"]["current"]} | {s["beats_original"]} |')
    lines += ["", "Mean change is a difference in cumulative return, in percentage points. Full period breakdowns and every outcome remain in results.json. The 9-year requested horizon is incomplete. Earlier reports retain their original approximate-fee results; this report uses corrected self-financing fees.", ""]
    (OUTPUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved {len(rows)} policy rows covering all {len(cases)} decisions", flush=True)


if __name__ == "__main__":
    main()
