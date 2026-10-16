"""FAA National Airspace System status (nasstatus.faa.gov): live ground stops, ground delay
programs, departure/arrival delays and airport closures. Free, no key, XML.

Important nuance: this feed is *current* status, not a forecast. Ground stops right
now say little about next Tuesday, so app.risk only weights them for same-day travel.
Closures are different - they carry a start/reopen window we can check against the date.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..net import Trace

URL = "https://nasstatus.faa.gov/api/airport-status-information"
PUBLIC_URL = "https://nasstatus.faa.gov/"

# Closures that only apply to some aircraft (general aviation, oversize, prior-permission).
# These appear in the feed every day and are not disruptions for airline passengers.
_RESTRICTED_MARKERS = ("CLSD TO", "NON SKED", "TRANSIENT", "WINGSPAN", " EXC ", "PPR")
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


@dataclass
class FaaEvent:
    iata: str
    kind: str  # ground_stop | ground_delay | delay | closure
    reason: str
    detail: str
    avg_minutes: Optional[int] = None
    max_minutes: Optional[int] = None
    start: Optional[datetime] = None
    end: Optional[datetime] = None


@dataclass
class FaaStatus:
    updated: str = ""
    events: dict[str, list[FaaEvent]] = field(default_factory=dict)
    restricted_closures_ignored: int = 0


def to_minutes(text: str | None) -> Optional[int]:
    if not text:
        return None
    h = re.search(r"(\d+)\s*hour", text)
    m = re.search(r"(\d+)\s*min", text)
    if not h and not m:
        return None
    return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)


def parse_faa_time(text: str | None, ref: datetime) -> Optional[datetime]:
    """'Oct 13 at 06:30 UTC.' -> aware datetime. The feed omits the year, so infer it."""
    if not text:
        return None
    m = re.search(r"([A-Z][a-z]{2})\s+(\d{1,2})\s+at\s+(\d{1,2}):(\d{2})", text)
    if not m or m.group(1) not in _MONTHS:
        return None
    dt = datetime(ref.year, _MONTHS[m.group(1)], int(m.group(2)), int(m.group(3)), int(m.group(4)), tzinfo=timezone.utc)
    if dt < ref - timedelta(days=200):
        dt = dt.replace(year=ref.year + 1)
    return dt


def humanize_reason(reason: str) -> str:
    # Delay reasons look like "WX:Thunderstorms" or "VOL:Volume".
    if ":" in reason and len(reason.split(":")[0]) <= 4:
        code, text = reason.split(":", 1)
        prefix = {"WX": "Weather", "VOL": "Traffic volume", "EQ": "Equipment", "RWY": "Runway"}.get(code.upper(), code)
        return f"{prefix} - {text.strip()}"
    return reason


def parse(xml_text: str, now: Optional[datetime] = None) -> FaaStatus:
    now = now or datetime.now(timezone.utc)
    root = ET.fromstring(xml_text)
    status = FaaStatus(updated=(root.findtext("Update_Time") or "").strip())

    def add(ev: FaaEvent) -> None:
        status.events.setdefault(ev.iata, []).append(ev)

    # Detect sections by their list element, not the section title (more robust).
    for prog in root.iter("Program"):
        add(FaaEvent(prog.findtext("ARPT", ""), "ground_stop", prog.findtext("Reason", ""),
                     f"Ground stop until {prog.findtext('End_Time', 'further notice')}"))
    for gd in root.iter("Ground_Delay"):
        avg, mx = gd.findtext("Avg", ""), gd.findtext("Max", "")
        add(FaaEvent(gd.findtext("ARPT", ""), "ground_delay", gd.findtext("Reason", ""),
                     f"Ground delay program: average {avg}, max {mx}", to_minutes(avg), to_minutes(mx)))
    for d in root.iter("Delay"):
        for ad in d.findall("Arrival_Departure"):
            lo, hi = ad.findtext("Min", ""), ad.findtext("Max", "")
            trend = ad.findtext("Trend", "")
            add(FaaEvent(d.findtext("ARPT", ""), "delay", d.findtext("Reason", ""),
                         f"{ad.get('Type', 'General')} delays {lo}-{hi}" + (f", {trend.lower()}" if trend else ""),
                         to_minutes(lo), to_minutes(hi)))
    for ap in root.iter("Airport"):
        reason = ap.findtext("Reason", "")
        if any(mk in f" {reason.upper()} " for mk in _RESTRICTED_MARKERS):
            status.restricted_closures_ignored += 1
            continue
        start = parse_faa_time(ap.findtext("Start"), now)
        end = parse_faa_time(ap.findtext("Reopen"), now)
        if start and end and end < start:
            end = end.replace(year=end.year + 1)
        add(FaaEvent(ap.findtext("ARPT", ""), "closure", reason,
                     f"Airport closed {ap.findtext('Start', '').strip(' .')} to {ap.findtext('Reopen', '').strip(' .')}",
                     start=start, end=end))
    return status


async def fetch_status(trace: Trace, target: str) -> FaaStatus:
    text = await trace.fetch("FAA NAS Status", target, URL, kind="text", ttl=120)
    return parse(text)
