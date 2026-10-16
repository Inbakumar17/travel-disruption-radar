"""Orchestrator: resolve places -> build checkpoints -> query sources in parallel -> score -> explain."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, risk, summary
from .models import AssessRequest, Assessment, Checkpoint, Finding, Place, RouteInfo
from .net import SourceError, Trace
from .places import airport_for, alternate_airports, resolve
from .sources import faa, nws, openmeteo, osrm

NWS_FORECAST_DAYS = 6       # NWS 7-day forecast; day 7 is often partial
MAX_DAYS_AHEAD = 15         # Open-Meteo provides 16 days including today
DRIVE_HOURS_PER_DAY = 10    # assumption for multi-day drives
DRIVE_START_HOUR = 8


class AssessmentError(ValueError):
    pass


def _today(tz: str):
    return datetime.now(ZoneInfo(tz)).date()


async def _build_fly(trace: Trace, req: AssessRequest, o: Place, d: Place, assumptions: list[str]):
    oa, o_dist = airport_for(o)
    da, d_dist = airport_for(d)
    if oa.iata == da.iata:
        raise AssessmentError(f"Both ends map to {oa.iata}. For short trips try Drive mode.")
    for place, ap, dist in ((o, oa, o_dist), (d, da, d_dist)):
        if dist > 0:
            assumptions.append(f"{place.name} → assessed at {ap.iata} ({ap.city}), the nearest major airport "
                               f"({dist:.0f} mi). Enter an airport code to choose another.")
    cps = [
        Checkpoint(id="origin", label=f"{oa.city} ({oa.iata})", role="origin", lat=oa.lat, lon=oa.lon, tz=oa.tz,
                   date=req.date, iata=oa.iata),
        Checkpoint(id="destination", label=f"{da.city} ({da.iata})", role="destination", lat=da.lat, lon=da.lon,
                   tz=da.tz, date=req.date, iata=da.iata),
    ]
    alts = {"origin": [a.iata for a in alternate_airports(oa)], "destination": [a.iata for a in alternate_airports(da)]}
    assumptions.append("Assumes a nonstop flight; connection airports are not assessed.")
    return cps, alts, None


async def _build_drive(trace: Trace, req: AssessRequest, o: Place, d: Place, assumptions: list[str]):
    r = await osrm.route(trace, o, d)
    samples = osrm.sample_points(r["coords"], r["distance_mi"], r["duration_h"])

    def day_for(eta_h: float):
        return req.date + timedelta(days=int((eta_h + 1e-6) // DRIVE_HOURS_PER_DAY))

    cps = [Checkpoint(id="origin", label=o.name, role="origin", lat=o.lat, lon=o.lon, tz=o.tz, date=req.date,
                      mile=0, eta_hours=0)]
    for i, s in enumerate(samples, start=1):
        cps.append(Checkpoint(id=f"wp{i}", label=f"Mile {s['mile']}", role="en_route", lat=s["lat"], lon=s["lon"],
                              tz=o.tz, date=day_for(s["eta_h"]), mile=s["mile"], eta_hours=round(s["eta_h"], 1)))
    cps.append(Checkpoint(id="destination", label=d.name, role="destination", lat=d.lat, lon=d.lon, tz=d.tz,
                          date=day_for(r["duration_h"]), mile=round(r["distance_mi"]),
                          eta_hours=round(r["duration_h"], 1)))
    assumptions.append(f"Driving route from OSRM; conditions checked every ~150 mi ({len(samples)} en-route points).")
    if r["duration_h"] > DRIVE_HOURS_PER_DAY:
        assumptions.append(f"Multi-day drive: assumes {DRIVE_START_HOUR}am start and {DRIVE_HOURS_PER_DAY} driving "
                           "hours/day, so later checkpoints are assessed for later dates.")
    route = RouteInfo(distance_mi=round(r["distance_mi"]), duration_h=round(r["duration_h"], 1), geometry=r["coords"])
    return cps, {}, route


async def assess(req: AssessRequest) -> Assessment:
    trace = Trace()
    assumptions: list[str] = []

    o, d = await asyncio.gather(resolve(trace, req.origin), resolve(trace, req.destination))
    days_ahead = (req.date - _today(o.tz)).days
    if days_ahead < 0:
        raise AssessmentError("That date is in the past. Choose today or a future date.")
    if days_ahead > MAX_DAYS_AHEAD:
        raise AssessmentError(f"Forecast data only reaches {MAX_DAYS_AHEAD} days ahead. "
                              "Check back closer to the trip.")

    builder = _build_fly if req.mode == "fly" else _build_drive
    try:
        cps, alternates, route = await builder(trace, req, o, d, assumptions)
    except SourceError as e:
        raise AssessmentError(f"Couldn't plan the route: {e}") from e

    failures: list[str] = []

    # ---- gather everything concurrently; any single source failing is tolerated
    async def per_checkpoint(cp: Checkpoint):
        alerts, periods = None, None
        cp_days = (cp.date - _today(cp.tz)).days
        alerts_t = asyncio.create_task(nws.active_alerts(trace, cp))
        try:
            pts = await nws.point_info(trace, cp)
            if cp.role == "en_route" and (lbl := nws.place_label(pts)):
                cp.label = f"Mile {cp.mile:.0f} ({lbl})"
            if cp_days <= NWS_FORECAST_DAYS and pts.get("forecast"):
                periods = await nws.forecast_periods(trace, cp, pts["forecast"])
        except SourceError:
            failures.append("NWS forecast")
        try:
            alerts = await alerts_t
        except SourceError:
            failures.append("NWS alerts")
        return cp, alerts, periods

    async def om_all():
        try:
            return await openmeteo.daily_for(trace, cps)
        except SourceError:
            failures.append("Open-Meteo")
            return {}

    async def faa_status():
        if req.mode != "fly":
            return None
        try:
            return await faa.fetch_status(trace, " / ".join(c.iata for c in cps if c.iata))
        except SourceError:
            failures.append("FAA airport status")
            return None

    results, om, faa_st = await asyncio.gather(asyncio.gather(*(per_checkpoint(cp) for cp in cps)), om_all(),
                                               faa_status())

    # ---- score
    findings: list[Finding] = []
    disagreements: list[str] = []
    for cp, alerts, periods in results:
        cp_f: list[Finding] = []
        if alerts:
            cp_f += risk.from_alerts(cp, alerts, req.mode)
        nws_f, nws_sev, nws_cond = risk.from_nws_forecast(cp, periods or [], req.mode)
        om_f, om_sev, om_cond = risk.from_openmeteo(cp, om.get(cp.id), req.mode)
        cp_f += nws_f + om_f
        if nws_sev is not None and om_sev is not None and abs(nws_sev - om_sev) >= 2:
            disagreements.append(cp.label)
        if faa_st:
            cp_f += risk.from_faa(cp, faa_st, days_ahead)
        cp.conditions = nws_cond or om_cond
        cp.max_severity = max((f.severity for f in cp_f), default=0)
        findings += cp_f

    if faa_st and faa_st.restricted_closures_ignored:
        assumptions.append(f"Ignored {faa_st.restricted_closures_ignored} FAA 'closures' that only restrict general "
                           "aviation / oversize aircraft (not airline passengers).")
    if req.mode == "fly" and days_ahead >= 2:
        assumptions.append("Live FAA delays/ground stops aren't used for trips 2+ days out (not predictive); "
                           "scheduled closures still are.")
    if days_ahead > NWS_FORECAST_DAYS:
        assumptions.append("Beyond the NWS 7-day window: weather comes from the Open-Meteo model only.")

    findings.sort(key=lambda f: (-f.severity, f.checkpoint_label))
    level, score = risk.overall(findings)
    conf, conf_reasons = risk.confidence(days_ahead, disagreements, failures)
    actions = risk.recommended_actions(level, req.mode, days_ahead, findings, cps, alternates,
                                       route.duration_h if route else None)

    top = findings[0] if findings and findings[0].severity >= 1 else None
    headline = (f"{level} risk — {top.title} at {top.checkpoint_label}" if top and top.severity >= 2
                else f"{level} risk — no significant disruptions found")

    date_str = f"{req.date:%a %b} {req.date.day}"  # portable (no "%-d", which fails on Windows)
    text = summary.rule_summary(o.name, d.name, date_str, req.mode, level, findings, conf)
    by = "rules"
    llm = await summary.llm_summary({
        "route": f"{o.name} → {d.name}", "mode": req.mode, "date": date_str, "risk_level": level,
        "confidence": conf, "findings": [f.model_dump(exclude={"source_url"}) for f in findings if f.severity >= 1][:12],
        "recommended_actions": actions,
    })
    if llm:
        text, by = llm, "llm"

    return Assessment(
        request=req, origin=o, destination=d, days_ahead=days_ahead, level=level, score=score, confidence=conf,
        confidence_reasons=conf_reasons, headline=headline, summary=text, summary_by=by, actions=actions,
        checkpoints=cps, findings=findings, sources=trace.calls, assumptions=assumptions, route=route,
        demo_mode=config.DEMO_MODE, generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
