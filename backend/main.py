from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from backend.simulation import SimulatedHistory, build_simulated_history
from backend.analytics import AllocationRequest, PortfolioAnalysis, analyse_portfolio

# Uvicorn imports this object when we start backend.main:app
app = FastAPI(title="Assetwise API", version="0.1.0")

# A fixed status value gives clients a predictable response and documents it in /docs
class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"

# This checks that the API can respond, not whether a database or market feed is ready
# Keep it independent of external services so local startup needs no credentials
@app.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    return HealthResponse()

# Keep invented history on an explicitly named route with its source in every response
@app.get("/demo/history", response_model=SimulatedHistory, tags=["demo"])
def demo_history() -> SimulatedHistory:
    return build_simulated_history()

# Accept allocation percentages while keeping the simulated history controlled by the server
@app.post("/demo/analysis", response_model=PortfolioAnalysis, tags=["demo"])
def demo_analysis(allocation: AllocationRequest) -> PortfolioAnalysis:
    try:
        return analyse_portfolio(build_simulated_history(), allocation)
    except ValueError as error:
        # Invalid allocations need an actionable response rather than a server error
        raise HTTPException(status_code=422, detail=str(error)) from error
