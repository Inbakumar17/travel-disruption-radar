"""DEMO_MODE responses. Shapes mirror the real APIs so the same parsers run unchanged.

Scenario (relative to today, so it never goes stale):
  * Denver region (~200 mi): winter storm tomorrow and the day after - NWS Winter Storm Warning.
  * Chicago region (~100 mi): thunderstorms tomorrow, gusty.
  * Everywhere else: fair weather.
  * FAA: a real snapshot of the NAS status feed (14 Oct 2026), including GA-only closures that
    the parser must ignore.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .places import haversine_mi

CITIES = {
    "new york": ("New York", "New York", 40.7128, -74.0060, "America/New_York", 8_804_190),
    "san francisco": ("San Francisco", "California", 37.7749, -122.4194, "America/Los_Angeles", 827_526),
    "chicago": ("Chicago", "Illinois", 41.8781, -87.6298, "America/Chicago", 2_746_388),
    "denver": ("Denver", "Colorado", 39.7392, -104.9903, "America/Denver", 715_522),
    "boston": ("Boston", "Massachusetts", 42.3601, -71.0589, "America/New_York", 675_647),
    "seattle": ("Seattle", "Washington", 47.6062, -122.3321, "America/Los_Angeles", 737_015),
    "miami": ("Miami", "Florida", 25.7617, -80.1918, "America/New_York", 442_241),
    "dallas": ("Dallas", "Texas", 32.7767, -96.7970, "America/Chicago", 1_304_379),
    "atlanta": ("Atlanta", "Georgia", 33.7490, -84.3880, "America/New_York", 498_715),
    "los angeles": ("Los Angeles", "California", 34.0522, -118.2437, "America/Los_Angeles", 3_898_747),
    "salt lake city": ("Salt Lake City", "Utah", 40.7608, -111.8910, "America/Denver", 199_723),
    "kansas city": ("Kansas City", "Missouri", 39.0997, -94.5786, "America/Chicago", 508_090),
    "omaha": ("Omaha", "Nebraska", 41.2565, -95.9345, "America/Chicago", 486_051),
    "west haven": ("West Haven", "Connecticut", 41.2707, -72.9470, "America/New_York", 55_584),
    "new haven": ("New Haven", "Connecticut", 41.3083, -72.9279, "America/New_York", 134_023),
    "washington": ("Washington", "District of Columbia", 38.9072, -77.0369, "America/New_York", 689_545),
    "philadelphia": ("Philadelphia", "Pennsylvania", 39.9526, -75.1652, "America/New_York", 1_603_797),
    "phoenix": ("Phoenix", "Arizona", 33.4484, -112.0740, "America/Phoenix", 1_608_139),
    "las vegas": ("Las Vegas", "Nevada", 36.1699, -115.1398, "America/Los_Angeles", 641_903),
    "cleveland": ("Cleveland", "Ohio", 41.4993, -81.6944, "America/New_York", 372_624),
    "pittsburgh": ("Pittsburgh", "Pennsylvania", 40.4406, -79.9959, "America/New_York", 302_971),
}

DENVER = (39.74, -104.99)
CHICAGO = (41.88, -87.63)

FAA_XML = """<AIRPORT_STATUS_INFORMATION><Update_Time>Wed Oct 14 17:13:24 2026 GMT</Update_Time>
<Dtd_File>http://www.fly.faa.gov/AirportStatus.dtd</Dtd_File>
<Delay_type><Name>Ground Stop Programs</Name><Ground_Stop_List><Program><ARPT>AUS</ARPT>
<Reason>runway construction</Reason><End_Time>1:15 pm CDT</End_Time></Program></Ground_Stop_List></Delay_type>
<Delay_type><Name>Ground Delay Programs</Name><Ground_Delay_List>
<Ground_Delay><ARPT>BOS</ARPT><Reason>runway construction</Reason><Avg>1 hour and 6 minutes</Avg><Max>2 hours and 47 minutes</Max></Ground_Delay>
<Ground_Delay><ARPT>DEN</ARPT><Reason>runway construction</Reason><Avg>30 minutes</Avg><Max>1 hour and 18 minutes</Max></Ground_Delay>
</Ground_Delay_List></Delay_type>
<Delay_type><Name>General Arrival/Departure Delay Info</Name><Arrival_Departure_Delay_List>
<Delay><ARPT>ORD</ARPT><Reason>VOL:Volume</Reason><Arrival_Departure Type="Departure"><Min>16 minutes</Min><Max>30 minutes</Max><Trend>Increasing</Trend></Arrival_Departure></Delay>
<Delay><ARPT>SAN</ARPT><Reason>VOL:Compacted Demand</Reason><Arrival_Departure Type="Departure"><Min>16 minutes</Min><Max>30 minutes</Max><Trend>Increasing</Trend></Arrival_Departure></Delay>
</Arrival_Departure_Delay_List></Delay_type>
<Delay_type><Name>Airport Closures</Name><Airport_Closure_List><Airport><ARPT>SNA</ARPT>
<Reason>!SNA 10/016 SNA AD AP CLSD DLY 0630-1315 2610130630-2610171315</Reason><Start>Oct 13 at 06:30 UTC.</Start><Reopen>Oct 17 at 13:15 UTC.</Reopen></Airport></Airport_Closure_List></Delay_type>
<Delay_type><Name>Airport Closures</Name><Airport_Closure_List>
<Airport><ARPT>LAX</ARPT><Reason>!LAX 05/277 LAX AD AP CLSD TO NON SKED TRANSIENT GA ACFT EXC 24HR PPR</Reason><Start>May 27 at 18:26 UTC.</Start><Reopen>May 28 at 16:00 UTC.</Reopen></Airport>
<Airport><ARPT>SAN</ARPT><Reason>!SAN 10/004 SAN AD AP CLSD TO NON SKED TRANSIENT GA ACFT EXC PPR</Reason><Start>Oct 01 at 16:33 UTC.</Start><Reopen>Jan 01 at 08:00 UTC.</Reopen></Airport>
<Airport><ARPT>PHL</ARPT><Reason>!PHL 09/263 PHL AD AP CLSD TO NON SKED ACFT WINGSPAN MORE THAN 214FT</Reason><Start>Sep 30 at 18:06 UTC.</Start><Reopen>Oct 31 at 12:00 UTC.</Reopen></Airport>
</Airport_Closure_List></Delay_type></AIRPORT_STATUS_INFORMATION>"""


def _today() -> date:
    return datetime.now(ZoneInfo("America/New_York")).date()


def _nearest_city(lat: float, lon: float):
    return min(CITIES.values(), key=lambda c: haversine_mi(lat, lon, c[2], c[3]))


def _tz(lat: float, lon: float) -> str:
    return _nearest_city(lat, lon)[4]


def _weather(lat: float, lon: float, d: date) -> dict:
    """Scenario weather for a point/day."""
    offset = (d - _today()).days
    if haversine_mi(lat, lon, *DENVER) < 200 and offset in (1, 2):
        return {"kind": "snow", "code": 75 if offset == 1 else 73, "snow": 7.5 if offset == 1 else 2.4,
                "precip": 0.7, "pop": 90, "gust": 41, "hi": 31, "lo": 22}
    if haversine_mi(lat, lon, *CHICAGO) < 100 and offset == 1:
        return {"kind": "storms", "code": 95, "snow": 0, "precip": 1.1, "pop": 70, "gust": 47, "hi": 71, "lo": 55}
    base = 68 - (lat - 30) * 1.2
    return {"kind": "fair", "code": 2 if (int(abs(lat * 10)) + offset) % 3 else 1, "snow": 0, "precip": 0,
            "pop": 10, "gust": 18, "hi": round(base + 8), "lo": round(base - 6)}


def _geocode(params: dict) -> dict:
    name = params.get("name", "").strip().lower()
    c = CITIES.get(name)
    if not c:
        return {"generationtime_ms": 0.1}
    return {"results": [{"name": c[0], "admin1": c[1], "latitude": c[2], "longitude": c[3], "timezone": c[4],
                         "population": c[5], "country_code": "US"}]}


def _points(lat: float, lon: float) -> dict:
    from .places import _STATE_ABBR

    city = _nearest_city(lat, lon)
    rel_city = city[0]
    state_abbr = _STATE_ABBR.get(city[1].lower(), city[1][:2].upper())
    return {"properties": {
        "forecast": f"https://api.weather.gov/gridpoints/DEMO/{lat:.4f},{lon:.4f}/forecast",
        "forecastHourly": f"https://api.weather.gov/gridpoints/DEMO/{lat:.4f},{lon:.4f}/forecast/hourly",
        "relativeLocation": {"properties": {"city": rel_city, "state": state_abbr}},
    }}


def _forecast(lat: float, lon: float) -> dict:
    z = ZoneInfo(_tz(lat, lon))
    periods, n = [], 1
    for i in range(7):
        d = _today() + timedelta(days=i)
        w = _weather(lat, lon, d)
        for daytime in (True, False):
            start = datetime.combine(d, time(6 if daytime else 18), tzinfo=z)
            if w["kind"] == "snow":
                short = "Heavy Snow" if w["snow"] > 5 else "Snow Likely"
                detail = f"{short}. Total accumulation of {w['snow']:.0f} inches possible. Gusts as high as {w['gust']} mph."
            elif w["kind"] == "storms":
                short = "Showers And Thunderstorms"
                detail = f"Showers and thunderstorms likely. Gusts as high as {w['gust']} mph."
            else:
                short = "Mostly Sunny" if daytime else "Mostly Clear"
                detail = f"{short}, with a high near {w['hi']}."
            periods.append({
                "number": n, "name": d.strftime("%A") + ("" if daytime else " Night"),
                "startTime": start.isoformat(), "endTime": (start + timedelta(hours=12)).isoformat(),
                "isDaytime": daytime, "temperature": w["hi"] if daytime else w["lo"], "temperatureUnit": "F",
                "probabilityOfPrecipitation": {"value": w["pop"]},
                "windSpeed": "15 to 25 mph" if w["kind"] != "fair" else "5 to 10 mph",
                "shortForecast": short, "detailedForecast": detail,
            })
            n += 1
    return {"properties": {"periods": periods}}


def _alerts(lat: float, lon: float) -> dict:
    feats = []
    if haversine_mi(lat, lon, *DENVER) < 200:
        z = ZoneInfo("America/Denver")
        on = datetime.combine(_today() + timedelta(days=1), time(3), tzinfo=z)
        off = datetime.combine(_today() + timedelta(days=2), time(18), tzinfo=z)
        feats.append({"properties": {
            "event": "Winter Storm Warning", "severity": "Severe", "certainty": "Likely", "urgency": "Expected",
            "onset": on.isoformat(), "ends": off.isoformat(), "expires": off.isoformat(),
            "headline": "Winter Storm Warning issued for the Denver metro and I-70/I-25 corridors",
            "description": "Heavy snow expected. Total snow accumulations of 6 to 12 inches.",
            "instruction": "Travel could be very difficult. The hazardous conditions could impact the morning "
                           "and evening commutes.\n\nCall 511 for road information.",
            "areaDesc": "Denver; Boulder; Castle Rock"}})
    if haversine_mi(lat, lon, 40.71, -74.0) < 40:  # a non-travel alert to show filtering
        z = ZoneInfo("America/New_York")
        feats.append({"properties": {
            "event": "Rip Current Statement", "severity": "Moderate",
            "onset": datetime.combine(_today(), time(6), tzinfo=z).isoformat(),
            "ends": datetime.combine(_today() + timedelta(days=2), time(20), tzinfo=z).isoformat(),
            "headline": "Rip Current Statement for Atlantic-facing beaches", "areaDesc": "Coastal Queens; Kings"}})
    return {"type": "FeatureCollection", "features": feats}


def _openmeteo(params: dict):
    lats = [float(x) for x in str(params["latitude"]).split(",")]
    lons = [float(x) for x in str(params["longitude"]).split(",")]
    start, end = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    out = []
    for lat, lon in zip(lats, lons):
        ws = [_weather(lat, lon, d) for d in days]
        out.append({"latitude": lat, "longitude": lon, "daily": {
            "time": [d.isoformat() for d in days],
            "weather_code": [w["code"] for w in ws],
            "temperature_2m_max": [w["hi"] for w in ws], "temperature_2m_min": [w["lo"] for w in ws],
            "precipitation_sum": [w["precip"] for w in ws], "snowfall_sum": [w["snow"] for w in ws],
            "precipitation_probability_max": [w["pop"] for w in ws],
            "wind_gusts_10m_max": [w["gust"] for w in ws]}})
    return out if len(out) > 1 else out[0]


def _osrm(url: str) -> dict:
    m = re.search(r"driving/([-\d.]+),([-\d.]+);([-\d.]+),([-\d.]+)", url)
    lon1, lat1, lon2, lat2 = map(float, m.groups())
    steps = 40
    # gentle curve so the map line doesn't look like a ruler
    coords = []
    for i in range(steps + 1):
        t = i / steps
        bend = math.sin(t * math.pi) * 0.6
        coords.append([lon1 + (lon2 - lon1) * t, lat1 + (lat2 - lat1) * t + bend])
    straight = haversine_mi(lat1, lon1, lat2, lon2)
    dist_m = straight * 1.18 * 1609.344
    return {"code": "Ok", "routes": [{"distance": dist_m, "duration": dist_m / 1609.344 / 58 * 3600,
                                      "geometry": {"type": "LineString", "coordinates": coords}}]}


def respond(url: str, params: dict):
    if "geocoding-api.open-meteo.com" in url:
        return _geocode(params)
    if "api.open-meteo.com" in url:
        return _openmeteo(params)
    if "nasstatus.faa.gov" in url:
        return FAA_XML
    if "router.project-osrm.org" in url:
        return _osrm(url)
    if "api.weather.gov/alerts" in url:
        lat, lon = map(float, params["point"].split(","))
        return _alerts(lat, lon)
    if m := re.search(r"api\.weather\.gov/points/([-\d.]+),([-\d.]+)", url):
        return _points(float(m.group(1)), float(m.group(2)))
    if m := re.search(r"gridpoints/DEMO/([-\d.]+),([-\d.]+)/forecast", url):
        return _forecast(float(m.group(1)), float(m.group(2)))
    raise RuntimeError(f"No demo fixture for {url}")
