from decimal import Decimal
from math import fsum, isfinite, sqrt
from statistics import StatisticsError, correlation, mean, stdev
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.history import History
from backend.forecast import ForecastComparison


class AllocationRequest(BaseModel):
    # Reject misspelled fields rather than quietly treating the request as all cash
    model_config = ConfigDict(extra="forbid")

    # Inputs are percentages, so 25 means 25% rather than a decimal weight of 0.25
    # Strict numbers reject booleans and strings, while finite values exclude NaN and infinity
    weights: dict[str, Annotated[
        float, Field(strict=True, ge=0, le=100, allow_inf_nan=False)
    ]]

    @model_validator(mode="after")
    def check_total(self) -> "AllocationRequest":
        # Decimal summation avoids rejecting ordinary percentages due to binary rounding
        total = sum(Decimal(str(weight)) for weight in self.weights.values())
        if total > 100:
            raise ValueError("Weights must total at most 100%")
        return self


class AssetCorrelation(BaseModel):
    # Return each distinct pair once, with identifiers rather than positional matrix labels
    asset_a: str
    asset_b: str

    # A constant return series has undefined correlation, not a correlation of 0
    value: float | None
    unavailable_reason: str | None = None


class PortfolioAnalysis(BaseModel):
    source: Literal["simulated", "historical"] = "simulated"
    dates: list[str] = []
    value_basis: str = "Simulated index"
    source_url: str | None = None
    dataset_version: str
    notice: str

    # These assumptions travel with the results when the interface requests them
    rebalancing: str = "Constant weights, rebalanced every session"
    cash_assumption: str = "Unallocated cash earns 0%"
    exclusions: str = "Trading costs and taxes excluded"
    periods_per_year: int = 252
    return_observations: int
    cash_weight_percent: float

    # Every return and volatility below is a decimal, so 0.05 represents 5%
    result_units: str = "Decimal returns and volatility"
    session_returns: list[float]
    cumulative_return: float
    annualised_arithmetic_return: float
    annualised_volatility: float

    # This dimensionless ratio uses daily excess returns, not return divided by portfolio volatility
    sharpe_ratio: float | None = None
    sharpe_unavailable_reason: str | None = None
    benchmark_source: str | None = None
    benchmark_basis: str | None = None
    exposure: dict | None = None
    forecast: ForecastComparison | None = None

    # Include the initial value so a loss in the first session counts as a drawdown
    portfolio_index: list[float]

    # Drawdowns are positive loss fractions, so 0.2 means 20% below the prior peak
    drawdowns: list[float]
    maximum_drawdown: float

    # Only positive-weight assets appear, with cash excluded because its return is constant
    asset_correlations: list[AssetCorrelation]


def analyse_portfolio(
    history: History, allocation: AllocationRequest
) -> PortfolioAnalysis:
    # Session positions stay consecutive for both sources; historical dates are validated separately
    if len(history.sessions) < 3:
        raise ValueError("At least 3 index observations are needed for sample volatility")
    if history.sessions != list(range(len(history.sessions))):
        raise ValueError("Session positions must be consecutive and start at 0")

    if history.source == "historical":
        # Never let a real-data label pass through with missing or misaligned calendar dates
        from datetime import date
        dates = history.dates
        if (len(dates) != len(history.sessions) or dates != sorted(set(dates))
                or any(date.fromisoformat(value).isoformat() != value for value in dates)):
            raise ValueError("Historical dates must be unique, ordered and aligned")

    # Duplicate identifiers would otherwise silently overwrite one asset's history
    assets = {asset.symbol: asset for asset in history.assets}
    if len(assets) != len(history.assets):
        raise ValueError("Asset identifiers must be unique")
    if allocation.weights.keys() - assets.keys():
        raise ValueError("Unknown asset identifier in weights")

    weighted_returns = []

    # Retain unweighted returns for correlation so allocation size cannot distort the inputs
    asset_returns = {}
    for symbol, percent in allocation.weights.items():
        # A zero-weight holding contributes nothing and needs no usable history
        if percent == 0:
            continue
        values = assets[symbol].index_values
        if len(values) != len(history.sessions):
            raise ValueError("Held assets must have a value for every session")
        if any(not isfinite(value) or value <= 0 for value in values):
            raise ValueError("Index values must be finite and greater than 0")

        # Divide each new index by its previous value to get that session's simple return
        # Applying the same weight every session assumes daily rebalancing, not buy-and-hold
        returns = [
            after / before - 1
            for before, after in zip(values, values[1:])
        ]
        if any(not isfinite(value) for value in returns):
            raise ValueError("Index changes are too large to calculate")
        asset_returns[symbol] = returns
        weighted_returns.append([value * (percent / 100) for value in returns])

    # An empty allocation is all cash, so fsum of no contributions gives 0 each session
    session_returns = [
        fsum(series[index] for series in weighted_returns)
        for index in range(len(history.sessions) - 1)
    ]

    # Compounding describes the whole sample, while mean times 252 is an arithmetic estimate
    # Neither quantity predicts future returns; results follow the supplied data basis
    portfolio_index = [1.0]
    drawdowns = [0.0]
    peak = 1.0
    for value in session_returns:
        # Grow the same rebalanced portfolio used by the return and volatility calculations
        current = portfolio_index[-1] * (1 + value)
        if not isfinite(current) or current <= 0:
            raise ValueError("Portfolio path exceeds the supported numeric range")
        portfolio_index.append(current)

        # Update the running peak before measuring loss, so a new high has zero drawdown
        peak = max(peak, current)
        drawdowns.append(1 - current / peak)

    cumulative = portfolio_index[-1] - 1
    annual_mean = mean(session_returns) * 252

    # Sample standard deviation uses n-1 and includes how the assets move together
    # For aligned constant weights this equals sqrt(w' sample_covariance w)
    # Multiplying by sqrt(252) is our annualisation convention, not a guarantee about markets
    annual_volatility = stdev(session_returns) * sqrt(252)
    sharpe = None
    sharpe_reason = "No matching Treasury-bill reference series"
    if history.risk_free_returns:
        # Reject partial or non-finite references instead of truncating zip or assuming zero rates
        if (len(history.risk_free_returns) != len(session_returns)
                or any(not isfinite(value) for value in history.risk_free_returns)):
            raise ValueError("Risk-free returns must be finite and match every session return")
        excess = [value - reference for value, reference in
                  zip(session_returns, history.risk_free_returns)]
        excess_volatility = stdev(excess)
        if annual_volatility < 1e-12 or excess_volatility < 1e-12:
            # A constant portfolio or differential path cannot support a useful risk-adjusted ratio
            sharpe_reason = "Undefined for a constant portfolio or constant excess returns"
        else:
            sharpe = mean(excess) / excess_volatility * sqrt(252)
            sharpe_reason = None
    if not all(isfinite(value) for value in (cumulative, annual_mean, annual_volatility)):
        raise ValueError("Calculated results exceed the supported numeric range")

    # Sort identifiers so dictionary insertion order does not change the pair ordering
    symbols = sorted(asset_returns)
    pairs = []
    for index, asset_a in enumerate(symbols):
        for asset_b in symbols[index + 1:]:
            # Pearson correlation uses matched session returns, never index levels
            try:
                value = correlation(asset_returns[asset_a], asset_returns[asset_b])
                reason = None
            except StatisticsError:
                # Lengths were checked above, leaving constant returns as the undefined case
                value = None
                reason = "Correlation is undefined when an asset has constant returns"
            pairs.append(AssetCorrelation(
                asset_a=asset_a, asset_b=asset_b,
                value=value, unavailable_reason=reason,
            ))

    # Calculate the remainder without changing any allocation supplied by the user
    invested = sum(Decimal(str(weight)) for weight in allocation.weights.values())
    # Composition is a separate dated snapshot and never changes calculated historical returns
    exposure = None
    if history.source == "historical":
        from backend.exposure import portfolio_exposure
        exposure = portfolio_exposure(allocation.weights)
    return PortfolioAnalysis(
        source=history.source, dates=history.dates, value_basis=history.value_basis,
        source_url=history.source_url, dataset_version=history.dataset_version,
        notice=history.notice,
        return_observations=len(session_returns),
        cash_weight_percent=float(Decimal(100) - invested),
        session_returns=session_returns,
        cumulative_return=cumulative,
        annualised_arithmetic_return=annual_mean,
        annualised_volatility=annual_volatility,
        sharpe_ratio=sharpe, sharpe_unavailable_reason=sharpe_reason,
        benchmark_source=history.benchmark_source,
        benchmark_basis=history.benchmark_basis,
        exposure=exposure,
        portfolio_index=portfolio_index,
        drawdowns=drawdowns,
        maximum_drawdown=max(drawdowns),
        asset_correlations=pairs,
    )
