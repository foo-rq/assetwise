from pydantic import BaseModel, ConfigDict

from backend.analytics import AllocationRequest, PortfolioAnalysis, analyse_portfolio
from backend.simulation import SimulatedHistory


class ComparisonRequest(BaseModel):
    # Validate both allocations with the same limits used by the holdings editor
    model_config = ConfigDict(extra="forbid")
    current: AllocationRequest
    proposed: AllocationRequest


class MetricChanges(BaseModel):
    # Proposed minus current, in decimal units rather than relative percentage changes
    # A negative volatility or drawdown change means the simulated risk measure fell
    annualised_arithmetic_return: float
    annualised_volatility: float
    maximum_drawdown: float


class PortfolioComparison(BaseModel):
    # Each side carries its simulation label, session count and calculation assumptions
    current: PortfolioAnalysis
    proposed: PortfolioAnalysis
    changes: MetricChanges
    change_units: str = "Decimal differences; multiply by 100 for percentage points"


def compare_portfolios(
    history: SimulatedHistory, request: ComparisonRequest
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
    return PortfolioComparison(current=current, proposed=proposed, changes=changes)
