"""National Weather Service (api.weather.gov): official alerts and point forecasts. Free, no key."""
from __future__ import annotations

from typing import Optional

from ..models import Checkpoint
from ..net import Trace

BASE = "https://api.weather.gov"
HEADERS = {"Accept": "application/geo+json"}


def _pt(cp: Checkpoint) -> str:
    # NWS redirects requests with more than 4 decimal places.
    return f"{cp.lat:.4f},{cp.lon:.4f}"


async def active_alerts(trace: Trace, cp: Checkpoint) -> list[dict]:
    data = await trace.fetch(
        "NWS Alerts", cp.label, f"{BASE}/alerts/active", {"point": _pt(cp)}, ttl=300, headers=HEADERS
    )
    return [f.get("properties", {}) for f in data.get("features", [])]


async def point_info(trace: Trace, cp: Checkpoint) -> dict:
    data = await trace.fetch("NWS Points", cp.label, f"{BASE}/points/{_pt(cp)}", ttl=86400, headers=HEADERS)
    return data.get("properties", {})


async def forecast_periods(trace: Trace, cp: Checkpoint, forecast_url: str) -> list[dict]:
    data = await trace.fetch("NWS Forecast", cp.label, forecast_url, ttl=1800, headers=HEADERS)
    return data.get("properties", {}).get("periods", [])


def place_label(points: dict) -> Optional[str]:
    rel = (points.get("relativeLocation") or {}).get("properties") or {}
    if rel.get("city") and rel.get("state"):
        return f"near {rel['city']}, {rel['state']}"
    return None
