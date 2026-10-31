from typing import Literal

from pydantic import BaseModel


class AssetHistory(BaseModel):
    # Symbols identify holdings in allocations; names are only human-readable labels
    symbol: str
    name: str

    # Position i belongs to sessions[i] and, for historical data, dates[i]
    # Synthetic test values are unitless indices; archived values are adjusted closes
    # The engine uses ratios, so it does not require both sources to start at 100
    index_values: list[float]


class History(BaseModel):
    # Both sources share aligned observations so the calculation engine has 1 contract
    source: Literal["simulated", "historical"]

    # Saved allocations keep this version so changed inputs cannot masquerade as the same sample
    dataset_version: str

    # Send limitations and measurement units with the data instead of trusting UI-only labels
    notice: str
    value_basis: str
    time_basis: str

    # Consecutive integer positions keep the existing return calculations source-independent
    sessions: list[int]

    # Synthetic sessions have no real dates or provider link; historical data must supply both
    dates: list[str] = []
    source_url: str | None = None

    # Benchmark returns align to dates[1:], since the initial price has no preceding return
    risk_free_returns: list[float] = []
    benchmark_source: str | None = None
    benchmark_basis: str | None = None

    # Every asset in this collection must use the same ordered observations
    assets: list[AssetHistory]
