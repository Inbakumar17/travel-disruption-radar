"""Open-Meteo forecast API: an independent model forecast (up to 16 days). Free, no key.

Used two ways: (1) as the only forecast beyond the ~7-day NWS horizon, and
(2) as a second opinion inside it, so we can flag when models disagree.
"""
from __future__ import annotations

from datetime import date

from ..models import Checkpoint
from ..net import Trace

URL = "https://api.open-meteo.com/v1/forecast"
DAILY_VARS = [
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "snowfall_sum",
    "precipitation_probability_max",
    "wind_gusts_10m_max",
]


async def daily_for(trace: Trace, cps: list[Checkpoint]) -> dict[str, dict]:
    """One batched request for all checkpoints. Returns {checkpoint_id: {var: value}} for each cp's date."""
    if not cps:
        return {}
    start = min(cp.date for cp in cps)
    end = max(cp.date for cp in cps)
    params = {
        "latitude": ",".join(f"{cp.lat:.4f}" for cp in cps),
        "longitude": ",".join(f"{cp.lon:.4f}" for cp in cps),
        "daily": ",".join(DAILY_VARS),
        "timezone": "auto",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
    }
    target = cps[0].label if len(cps) == 1 else f"{len(cps)} locations"
    data = await trace.fetch("Open-Meteo Forecast", target, URL, params, ttl=1800)
    items = data if isinstance(data, list) else [data]

    out: dict[str, dict] = {}
    for cp, item in zip(cps, items):
        daily = item.get("daily") or {}
        days = daily.get("time") or []
        want = cp.date.isoformat()
        if want in days:
            i = days.index(want)
            out[cp.id] = {v: (daily.get(v) or [None] * len(days))[i] for v in DAILY_VARS}
    return out


def horizon_ok(d: date, today: date) -> bool:
    return 0 <= (d - today).days <= 15
