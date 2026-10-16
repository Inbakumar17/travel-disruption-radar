"""Request/response models shared by the API, the risk engine and the UI."""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

SEVERITY_LABELS = ["Info", "Low", "Moderate", "High"]
LEVELS = ["Minimal", "Low", "Moderate", "High"]


class AssessRequest(BaseModel):
    origin: str = Field(min_length=2, max_length=80, examples=["New York"])
    destination: str = Field(min_length=2, max_length=80, examples=["San Francisco"])
    date: date
    mode: Literal["fly", "drive"] = "fly"


class Place(BaseModel):
    query: str
    name: str
    lat: float
    lon: float
    tz: str
    kind: Literal["city", "airport"]
    iata: Optional[str] = None


class Checkpoint(BaseModel):
    """A location along the trip that we pull data for (airport, city or highway point)."""

    id: str
    label: str
    role: Literal["origin", "destination", "en_route"]
    lat: float
    lon: float
    tz: str
    date: date  # local date the traveller is expected to be here
    iata: Optional[str] = None
    mile: Optional[float] = None
    eta_hours: Optional[float] = None
    conditions: Optional[str] = None
    temp_range_f: Optional[str] = None
    max_severity: int = 0


class Finding(BaseModel):
    """One piece of evidence. Every finding names the source it came from."""

    checkpoint_id: str
    checkpoint_label: str
    category: Literal["weather_alert", "forecast", "airport_ops", "route"]
    severity: int = Field(ge=0, le=3)
    title: str
    detail: str
    source: str
    source_url: Optional[str] = None
    window: Optional[str] = None


class SourceCall(BaseModel):
    source: str
    target: str
    url: str
    ok: bool
    ms: int
    cached: bool = False
    age_s: Optional[int] = None  # for cached answers: seconds since the data was actually fetched
    error: Optional[str] = None


class RouteInfo(BaseModel):
    distance_mi: float
    duration_h: float
    geometry: list[list[float]]  # [lon, lat] pairs


class Assessment(BaseModel):
    request: AssessRequest
    origin: Place
    destination: Place
    days_ahead: int
    level: str
    score: int
    confidence: Literal["High", "Medium", "Low"]
    confidence_reasons: list[str]
    headline: str
    summary: str
    summary_by: Literal["rules", "llm"]
    actions: list[str]
    checkpoints: list[Checkpoint]
    findings: list[Finding]
    sources: list[SourceCall]
    assumptions: list[str]
    route: Optional[RouteInfo] = None
    demo_mode: bool = False
    generated_at: str
