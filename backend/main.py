from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from backend.simulation import SimulatedHistory, build_simulated_history

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
