from decimal import Decimal, ROUND_DOWN
from math import log1p, sqrt
from typing import Annotated

import numpy as np
from scipy.optimize import minimize
from pydantic import BaseModel, ConfigDict, Field

from backend.analytics import AllocationRequest, analyse_portfolio
from backend.comparison import ComparisonRequest, PortfolioComparison, compare_portfolios
from backend.history import History
from backend.profile import InvestorProfile
from backend.forecast import drawdown_screen, fit_forecast, forecast_comparison

Percent = Annotated[float, Field(strict=True, ge=0, le=100, allow_inf_nan=False)]


class PlanningRequest(BaseModel):
    # Reuse strict portfolio validation for the baseline; planning never assumes it is already valid
    model_config = ConfigDict(extra="forbid")
    current: AllocationRequest

    # Replace the asset's existing weight with this target before any loss-budget cash scaling
    target_percent: Annotated[float, Field(strict=True, gt=0, le=100, allow_inf_nan=False)] = 15
    max_position_percent: Annotated[float, Field(strict=True, gt=0, le=100, allow_inf_nan=False)] = 40

    # Candidates treat this as minimum cash, while optimisation holds exactly this reserve
    cash_percent: Percent = 10
    profile: InvestorProfile | None = None


class Proposal(BaseModel):
    # Keep the full allocation with the comparison so Preview can show how every position is funded
    name: str
    weights: dict[str, float]
    comparison: PortfolioComparison
    rank_score: float = 0


class CandidateResults(BaseModel):
    # Carry source/version through ranking so archived and invented estimates cannot be mixed
    source: str = "simulated"
    dataset_version: str
    minimum_reduction_percentage_points: float = 0.1

    # An empty proposal list is a valid no-change outcome rather than a calculation failure
    proposals: list[Proposal]
    evaluated: int
    notice: str = "No change is required; candidates must reduce volatility by at least 0.1 percentage points and meet your cash and position limits"
    criteria: str = "Lower historical volatility"


class Sensitivity(BaseModel):
    # Each entry is a separate fit to part of the sample, not the performance of a held-out strategy
    sample: str
    return_observations: int
    weights: dict[str, float]


class OptimisationResult(BaseModel):
    proposal: Proposal
    sensitivity: list[Sensitivity]
    objective: str = "Minimum sample variance with fixed cash and a maximum per asset"
    notice: str = "Sample-dependent demonstration, not an expected-return forecast or a capital guarantee"


def funded_weights(weights, symbol, target, preserve_cash=False):
    # Decimal arithmetic keeps repeated scaling within the allocation contract
    target = Decimal(str(target))

    # The funding pool includes all other positions plus cash, which is implicit in the 100% total
    # Exclude the candidate's existing weight because target replaces that weight rather than adds to it
    pool = sum(Decimal(str(value)) for value in weights.values()) if preserve_cash else Decimal(100)
    if target > pool:
        raise ValueError("Target would spend the protected cash reserve")
    other = pool - Decimal(str(weights.get(symbol, 0)))
    if other == 0 and target < pool:
        # A 100% single-asset portfolio has no other positions to scale using this funding rule
        raise ValueError("No other funded positions to scale")
    # For a new 15% holding, every old holding and cash are multiplied by 0.85
    factor = (pool - target) / other if other else Decimal(0)
    result = {
        # Round down tiny tails so the accepted proposal cannot exceed 100% through float noise
        key: float((Decimal(str(value)) * factor).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN))
        for key, value in weights.items() if key != symbol
    }
    result[symbol] = float(target)
    return result


def make_proposal(history, current, weights, name, forecast_model=None, profile=None):
    # Always compare on one dataset, with the actual current portfolio as the baseline
    comparison = compare_portfolios(history, ComparisonRequest(
        current=current, proposed=AllocationRequest(weights=weights), profile=profile
    ), forecast_model=forecast_model)
    return Proposal(name=name, weights=weights, comparison=comparison)


def reserve_weights(weights, cash_percent):
    # Scale only invested holdings; the remainder stays in the existing implicit cash balance
    invested = sum(Decimal(str(value)) for value in weights.values())
    scale = (Decimal(100) - Decimal(str(cash_percent))) / invested if invested else Decimal(0)
    return {symbol: float((Decimal(str(weight)) * scale).quantize(
        Decimal("0.00000001"), rounding=ROUND_DOWN)) for symbol, weight in weights.items()}


def budgeted_choices(history, model, choices, horizon, loss_limit):
    # A failing asset mix gets the same chance to add cash as the current portfolio
    if model is None or horizon is None or loss_limit is None:
        return []
    prices = np.array([asset.index_values for asset in history.assets], dtype=float).T
    returns = prices[1:] / prices[:-1] - 1
    training_years = model["count"] / 252
    terminal_floor = log1p(-loss_limit) if loss_limit < 1 else -float("inf")
    adjusted_choices = []
    for name, weights in choices:
        if name == "Cash reserve":
            continue
        allocation = np.array([weights.get(symbol, 0) / 100 for symbol in model["symbols"]])
        annual_mean = float(allocation @ model["expected"] * 252)
        annual_variance = max(0.0, float(allocation @ model["covariance"] @ allocation * 252))
        daily = returns @ allocation

        def fits(scale):
            # Scaling into cash changes variance drag as well as volatility, so recheck every loss measure
            volatility = sqrt(annual_variance) * scale
            growth = annual_mean * scale - volatility ** 2 / 2
            if drawdown_screen(growth, volatility, horizon, training_years) > loss_limit:
                return False
            adverse = horizon * growth - 1.6448536269514722 * volatility * sqrt(
                horizon + horizon ** 2 / training_years)
            if adverse < terminal_floor:
                return False
            path = np.r_[1.0, np.cumprod(1 + daily * scale)]
            return float(np.max(1 - path / np.maximum.accumulate(path))) <= loss_limit

        if fits(1):
            # Already feasible mixes stay unchanged, including choices without a binding loss budget
            continue
        lower, upper = 0.0, 1.0
        for _ in range(24):
            middle = (lower + upper) / 2
            if fits(middle):
                lower = middle
            else:
                upper = middle
        cash = 100 - sum(weights.values()) * lower
        adjusted = reserve_weights(weights, cash)
        # The existing final filters recheck rounded weights, position caps and protected cash
        # Full cash has its own name; do not offer an asset addition at 0%
        if adjusted.get(name, 0) > 0:
            adjusted_choices.append((name, adjusted))
    return adjusted_choices


def candidates(history: History, request: PlanningRequest) -> CandidateResults:
    # Validate the real baseline first, even if none of the candidates will meet the requested limits
    baseline = analyse_portfolio(history, request.current)
    forecast_model = fit_forecast(history)
    profile = request.profile
    horizon = profile.horizon_years if profile else None
    loss_limit = profile.max_loss_percent / 100 if profile and profile.max_loss_percent is not None else None
    # Period sets the calculation length and a loss budget constrains it; neither silently replaces the user's goal
    preserve = profile is not None and (profile.goal == "preserve" or profile.capacity == "essential")
    growth = profile is not None and profile.goal == "growth" and not preserve
    # Existing cash is a floor when the user may need it soon, never an invented emergency-fund amount
    cash_floor = max(request.cash_percent, baseline.cash_weight_percent) if preserve else request.cash_percent
    criteria = ("Lower volatility and no larger historical drawdown; keep at least your current cash allocation"
                if preserve else "Lower volatility without reducing historical arithmetic return"
                if growth else "Lower historical volatility")
    if profile is not None and profile.goal == "income":
        criteria += "; income payments are not ranked because payout history is unavailable"
    if forecast_model:
        criteria = ("Lower estimated risk; retain cash and check past losses" if preserve else
                    "Lower estimated risk with no lower modelled compounded growth" if growth else
                    "Lower estimated risk; compare the expected return trade-off")
        if profile is not None and profile.goal == "income":
            criteria += "; income payments are not forecast"
        if profile and (profile.goal != "learn" or loss_limit is not None) and horizon is None:
            # An old category or an unsure answer cannot silently become a numerical investment period
            return CandidateResults(source=history.source, dataset_version=history.dataset_version,
                proposals=[], evaluated=0, criteria="Choose an investment period in Your preferences to compare growth and possible losses")
        if loss_limit is not None:
            criteria += "; past drawdown and modelled path losses must fit your loss limit"
    current_forecast = forecast_comparison(forecast_model, request.current.weights,
        request.current.weights, horizon_years=horizon) if forecast_model else None
    outside_limit = loss_limit is not None and (baseline.maximum_drawdown > loss_limit or
        (current_forecast is not None and
         ((current_forecast.current.projected_growth_range is not None and
           current_forecast.current.projected_growth_range[0] < -loss_limit) or
          (current_forecast.current.drawdown_screen is not None and
           current_forecast.current.drawdown_screen > loss_limit))))
    if outside_limit:
        # A user's loss limit takes priority over keeping the return estimate of an already excessive-risk mix
        criteria = "Bring the tested loss measures within your limit; compare the growth trade-off"
    minimum_reduction = 0.0 if outside_limit else .001
    choices = []
    for asset in history.assets:
        try:
            choices.append((asset.symbol, funded_weights(request.current.weights, asset.symbol,
                                                        request.target_percent, preserve)))
        except ValueError:
            # A fully invested single holding cannot be resized by scaling nonexistent other holdings
            continue
    if forecast_model and profile and baseline.cash_weight_percent < 100:
        # Cash is an allocation choice, not a fabricated ticker or an extra price series
        targets = [request.target_percent, 100] if preserve or outside_limit else [request.target_percent]
        if outside_limit and horizon is not None:
            # Find the smallest reserve meeting the path and endpoint screens, rather than only trying 15%/30%
            lower, upper = baseline.cash_weight_percent, 100.0
            asset_returns = np.array([asset.index_values for asset in history.assets], dtype=float).T
            asset_returns = asset_returns[1:] / asset_returns[:-1] - 1
            for _ in range(24):
                target = (lower + upper) / 2
                trial = reserve_weights(request.current.weights, target)
                projected = forecast_comparison(forecast_model, request.current.weights,
                    trial, horizon_years=horizon).proposed
                allocation = np.array([trial.get(symbol, 0) / 100 for symbol in forecast_model["symbols"]])
                path = np.r_[1.0, np.cumprod(1 + asset_returns @ allocation)]
                drawdown = float(np.max(1 - path / np.maximum.accumulate(path)))
                if (projected.projected_growth_range is not None and
                        projected.projected_growth_range[0] >= -loss_limit and
                        projected.drawdown_screen <= loss_limit and drawdown <= loss_limit):
                    upper = target
                else:
                    lower = target
            targets.append(upper)
        for target in dict.fromkeys(targets):
            if target <= baseline.cash_weight_percent:
                continue
            reserve = reserve_weights(request.current.weights, target)
            # Keep 0% rows for a full reserve so Apply can still explain where the user's money went
            choices.append(("Cash reserve", reserve))
    choices.extend(budgeted_choices(history, forecast_model, choices, horizon, loss_limit))
    proposals = []
    for name, weights in choices:
        # Filter the whole proposal, not just the asset being added
        invested = sum(Decimal(str(value)) for value in weights.values())
        if max(weights.values(), default=0) > request.max_position_percent:
            continue
        if Decimal(100) - invested < Decimal(str(cash_floor)):
            # Scaling cash proportionally can leave less than the requested minimum reserve
            continue
        # Reject failed forecasts before calculating full paths, correlations and exposure tables
        forecast = forecast_comparison(forecast_model, request.current.weights, weights, horizon_years=horizon) if forecast_model else None
        if forecast:
            before, after = forecast.current, forecast.proposed
            # Compounded wealth is the growth objective; a larger arithmetic average alone does not establish it
            if growth and not outside_limit and after.approximate_annual_log_growth < before.approximate_annual_log_growth - 1e-12:
                continue
            if horizon is not None and (after.projected_growth is None or after.projected_growth_range is None):
                continue
            risk_change = after.estimated_annual_volatility - before.estimated_annual_volatility
            if risk_change > -minimum_reduction:
                continue
            if loss_limit is not None:
                if after.projected_growth_range is None or after.projected_growth_range[0] < -loss_limit - 1e-12:
                    continue
                if after.drawdown_screen is None or after.drawdown_screen > loss_limit + 1e-12:
                    continue
        proposal = make_proposal(history, request.current, weights, name, forecast_model, profile)

        # The past-drawdown guard applies only to protective profiles, not the growth forecast
        if preserve and proposal.comparison.proposed.maximum_drawdown > baseline.maximum_drawdown + 1e-12:
            continue
        if loss_limit is not None and proposal.comparison.proposed.maximum_drawdown > loss_limit + 1e-12:
            continue
        if not forecast:
            # Undated fixtures retain their deterministic comparison rules, not a fabricated forecast
            if growth and proposal.comparison.proposed.annualised_arithmetic_return < baseline.annualised_arithmetic_return - 1e-12:
                continue
            risk_change = proposal.comparison.changes.annualised_volatility

        # A small numerical change is not presented as a meaningful improvement
        if risk_change <= -minimum_reduction:
            # Changes are decimals, so -0.001 means a reduction of 0.1 percentage points
            # Prefer a growth improvement that survives disagreement between the existing mean estimators
            if forecast and growth:
                difference = np.array([(weights.get(symbol, 0) - request.current.weights.get(symbol, 0)) / 100
                                       for symbol in forecast_model["symbols"]])
                variance_change = after.estimated_annual_volatility ** 2 - before.estimated_annual_volatility ** 2
                cautious_growth = min(float(difference @ mean * 252) - variance_change / 2
                                      for mean in forecast_model["return_scenarios"].values())
                proposal.rank_score = -round(cautious_growth * horizon, 12)
            elif forecast and preserve and after.drawdown_screen is not None:
                # Capital preservation ranks path losses even when a supplied budget is nonbinding
                proposal.rank_score = after.drawdown_screen
            else:
                proposal.rank_score = (forecast.proposed.estimated_annual_volatility if forecast else
                                      proposal.comparison.proposed.maximum_drawdown if preserve else
                                      proposal.comparison.proposed.annualised_volatility)
            if np.isfinite(proposal.rank_score):
                proposals.append(proposal)
    # Rank by the profile objective and break equal scores by identifier for repeatable ordering
    proposals.sort(key=lambda item: (item.rank_score, item.name))
    return CandidateResults(source=history.source, dataset_version=history.dataset_version,
                            proposals=proposals, evaluated=len(choices), criteria=criteria,
                            minimum_reduction_percentage_points=minimum_reduction * 100,
                            notice="Compare the growth and cash trade-offs; modelled loss screens do not guarantee a maximum loss")


def solve_weights(history, cash_percent, cap_percent, start=0, stop=None):
    # Optimisation includes the selected universe, even assets absent from the current portfolio
    symbols = [asset.symbol for asset in history.assets]
    if not symbols:
        raise ValueError("No assets are available")
    invested = 1 - cash_percent / 100
    cap = cap_percent / 100

    # If every asset reached its cap and still could not fund the invested total, no solution exists
    if invested > len(symbols) * cap + 1e-12:
        raise ValueError("Position limit cannot fund the invested portion. Increase the limit or cash")
    if invested == 0:
        # 100% fixed cash needs no solver and is represented by an empty holdings dictionary
        return {}

    # The existing engine validates all paths before covariance is estimated
    columns = [analyse_portfolio(history, AllocationRequest(weights={symbol: 100})).session_returns
               for symbol in symbols]

    # Rows are assets and columns are matched returns; start/stop select a sensitivity window
    returns = np.asarray(columns, dtype=float)[:, start:stop]
    if returns.shape[1] < 2:
        raise ValueError("At least 2 returns are required in each optimisation window")
    # ddof=1 estimates sample covariance and 252 puts variance on the annualised basis used by the report
    # Keep a 2D matrix even for 1 asset so the quadratic form below still has compatible dimensions
    covariance = np.atleast_2d(np.cov(returns, ddof=1)) * 252

    if not np.all(np.isfinite(covariance)):
        raise ValueError("Covariance exceeds the supported numeric range")

    # Scaling improves solver conditioning without changing the variance minimiser
    scale = max(float(np.max(np.diag(covariance))), 1e-12)
    matrix = covariance / scale
    result = minimize(
        # Portfolio variance is w' covariance w; its derivative is 2 covariance w
        lambda w: float(w @ matrix @ w),
        # Equal starting weights satisfy the invested total; the feasibility check already checked the cap
        np.full(len(symbols), invested / len(symbols)),
        jac=lambda w: 2 * matrix @ w,
        method="SLSQP", bounds=[(0, cap)] * len(symbols),
        # Equality fixes invested weight, preventing the solver from reducing risk by adding extra cash
        constraints=[{"type": "eq", "fun": lambda w: float(np.sum(w) - invested),
                      "jac": lambda w: np.ones(len(w))}],
        options={"ftol": 1e-12, "maxiter": 1000},
    )

    # Never silently use a failed solver result or relax the user's constraints
    if (not result.success or not np.all(np.isfinite(result.x)) or
            abs(float(np.sum(result.x)) - invested) > 1e-8 or
            np.min(result.x) < -1e-8 or np.max(result.x) > cap + 1e-8):
        raise ValueError("Optimisation did not converge within the requested limits")

    # Round down tiny tails; any sub-millionth remainder stays in cash
    return {symbol: float(Decimal(str(min(cap, max(0, float(value))) * 100)).quantize(
        Decimal("0.00000001"), rounding=ROUND_DOWN))
        for symbol, value in zip(symbols, result.x) if value > 1e-10}


def optimise(history: History, request: PlanningRequest) -> OptimisationResult:
    # Compare the full-sample solution against the actual current portfolio using the same observations
    analyse_portfolio(history, request.current)
    weights = solve_weights(history, request.cash_percent, request.max_position_percent)
    proposal = make_proposal(history, request.current, weights, "Minimum variance", profile=request.profile)
    count = len(history.sessions) - 1
    halves = []
    if count >= 4:
        # Split returns, not index observations; each half needs at least 2 returns for sample covariance
        for label, start, stop in [("First half", 0, count // 2), ("Second half", count // 2, count)]:
            halves.append(Sensitivity(sample=label, return_observations=stop-start,
                weights=solve_weights(history, request.cash_percent, request.max_position_percent, start, stop)))
    return OptimisationResult(proposal=proposal, sensitivity=halves)
