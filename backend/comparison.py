from pydantic import BaseModel, ConfigDict

from backend.analytics import AllocationRequest, PortfolioAnalysis, analyse_portfolio
from backend.history import History
from backend.forecast import ForecastComparison, fit_forecast, forecast_comparison
from backend.profile import InvestorProfile


class ComparisonRequest(BaseModel):
    # Validate both allocations with the same limits used by the holdings editor
    model_config = ConfigDict(extra="forbid")
    current: AllocationRequest
    proposed: AllocationRequest
    profile: InvestorProfile | None = None


class MetricChanges(BaseModel):
    # Proposed minus current, in decimal units rather than relative percentage changes
    # A negative volatility or drawdown change means the measured risk fell
    annualised_arithmetic_return: float
    annualised_volatility: float
    maximum_drawdown: float


class PortfolioComparison(BaseModel):
    # Each side carries its source label, session count and calculation assumptions
    current: PortfolioAnalysis
    proposed: PortfolioAnalysis
    changes: MetricChanges
    forecast: ForecastComparison | None = None
    change_units: str = "Decimal differences; multiply by 100 for percentage points"


def compare_portfolios(
    history: History, request: ComparisonRequest, *, forecast_model=None
) -> PortfolioComparison:
    # Share one history object so both sides use exactly the same sessions and dataset
    # Analyse independently so adding an asset can improve, worsen or leave risk unchanged
    current = analyse_portfolio(history, request.current)
    proposed = analyse_portfolio(history, request.proposed)

    # Return the original results too so the interface can show both values beside a change
    changes = MetricChanges(**{
        field: getattr(proposed, field) - getattr(current, field)
        for field in MetricChanges.model_fields
    })
    model = forecast_model if forecast_model is not None else fit_forecast(history)
    # A preview uses the same stated period as analysis rather than silently falling back to 1 year
    years = request.profile.horizon_years if request.profile else None
    forecast = forecast_comparison(model, request.current.weights, request.proposed.weights, years) if model else None
    return PortfolioComparison(current=current, proposed=proposed, changes=changes, forecast=forecast)
