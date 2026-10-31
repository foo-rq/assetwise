from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class InvestorProfile(BaseModel):
    # Keep legacy answers readable alongside the exact period and loss inputs used by current comparisons
    model_config = ConfigDict(extra="forbid")
    experience: Literal["new", "some", "confident"]
    age: Literal["under18", "18-29", "30-44", "45-59", "60plus", "private"] = "private"
    goal: Literal["learn", "growth", "income", "preserve"]
    horizon: Literal["short", "medium", "long", "unsure"]
    # Exact years drive the projection; a saved category alone must not invent a time horizon
    horizon_years: float | None = Field(default=None, ge=.25, le=40, allow_inf_nan=False, strict=True)
    tolerance: Literal["low", "moderate", "high", "unsure"]
    # This is the user's stated loss limit, not a guarantee that a portfolio cannot lose more
    max_loss_percent: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False, strict=True)
    capacity: Literal["essential", "some", "spare", "unsure"]
    existing: Literal["yes", "no"] = "no"
    # A modelling total enables amount entry while stored portfolio weights stay percentages
    amount: float = Field(default=0, ge=0, le=1_000_000_000_000, allow_inf_nan=False, strict=True)
