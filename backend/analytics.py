from decimal import Decimal
from math import fsum, isfinite, prod, sqrt
from statistics import mean, stdev
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.simulation import SimulatedHistory


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


class PortfolioAnalysis(BaseModel):
    source: Literal["simulated"] = "simulated"
    dataset_version: str
    notice: str

    # These assumptions travel with the results when the interface requests them
    rebalancing: str = "Constant weights, rebalanced every synthetic session"
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


def analyse_portfolio(
    history: SimulatedHistory, allocation: AllocationRequest
) -> PortfolioAnalysis:
    # This first engine accepts aligned synthetic sessions only, never filled market gaps
    if len(history.sessions) < 3:
        raise ValueError("At least 3 index observations are needed for sample volatility")
    if history.sessions != list(range(len(history.sessions))):
        raise ValueError("Synthetic sessions must be consecutive and start at 0")

    # Duplicate identifiers would otherwise silently overwrite one asset's history
    assets = {asset.symbol: asset for asset in history.assets}
    if len(assets) != len(history.assets):
        raise ValueError("Asset identifiers must be unique")
    if allocation.weights.keys() - assets.keys():
        raise ValueError("Unknown asset identifier in weights")

    weighted_returns = []
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
            (after / before - 1) * (percent / 100)
            for before, after in zip(values, values[1:])
        ]
        if any(not isfinite(value) for value in returns):
            raise ValueError("Index changes are too large to calculate")
        weighted_returns.append(returns)

    # An empty allocation is all cash, so fsum of no contributions gives 0 each session
    session_returns = [
        fsum(series[index] for series in weighted_returns)
        for index in range(len(history.sessions) - 1)
    ]

    # Compounding describes the whole sample, while mean times 252 is an arithmetic estimate
    # Neither quantity predicts future returns or represents actual market performance
    cumulative = prod(1 + value for value in session_returns) - 1
    annual_mean = mean(session_returns) * 252

    # Sample standard deviation uses n-1 and includes how the assets move together
    # For aligned constant weights this equals sqrt(w' sample_covariance w)
    # Multiplying by sqrt(252) is our annualisation convention, not a guarantee about markets
    annual_volatility = stdev(session_returns) * sqrt(252)
    if not all(isfinite(value) for value in (cumulative, annual_mean, annual_volatility)):
        raise ValueError("Calculated results exceed the supported numeric range")

    # Calculate the remainder without changing any allocation supplied by the user
    invested = sum(Decimal(str(weight)) for weight in allocation.weights.values())
    return PortfolioAnalysis(
        dataset_version=history.dataset_version,
        notice=history.notice,
        return_observations=len(session_returns),
        cash_weight_percent=float(Decimal(100) - invested),
        session_returns=session_returns,
        cumulative_return=cumulative,
        annualised_arithmetic_return=annual_mean,
        annualised_volatility=annual_volatility,
    )
