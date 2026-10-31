from pathlib import Path
from fastapi.staticfiles import StaticFiles
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from backend.api import router as portfolio_router
from backend.storage import router as storage_router

# Uvicorn imports this object when we start backend.main:app
app = FastAPI(title="Assetwise API", version="0.1.0")
app.include_router(portfolio_router)
app.include_router(storage_router)

# Opening the deployed site should reach the workspace without requiring a path to be typed
@app.get("/", include_in_schema=False)
def home():
    return RedirectResponse("/workspace/")


@app.middleware("http")
async def refresh_workspace_files(request, call_next):
    response = await call_next(request)

    # Revalidate interface files so a cached page cannot pair old markup with new scripts
    if request.url.path.startswith("/workspace"):
        response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/api/storage"):
        # Private portfolios must never enter shared browser or proxy caches
        response.headers["Cache-Control"] = "no-store"
    return response

# A fixed status value gives clients a predictable response and documents it in /docs
class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"

# This checks that the API can respond, not whether a database or market feed is ready
# Keep it independent of external services so local startup needs no credentials
@app.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    return HealthResponse()

# Serve only the interface folder, never the repository or local context files
# Resolve from this file so starting the server in another directory still works
app.mount("/workspace", StaticFiles(directory=Path(__file__).resolve().parent.parent / "prototype", html=True), name="workspace")
