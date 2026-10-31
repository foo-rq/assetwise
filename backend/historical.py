import hashlib
import json
from datetime import date
from functools import lru_cache
from math import isfinite
from pathlib import Path

from backend.history import History

# Read and validate once per process; restart the API after deliberately rebuilding the snapshot
@lru_cache(maxsize=1)
def build_historical_history() -> History:
    # The bundled snapshot needs no network or provider key while serving the application
    folder = Path(__file__).resolve().parent / "data"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    # Refuse the legacy importer output rather than label its old prices as the corrected provider
    if manifest["dataset_version"] != "archive-2017-v2":
        raise ValueError("Install the corrected archive-2017-v2 snapshot")
    raw = (folder / "historical.json").read_bytes()

    # Hash the original bytes before decoding so a partial copy or accidental edit is rejected
    # This checks consistency with our manifest, not whether the publisher's prices are correct
    if hashlib.sha256(raw).hexdigest() != manifest["snapshot_sha256"]:
        raise ValueError("Historical snapshot checksum failed")
    data = json.loads(raw)
    dates = data["dates"]
    benchmark = json.loads((folder / "benchmark.json").read_text(encoding="utf-8"))
    if (benchmark["dates"] != dates[1:]
            or len(benchmark["returns"]) != len(dates) - 1
            or any(not isfinite(value) or not -0.01 < value < 0.01
                   for value in benchmark["returns"])):
        raise ValueError("Treasury-bill benchmark must match every return date")

    # The length check also prevents empty dates from reaching dates[0] or dates[-1]
    # ISO validation rejects impossible dates, while set/sort checks catch duplicates and disorder
    # Matching the manifest's endpoints and count detects a shortened or mismatched archive
    if (len(dates) < 252 or len(set(dates)) != len(dates) or dates != sorted(dates)
            or any(date.fromisoformat(value).isoformat() != value for value in dates)
            or dates[0] != manifest["start"] or dates[-1] != manifest["end"]
            or len(dates) != manifest["observations"]):
        raise ValueError("Historical date coverage is invalid")
    for asset in data["assets"]:
        # A missing price would shift later returns onto the wrong trading dates
        # Reject the entire path rather than align by list position after removing a gap
        if len(asset["index_values"]) != len(dates) or any(
            not isfinite(value) or value <= 0 for value in asset["index_values"]
        ):
            raise ValueError("Historical prices must cover every reference date")
    # Keep actual closes unchanged; daily ratios cancel their starting level in portfolio analysis
    # Real dates accompany integer sessions so charts can label time without changing the maths
    return History(
        source="historical", dataset_version=manifest["dataset_version"],
        notice="Historical prices ending 10 November 2017. The corrected price snapshot recalculates saved allocation weights; forecasts remain estimates.",
        value_basis="Yahoo Finance USD adjusted closes, including splits and distributions",
        time_basis="Aligned archived trading dates", source_url=manifest["source_url"],
        dates=dates, sessions=list(range(len(dates))), assets=data["assets"],
        risk_free_returns=benchmark["returns"], benchmark_source=benchmark["source_url"],
        benchmark_basis=benchmark["method"],
    )
