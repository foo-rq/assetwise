"""Dated annual estimates and compounded growth over the investor's chosen period"""

from math import expm1, isfinite, sqrt

import numpy as np
from pydantic import BaseModel

from backend.history import History


class ForecastMetrics(BaseModel):
    expected_annual_return: float
    estimated_annual_volatility: float
    approximate_annual_log_growth: float
    # Decimal compounded growth over the chosen period, separate from the annual return estimate
    projected_growth: float | None = None
    projected_growth_range: tuple[float, float] | None = None
    # A conservative peak-to-trough loss screen covers the path, not just the final value
    drawdown_screen: float | None = None


class ForecastComparison(BaseModel):
    model: str
    validation_windows: int
    validation_return_rmse: dict[str, float]
    as_of: str
    training_start: str
    observations: int
    horizon_sessions: int | None = None
    horizon_years: float | None = None
    current: ForecastMetrics
    proposed: ForecastMetrics
    return_change_range_90: tuple[float, float]
    return_change_model_range: tuple[float, float] | None = None
    # Proposed wealth divided by current wealth minus 1, not a difference of percentage returns
    projected_relative_wealth_range: tuple[float, float] | None = None
    projection_notice: str | None = None
    mean_shrinkage: float
    covariance_shrinkage: float
    assumptions: str = (
        "Annual estimates use 252 sessions; daily rebalancing; cash earns 0%; no costs or taxes. "
        "Values are nominal and exclude inflation. "
        "Ranges use a normal-model approximation; real-world coverage is unverified. "
        "Changing market conditions and data errors can invalidate estimates."
    )


def fit_forecast(history: History):
    # A dated model cannot quietly train on later prices or silently omit missing observations
    if history.source != "historical" or len(history.dates) < 253:
        return None
    if history.dates != sorted(set(history.dates)) or len(history.dates) != len(history.sessions):
        raise ValueError("Forecast dates must be ordered and aligned")
    symbols = [asset.symbol for asset in history.assets]
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("Forecast assets must be unique")
    if any(len(asset.index_values) != len(history.dates) for asset in history.assets):
        raise ValueError("Forecast prices must cover every training date")
    prices = np.array([asset.index_values for asset in history.assets], dtype=float).T
    if not np.all(np.isfinite(prices)) or np.any(prices <= 0):
        raise ValueError("Forecast prices must be finite and positive")
    returns = prices[1:] / prices[:-1] - 1
    if not np.all(np.isfinite(returns)):
        raise ValueError("Forecast returns exceed numeric limits")
    market = symbols.index("SPY") if "SPY" in symbols else None
    # Distinct traded proxies stop the equity market from being the only driver of bond/gold estimates
    factors = [symbols.index(symbol) for symbol in ("SPY", "EFA", "EEM", "IEF", "LQD", "GLD", "VNQ")
               if symbol in symbols]
    factors = factors if len(factors) >= 2 else None
    names = (("shrinkage", "asset_factors", "asset_blend") if factors else
             ("shrinkage", "market_factor", "equal_blend") if market is not None else ("shrinkage",))
    errors = {name: [] for name in names}
    # Each validation year follows its training data and finishes before the decision date
    for stop in range(504, len(returns) - 252, 252):
        estimates, _, _, _ = estimate_moments(returns[:stop], market, factors)
        # The model sees the fitting close, trades at the next close and earns returns only after that
        completed = returns[stop + 1:stop + 253]
        realised = completed.mean(axis=0) * 252
        for name in errors:
            errors[name].append(float(np.square(estimates[name] * 252 - realised).mean()))
    scores = {name: float(np.sqrt(np.mean(values))) for name, values in errors.items() if values}
    selected = min(scores, key=scores.get) if scores else "shrinkage"
    estimates, covariance, mean_shrinkage, covariance_shrinkage = estimate_moments(returns, market, factors)
    return dict(symbols=symbols, expected=estimates[selected], covariance=covariance,
                return_scenarios={name: estimates[name] for name in ("shrinkage", "asset_factors", "market_factor")
                                  if name in estimates},
                count=len(returns), start=history.dates[0], end=history.dates[-1],
                mean_shrinkage=mean_shrinkage, covariance_shrinkage=covariance_shrinkage,
                model=("asset-classes-next-close-v2:" if factors else "chronological-next-close-v3:") + selected,
                validation_windows=len(errors["shrinkage"]),
                validation_return_rmse=scores)


def estimate_moments(returns, market=None, factors=None):
    count, size = returns.shape
    mean = returns.mean(axis=0)
    centered = returns - mean
    sample = centered.T @ centered / count
    target = np.eye(size) * np.trace(sample) / size
    # Ledoit-Wolf identity shrinkage uses observed noise rather than a hindsight-tuned coefficient
    distance = float(np.square(sample - target).sum())
    noise = max(0.0, float(np.square(np.square(centered).sum(axis=1)).mean()
                           - np.square(sample).sum())) / count
    covariance_shrinkage = min(1.0, noise / distance) if distance > 0 else 1.0
    covariance = (1 - covariance_shrinkage) * sample + covariance_shrinkage * target
    # Jorion-style mean shrinkage pulls noisy winners towards a covariance-weighted common mean
    inverse = np.linalg.pinv(covariance, hermitian=True)
    ones = np.ones(size)
    denominator = float(ones @ inverse @ ones)
    anchor = float(ones @ inverse @ mean / denominator) if denominator > 0 else float(mean.mean())
    deviations = mean - anchor
    mean_shrinkage = (size + 2) / (size + 2 + count * max(0.0, float(deviations @ inverse @ deviations)))
    estimates = {"shrinkage": (1 - mean_shrinkage) * mean + mean_shrinkage * anchor}
    if factors:
        # Covariance-based loadings share class returns without assuming persistent asset-specific alpha
        factor_covariance = covariance[np.ix_(factors, factors)]
        loadings = covariance[:, factors] @ np.linalg.pinv(factor_covariance, hermitian=True)
        estimates["asset_factors"] = loadings @ mean[factors]
        estimates["asset_blend"] = (estimates["shrinkage"] + estimates["asset_factors"]) / 2
    elif market is not None:
        # The market-factor alternative assumes zero cash yield and no persistent stock-specific alpha
        variance = covariance[market, market]
        estimates["market_factor"] = covariance[:, market] / variance * mean[market] if variance > 0 else np.zeros(size)
        # Fixed equal weights avoid fitting another coefficient to just a few validation years
        estimates["equal_blend"] = (estimates["shrinkage"] + estimates["market_factor"]) / 2
    return estimates, covariance, mean_shrinkage, covariance_shrinkage


def _compounded_projection(center, spread):
    # Report unavailable values when exponentiation exceeds numeric limits rather than cap the result
    try:
        growth = expm1(center)
        interval = (expm1(center - spread), expm1(center + spread))
    except (OverflowError, ValueError):
        return None, None
    if not all(isfinite(value) for value in (growth, *interval)):
        return None, None
    return growth, interval


def drawdown_screen(log_growth, volatility, years, training_years):
    # Under a Brownian log-price model, zero-drift drawdown has the law of maximum absolute Brownian motion
    # Reflection bounds its path tail by 4 * (1 - Phi(z)); reserve 2.5% each for this tail and mean uncertainty
    lower_drift = log_growth - 1.959963984540054 * volatility / sqrt(training_years)
    log_loss = 2.497705474412374 * volatility * sqrt(years) + max(0.0, -lower_drift) * years
    # Positive drift gets no safety credit; jumps, changing volatility and serial dependence remain outside this model
    return -expm1(-log_loss)


def forecast_comparison(model, current, proposed, horizon_years=None):
    # The public profile validates this too; direct calculation callers receive the same bounds
    if horizon_years is not None and (isinstance(horizon_years, bool) or
            not isinstance(horizon_years, (int, float)) or not isfinite(horizon_years) or
            not .25 <= horizon_years <= 40):
        raise ValueError("Investment period must be a number between 0.25 and 40 years")
    symbols = model["symbols"]
    if (current.keys() | proposed.keys()) - set(symbols):
        raise ValueError("Unknown forecast asset")
    before = np.array([current.get(symbol, 0) / 100 for symbol in symbols])
    after = np.array([proposed.get(symbol, 0) / 100 for symbol in symbols])
    # Mean-estimation uncertainty grows with the square of time, even for long investment periods
    uncertainty_time = (horizon_years + horizon_years ** 2 / (model["count"] / 252)
                        if horizon_years is not None else None)

    def estimate(weights):
        annual_return = float(weights @ model["expected"] * 252)
        variance = float(weights @ model["covariance"] @ weights * 252)
        if not isfinite(annual_return) or not isfinite(variance):
            raise ValueError("Annual forecast exceeds the supported numeric range")
        variance = max(0.0, variance)
        log_growth = annual_return - variance / 2
        growth = interval = path_loss = None
        if horizon_years is not None:
            # The center is median compounded growth under this lognormal approximation, not a promised mean
            center = horizon_years * log_growth
            spread = 1.6448536269514722 * sqrt(variance * uncertainty_time)
            growth, interval = _compounded_projection(center, spread)
            path_loss = drawdown_screen(log_growth, sqrt(variance), horizon_years, model["count"] / 252)
        return ForecastMetrics(expected_annual_return=annual_return,
                               estimated_annual_volatility=sqrt(variance),
                               approximate_annual_log_growth=log_growth,
                               projected_growth=growth, projected_growth_range=interval,
                               drawdown_screen=path_loss)

    current_estimate, proposed_estimate = estimate(before), estimate(after)
    difference = after - before
    # Compare paired portfolios so shared market exposure cancels in the uncertainty calculation
    variance = float(difference @ model["covariance"] @ difference)
    if not isfinite(variance):
        raise ValueError("Paired forecast exceeds the supported numeric range")
    variance = max(0.0, variance)
    spread = 1.6448536269514722 * np.sqrt(variance * (252 + 252 ** 2 / model["count"]))
    change = proposed_estimate.expected_annual_return - current_estimate.expected_annual_return
    # Keep disagreement between mean estimators distinct from the wider range of future outcomes
    model_changes = [float(difference @ mean * 252) for mean in model["return_scenarios"].values()]
    if not all(isfinite(value) for value in (change - spread, change + spread, *model_changes)):
        raise ValueError("Annual return comparison exceeds the supported numeric range")
    relative_interval = None
    projection_notice = None
    if horizon_years is not None:
        # Shared holdings cancel in relative wealth, so use the active portfolio's paired covariance
        relative_center = horizon_years * (proposed_estimate.approximate_annual_log_growth -
                                           current_estimate.approximate_annual_log_growth)
        relative_spread = 1.6448536269514722 * sqrt(variance * 252 * uncertainty_time)
        _, relative_interval = _compounded_projection(relative_center, relative_spread)
        if (current_estimate.projected_growth is None or proposed_estimate.projected_growth is None or
                relative_interval is None):
            projection_notice = "Some period projections are unavailable because the model exceeds numeric limits"
    assumptions = ForecastComparison.model_fields["assumptions"].default
    if horizon_years is not None:
        unit = "year" if horizon_years == 1 else "years"
        assumptions = (f"Modelled compounded growth over {horizon_years:g} {unit} uses a lognormal approximation "
                       "with uncertainty in the fitted mean. Relative wealth compares proposed/current wealth. " +
                       "The drawdown screen estimates peak-to-trough losses under the model. Actual losses can be larger. " +
                       assumptions)
    return ForecastComparison(model=model["model"], validation_windows=model["validation_windows"],
        validation_return_rmse=model["validation_return_rmse"],
        as_of=model["end"], training_start=model["start"],
        observations=model["count"], current=current_estimate, proposed=proposed_estimate,
        horizon_years=horizon_years, horizon_sessions=round(horizon_years * 252) if horizon_years is not None else None,
        projected_relative_wealth_range=relative_interval,
        projection_notice=projection_notice, assumptions=assumptions,
        return_change_range_90=(change - spread, change + spread),
        return_change_model_range=(min(model_changes), max(model_changes)),
        mean_shrinkage=model["mean_shrinkage"], covariance_shrinkage=model["covariance_shrinkage"])
