"""Deterministic risk rules. Each source's raw data becomes Findings with a 0-3 severity:

    0 Info      checked, not travel-relevant (shown for transparency)
    1 Low       worth knowing, unlikely to disrupt
    2 Moderate  delays likely; plan a buffer
    3 High      cancellations / unsafe roads likely; act now

Rules are intentionally simple and readable so Ops can audit why a trip was flagged.
Thresholds live at the top of each function so they're easy to tune.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from .models import LEVELS, Checkpoint, Finding
from .sources import faa as faa_src

NWS_ALERTS_URL = "https://alerts.weather.gov/"

# ---------------------------------------------------------------- helpers


def local_day_utc(d: date, tz: str) -> tuple[datetime, datetime]:
    z = ZoneInfo(tz)
    start = datetime.combine(d, time.min, tzinfo=z)
    return start, start + timedelta(days=1)


def _dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fmt_window(start: Optional[datetime], end: Optional[datetime], tz: str) -> Optional[str]:
    if not start and not end:
        return None
    z = ZoneInfo(tz)
    def f(x: datetime) -> str:
        x = x.astimezone(z)
        # built manually: "%-d"/"%-I" (no leading zero) are Linux-only and crash on Windows
        return f"{x:%a %b} {x.day} {x.hour % 12 or 12}:{x:%M %p}"

    if start and end:
        return f"{f(start)} → {f(end)}"
    return f"from {f(start)}" if start else f"until {f(end)}"


def _mk(cp: Checkpoint, category: str, sev: int, title: str, detail: str, source: str,
        url: Optional[str] = None, window: Optional[str] = None) -> Finding:
    return Finding(checkpoint_id=cp.id, checkpoint_label=cp.label, category=category, severity=sev,
                   title=title, detail=detail, source=source, source_url=url, window=window)


# ---------------------------------------------------------------- NWS alerts

_IGNORE = ("rip current", "beach", "small craft", "gale", "hazardous seas", "marine", "air quality",
           "frost", "freeze warning", "freeze watch", "red flag", "fire weather", "low water",
           "hydrologic outlook", "lake wind", "brisk wind", "freezing spray", "high surf")
_TRAVEL = ("winter", "blizzard", "ice", "snow", "freezing", "wind", "tornado", "thunderstorm",
           "hurricane", "tropical", "flood", "fog", "dust", "storm surge", "avalanche", "cold")


def alert_severity(event: str, mode: str) -> int:
    e = event.lower()
    if any(k in e for k in _IGNORE):
        return 0
    if not any(k in e for k in _TRAVEL):
        return 1 if ("heat" in e or "special weather" in e) else 0
    sev = 3 if "warning" in e else 2 if "watch" in e else 1
    if "winter weather advisory" in e or "freezing" in e and "advisory" in e:
        sev = max(sev, 2)  # de-icing delays for flights, slick roads for drivers
    if "dense fog" in e:
        sev = max(sev, 2 if mode == "fly" else 1)  # low visibility cuts airport arrival rates
    if "flood" in e and mode == "fly":
        sev = min(sev, 2)  # affects getting to the airport more than flying
    if "chill" in e or "cold" in e:
        sev = min(sev, 2)
    if "wind advisory" in e:
        sev = 1
    return sev


def from_alerts(cp: Checkpoint, alerts: list[dict], mode: str) -> list[Finding]:
    start, end = local_day_utc(cp.date, cp.tz)
    out, seen = [], set()
    for a in alerts:
        event = a.get("event") or "Weather alert"
        on = _dt(a.get("onset")) or _dt(a.get("effective"))
        off = _dt(a.get("ends")) or _dt(a.get("expires"))
        if (on and on >= end) or (off and off <= start):
            continue  # alert doesn't overlap the day the traveller is here
        if event in seen:
            continue  # NWS often issues several updates of the same event for one point
        seen.add(event)
        sev = alert_severity(event, mode)
        detail = a.get("headline") or (a.get("description") or "")[:240]
        if sev >= 2 and a.get("instruction"):
            detail += " — " + a["instruction"].split("\n\n")[0][:200]
        out.append(_mk(cp, "weather_alert", sev, event, detail, "NWS Alerts", NWS_ALERTS_URL,
                       _fmt_window(on, off, cp.tz)))
    return out


# ---------------------------------------------------------------- NWS forecast


def _max_wind(text: str) -> int:
    """'10 to 25 mph' -> 25."""
    text = text or ""
    if "mph" not in text:
        return 0
    nums = [int(n) for n in re.findall(r"\d+", text)]
    return max(nums) if nums else 0


def nws_period_severity(p: dict, mode: str) -> tuple[int, list[str]]:
    short = (p.get("shortForecast") or "").lower()
    detailed = (p.get("detailedForecast") or "").lower()
    pop = (p.get("probabilityOfPrecipitation") or {}).get("value") or 0
    sev, why = 0, []

    def bump(s: int, reason: str) -> None:
        nonlocal sev
        sev = max(sev, s)
        why.append(reason)

    if "blizzard" in short:
        bump(3, "blizzard")
    if any(k in short for k in ("freezing rain", "freezing drizzle", "sleet", "ice")):
        bump(3 if mode == "drive" else 2, "freezing precipitation")
    if "heavy snow" in short:
        bump(3, "heavy snow")
    elif "snow" in short:
        bump(2 if pop >= 50 else 1, f"snow ({pop}% chance)")
    if "thunder" in short or "t-storm" in short:
        if "severe" in short or "severe" in detailed:
            bump(3, "severe thunderstorms")
        else:
            bump(2 if pop >= 50 else 1, f"thunderstorms ({pop}% chance)")
    if "heavy rain" in short:
        bump(2, "heavy rain")
    if "dense fog" in short:
        bump(2 if mode == "fly" else 1, "dense fog")
    elif "fog" in short:
        bump(1, "fog")

    wind = max(_max_wind(p.get("windSpeed", "")), _max_wind(p.get("windGust") or ""))
    g = re.search(r"gusts? as high as (\d+) mph", detailed)
    if g:
        wind = max(wind, int(g.group(1)))
    if wind >= 55:
        bump(3, f"wind/gusts to {wind} mph")
    elif wind >= 40:
        bump(2, f"wind/gusts to {wind} mph")
    elif wind >= 30:
        bump(1, f"breezy, gusts to {wind} mph")
    return sev, why


def from_nws_forecast(cp: Checkpoint, periods: list[dict], mode: str) -> tuple[list[Finding], Optional[int], Optional[str]]:
    """Returns (findings, worst severity for the day, a short conditions summary)."""
    day = [p for p in periods if (_dt(p.get("startTime")) or datetime.min).date() == cp.date]
    if not day:
        return [], None, None
    scored = [(p, *nws_period_severity(p, mode)) for p in day]
    worst_p, worst_sev, _ = max(scored, key=lambda t: t[1])
    conditions = (next((p for p in day if p.get("isDaytime")), day[0]).get("shortForecast"))
    if worst_sev == 0:
        return [], 0, conditions
    lines = [f"{p.get('name')}: {p.get('shortForecast')} — {', '.join(w)}" for p, s, w in scored if s > 0]
    f = _mk(cp, "forecast", worst_sev, f"NWS forecast: {worst_p.get('shortForecast')}", "; ".join(lines),
            "NWS Forecast", f"https://forecast.weather.gov/MapClick.php?lat={cp.lat:.4f}&lon={cp.lon:.4f}")
    return [f], worst_sev, conditions


# ---------------------------------------------------------------- Open-Meteo

WMO = {0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Freezing fog",
       51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
       61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Heavy freezing rain",
       71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Rain showers",
       81: "Heavy rain showers", 82: "Violent rain showers", 85: "Snow showers", 86: "Heavy snow showers",
       95: "Thunderstorms", 96: "Thunderstorms with hail", 99: "Severe thunderstorms with hail"}


def openmeteo_severity(d: dict, mode: str) -> tuple[int, list[str]]:
    sev, why = 0, []

    def bump(s: int, reason: str) -> None:
        nonlocal sev
        sev = max(sev, s)
        why.append(reason)

    code = d.get("weather_code")
    snow = d.get("snowfall_sum") or 0.0
    rain = d.get("precipitation_sum") or 0.0
    gust = d.get("wind_gusts_10m_max") or 0.0
    pop = d.get("precipitation_probability_max")

    if snow >= 4:
        bump(3, f"{snow:.1f} in snow")
    elif snow >= 1:
        bump(2, f"{snow:.1f} in snow")
    elif snow >= 0.2:
        bump(1, f"{snow:.1f} in snow")
    if code in (56, 57, 66, 67):
        bump(3 if mode == "drive" else 2, WMO[code].lower())
    if code in (96, 99):
        bump(3, WMO[code].lower())
    elif code == 95:
        bump(2, "thunderstorms")
    if code == 48:
        bump(2 if mode == "fly" else 1, "freezing fog")
    elif code == 45:
        bump(1, "fog")
    if gust >= 60:
        bump(3, f"gusts {gust:.0f} mph")
    elif gust >= 45:
        bump(2, f"gusts {gust:.0f} mph")
    elif gust >= 35:
        bump(1, f"gusts {gust:.0f} mph")
    if rain >= 1.5:
        bump(2, f"{rain:.1f} in precipitation")
    elif rain >= 0.5 and (pop or 0) >= 60:
        bump(1, f"{rain:.1f} in precipitation ({pop}% chance)")
    return sev, why


def from_openmeteo(cp: Checkpoint, d: Optional[dict], mode: str) -> tuple[list[Finding], Optional[int], Optional[str]]:
    if not d:
        return [], None, None
    sev, why = openmeteo_severity(d, mode)
    desc = WMO.get(d.get("weather_code"), "Unknown")
    hi, lo = d.get("temperature_2m_max"), d.get("temperature_2m_min")
    if hi is not None and lo is not None:
        cp.temp_range_f = f"{lo:.0f}–{hi:.0f}°F"
    if sev == 0:
        return [], 0, desc
    f = _mk(cp, "forecast", sev, f"Model forecast: {desc}", ", ".join(why).capitalize(), "Open-Meteo Forecast",
            "https://open-meteo.com/")
    return [f], sev, desc


# ---------------------------------------------------------------- FAA


def from_faa(cp: Checkpoint, status: faa_src.FaaStatus, days_ahead: int) -> list[Finding]:
    if not cp.iata:
        return []
    out = []
    start, end = local_day_utc(cp.date, cp.tz)
    for ev in status.events.get(cp.iata, []):
        reason = faa_src.humanize_reason(ev.reason)
        if ev.kind == "closure":
            if ev.start and ev.end and (ev.start >= end or ev.end <= start):
                continue
            out.append(_mk(cp, "airport_ops", 3, f"{cp.iata} airport closure", f"{ev.detail}. {reason}",
                           "FAA NAS Status", faa_src.PUBLIC_URL, _fmt_window(ev.start, ev.end, cp.tz)))
            continue

        if ev.kind == "ground_stop":
            sev = 3
        elif ev.kind == "ground_delay":
            sev = 3 if (ev.avg_minutes or 0) >= 90 else 2
        else:
            sev = 2 if (ev.max_minutes or 0) >= 45 else 1

        if days_ahead >= 2:
            continue  # live status isn't predictive several days out
        title = {"ground_stop": "Ground stop", "ground_delay": "Ground delay program", "delay": "Airport delays"}[ev.kind]
        if days_ahead == 1:
            sev = min(sev, 1)  # it's happening now; may or may not persist into tomorrow
            title = f"Current {title.lower()} (live now, may clear by tomorrow)"
        out.append(_mk(cp, "airport_ops", sev, f"{cp.iata}: {title}", f"{ev.detail}. Reason: {reason}",
                       "FAA NAS Status", faa_src.PUBLIC_URL))
    return out


# ---------------------------------------------------------------- aggregation


def overall(findings: list[Finding]) -> tuple[str, int]:
    top = max((f.severity for f in findings), default=0)
    base = [5, 25, 55, 80][top]
    # Several *places* with problems compound (e.g. both ends of a flight). Several sources agreeing
    # about the same place is corroboration, not extra risk, so it doesn't add to the score.
    hot_places = len({f.checkpoint_id for f in findings if f.severity >= 2})
    score = base + min(15, 7 * max(0, hot_places - 1))
    return LEVELS[top], min(score, 100)


def confidence(days_ahead: int, disagreements: list[str], failures: list[str]) -> tuple[str, list[str]]:
    order = ["High", "Medium", "Low"]
    i = 0 if days_ahead <= 2 else 1 if days_ahead <= 5 else 2
    reasons = [
        {0: "Trip is 0–2 days out: forecasts are reliable and live airport status applies."
            if days_ahead == 0 else "Trip is 0–2 days out: forecasts are reliable.",
         1: f"Trip is {days_ahead} days out: forecast details can still change.",
         2: f"Trip is {days_ahead} days out: only broad patterns are predictable this far ahead."}[i]
    ]
    if disagreements:
        i = min(2, i + 1)
        reasons.append("Forecast sources disagree at " + ", ".join(disagreements) + ".")
    if failures:
        i = min(2, i + 1)
        reasons.append("Unavailable: " + ", ".join(sorted(set(failures))) + " — assessment may be incomplete.")
    return order[i], reasons


def recommended_actions(level: str, mode: str, days_ahead: int, findings: list[Finding], checkpoints: list[Checkpoint],
                        alternates: dict[str, list[str]], route_hours: Optional[float]) -> list[str]:
    acts: list[str] = []
    top = [f for f in findings if f.severity >= 2]
    hot = sorted({f.checkpoint_label for f in findings if f.severity == 3})

    if mode == "fly":
        if level == "High":
            acts += [
                f"Flag this trip to the traveller and their manager today — high-risk conditions at {', '.join(hot)}.",
                "Ask the traveller to consider moving the flight outside the hazard window; check the airline's "
                "travel-waiver page (airlines often waive change fees for declared weather events).",
                "If not booked yet, book a changeable fare and a cancellable hotel.",
            ]
        elif level == "Moderate":
            acts += [
                "Prefer a morning departure — delays build through the day — and avoid connections under 90 minutes.",
                "Turn on airline flight-status notifications and re-run this check 24 hours before departure.",
            ]
        else:
            acts.append("No action needed now. Re-run this check the day before departure.")
        for f in top:
            if f.category == "airport_ops" and days_ahead == 0:
                acts.append(f"Check the airline app before leaving for the airport — {f.title.lower()} in effect.")
        for cp in checkpoints:
            alts = alternates.get(cp.id) or []
            if cp.max_severity >= 2 and alts and any(f.category == "airport_ops" and f.checkpoint_id == cp.id
                                                       for f in top):
                acts.append(f"Nearby alternates to {cp.iata}: {', '.join(alts)} (same regional weather, "
                            f"but may not share the airport-specific delay).")
    else:
        if level == "High":
            acts += [
                f"Advise postponing or rerouting — avoid driving through {', '.join(hot)} during the hazard window.",
                "If travel must happen, share the route and ETA with a contact and check the state 511 road-conditions "
                "service before leaving.",
            ]
        elif level == "Moderate":
            acts += [
                "Build in extra driving time and check the state 511 road-conditions service before departure.",
                "Re-run this check the evening before departure.",
            ]
        else:
            acts.append("No action needed now. Re-run this check the day before departure.")
        if any("snow" in (f.title + f.detail).lower() or "freezing" in (f.title + f.detail).lower() for f in top):
            acts.append("Winter conditions on route: confirm the vehicle has suitable tyres and a winter kit.")
        if route_hours and route_hours > 10:
            acts.append(f"This is a ~{route_hours:.0f}-hour drive. Plan an overnight stop — company policy usually caps "
                        "a single day's driving.")

    if days_ahead > 3 and level in ("Moderate", "High"):
        acts.append("Forecasts this far out shift a lot — don't rebook yet; re-check within 72 hours of travel.")
    # de-duplicate, keep order
    seen, out = set(), []
    for a in acts:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out
