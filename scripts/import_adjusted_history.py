"""Rebuild the 2017 snapshot from retained Yahoo adjusted-price responses"""

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from scripts.import_history import ASSETS, START, END, OUTPUT


def parse_adjusted(raw, symbol, start=START, end=END):
    chart = json.loads(raw)["chart"]
    if chart.get("error") or len(chart.get("result") or []) != 1:
        raise ValueError("Missing price response")
    data = chart["result"][0]
    if data["meta"].get("symbol") != symbol or data["meta"].get("currency") != "USD":
        raise ValueError("Price symbol or currency does not match")
    dates = [datetime.fromtimestamp(t, timezone.utc).date().isoformat() for t in data["timestamp"]]
    adjusted = data["indicators"]["adjclose"][0]["adjclose"]
    closes = data["indicators"]["quote"][0]["close"]
    if dates != sorted(set(dates)) or len(dates) != len(adjusted) or len(dates) != len(closes):
        raise ValueError("Prices must have unique aligned dates")
    if any(v is None or not math.isfinite(v) or v <= 0 for v in adjusted + closes):
        raise ValueError("Prices must be finite and positive")
    distributions = {}
    for kind in ("dividends", "capitalGains"):
        for event in data.get("events", {}).get(kind, {}).values():
            day = datetime.fromtimestamp(event["date"], timezone.utc).date().isoformat()
            amount = event["amount"]
            if not math.isfinite(amount) or amount < 0:
                raise ValueError("Distributions must be finite and nonnegative")
            distributions[day] = distributions.get(day, 0) + amount
    for i in range(1, len(dates)):
        # Quote closes already account for splits; cash distributions explain the remaining adjustment
        factor = (adjusted[i] / adjusted[i - 1]) / (closes[i] / closes[i - 1])
        amount = distributions.get(dates[i], 0)
        if amount >= closes[i - 1]:
            raise ValueError("Distribution exceeds the preceding close")
        expected = 1 / (1 - amount / closes[i - 1])
        if abs(factor / expected - 1) > .0001:
            raise ValueError(f"Unexplained adjustment on {symbol} {dates[i]}")
    return {day: value for day, value in zip(dates, adjusted) if start <= day <= end}


def rebuild(source):
    paths, provenance = {}, []
    for symbol, name, _ in ASSETS:
        raw = (source / f"{symbol}.json").read_bytes()
        paths[symbol] = parse_adjusted(raw, symbol)
        provenance.append(dict(symbol=symbol, name=name, file=f"{symbol}.json",
                               sha256=hashlib.sha256(raw).hexdigest(),
                               source_url=f"https://finance.yahoo.com/quote/{symbol}/history/"))
    dates = list(paths["SPY"])
    benchmark = json.loads((OUTPUT / "benchmark.json").read_text(encoding="utf-8"))
    if not dates or dates[0] != START or dates[-1] != END or dates[1:] != benchmark["dates"]:
        raise ValueError("Prices must match the full dated benchmark calendar")
    if any(list(values) != dates for values in paths.values()):
        raise ValueError("Every asset must cover exactly the same dates")
    snapshot = dict(dates=dates, assets=[dict(symbol=s, name=n, index_values=list(paths[s].values()))
                                       for s, n, _ in ASSETS])
    payload = (json.dumps(snapshot, separators=(",", ":")) + "\n").encode()
    manifest = dict(dataset_version="archive-2017-v2", source_url="https://finance.yahoo.com/",
                    publisher="Yahoo Finance", source_version="retained responses retrieved 2026-10-28",
                    licence="Provider terms apply; not the previous archive's CC0 licence",
                    retrieved_on="2026-10-28", start=START, end=END, observations=len(dates),
                    adjustment="Provider adjusted closes for splits and distributions",
                    limitations="Selected surviving assets; revised historical data, not a point-in-time market universe",
                    files=provenance, snapshot_sha256=hashlib.sha256(payload).hexdigest())
    # Validate all files before replacing either input; runtime checksum rejects an interrupted pair
    (OUTPUT / "historical.json").write_bytes(payload)
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Validated {len(paths)} assets on {len(dates)} matching dates")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Directory containing the retained provider JSON responses")
    rebuild(parser.parse_args().source)
