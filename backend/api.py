from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from backend.analytics import AllocationRequest, PortfolioAnalysis, analyse_portfolio
from backend.comparison import ComparisonRequest, PortfolioComparison, compare_portfolios
from backend.historical import build_historical_history
from backend.history import History
from backend.forecast import fit_forecast, forecast_comparison
from backend.planning import CandidateResults, OptimisationResult, PlanningRequest, candidates, optimise
from backend.profile import InvestorProfile

# All public calculations use the dated archive with its disclosed source limitations
router = APIRouter(prefix="/api", tags=["portfolio"])


class AnalysisRequest(AllocationRequest):
    # Profile context changes projections, while the existing weight validation remains shared
    profile: InvestorProfile | None = None


def selected_history(dataset: Literal["historical"] = "historical"):
    # FastAPI reads dataset from the query string and rejects unsupported names before this runs
    # Resolve 1 complete history object for the whole request, including both comparison columns
    try:
        return build_historical_history()
    except (ValueError, OSError) as error:
        # Never substitute invented data when the historical snapshot cannot be verified
        raise HTTPException(503, "Dataset unavailable. Check the installed snapshot and retry") from error


def calculate(operation, history, request):
    # Each operation receives the same validated history/request pair and owns its calculations
    try:
        return operation(history, request)
    except ValueError as error:
        # Invalid weights or infeasible limits are input errors, so return their reason as HTTP 422
        raise HTTPException(422, str(error)) from error


@router.get("/history", response_model=History)
def history(data: History = Depends(selected_history)):
    # Names, values and dates come from 1 versioned source rather than separate frontend lists
    return data


@router.post("/analysis", response_model=PortfolioAnalysis)
def analysis(request: AnalysisRequest, data: History = Depends(selected_history)):
    result = calculate(analyse_portfolio, data, request)
    try:
        model = fit_forecast(data)
        # Older direct callers may still pass AllocationRequest, which has no profile context
        profile = getattr(request, "profile", None)
        years = profile.horizon_years if profile else None
        result.forecast = forecast_comparison(model, request.weights, request.weights, years) if model else None
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return result


@router.post("/comparison", response_model=PortfolioComparison)
def comparison(request: ComparisonRequest, data: History = Depends(selected_history)):
    # Both allocations use this same history; changing a proposal cannot change its baseline period
    return calculate(compare_portfolios, data, request)


@router.post("/candidates", response_model=CandidateResults)
def candidate_search(request: PlanningRequest, data: History = Depends(selected_history)):
    # Candidate funding and ranking stay in the planning module; this route only selects the data
    return calculate(candidates, data, request)


@router.post("/optimise", response_model=OptimisationResult)
def optimisation(request: PlanningRequest, data: History = Depends(selected_history)):
    # A solver result remains a proposal and never writes the user's accepted portfolio to SQL
    return calculate(optimise, data, request)
