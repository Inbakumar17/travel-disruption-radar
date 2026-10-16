"""Turn free-text input ("New York", "Austin, TX", "SFO") into coordinates and airports."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .models import Place
from .net import Trace

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"


@dataclass(frozen=True)
class Airport:
    iata: str
    city: str
    lat: float
    lon: float
    tz: str


_ET, _CT, _MT, _PT = "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"

# Major U.S. commercial airports (the ones the FAA NAS status feed most often reports on).
AIRPORTS: dict[str, Airport] = {
    a.iata: a
    for a in [
        Airport("ATL", "Atlanta", 33.6407, -84.4277, _ET),
        Airport("LAX", "Los Angeles", 33.9416, -118.4085, _PT),
        Airport("ORD", "Chicago O'Hare", 41.9742, -87.9073, _CT),
        Airport("MDW", "Chicago Midway", 41.7868, -87.7522, _CT),
        Airport("DFW", "Dallas/Fort Worth", 32.8998, -97.0403, _CT),
        Airport("DAL", "Dallas Love", 32.8471, -96.8518, _CT),
        Airport("DEN", "Denver", 39.8561, -104.6737, _MT),
        Airport("JFK", "New York JFK", 40.6413, -73.7781, _ET),
        Airport("LGA", "New York LaGuardia", 40.7769, -73.8740, _ET),
        Airport("EWR", "Newark", 40.6895, -74.1745, _ET),
        Airport("SFO", "San Francisco", 37.6213, -122.3790, _PT),
        Airport("OAK", "Oakland", 37.7126, -122.2197, _PT),
        Airport("SJC", "San Jose", 37.3639, -121.9289, _PT),
        Airport("SEA", "Seattle", 47.4502, -122.3088, _PT),
        Airport("LAS", "Las Vegas", 36.0840, -115.1537, _PT),
        Airport("MCO", "Orlando", 28.4312, -81.3081, _ET),
        Airport("CLT", "Charlotte", 35.2140, -80.9431, _ET),
        Airport("PHX", "Phoenix", 33.4342, -112.0116, "America/Phoenix"),
        Airport("IAH", "Houston Bush", 29.9902, -95.3368, _CT),
        Airport("HOU", "Houston Hobby", 29.6454, -95.2789, _CT),
        Airport("MIA", "Miami", 25.7959, -80.2870, _ET),
        Airport("FLL", "Fort Lauderdale", 26.0742, -80.1506, _ET),
        Airport("BOS", "Boston", 42.3656, -71.0096, _ET),
        Airport("MSP", "Minneapolis", 44.8848, -93.2223, _CT),
        Airport("DTW", "Detroit", 42.2162, -83.3554, "America/Detroit"),
        Airport("PHL", "Philadelphia", 39.8744, -75.2424, _ET),
        Airport("BWI", "Baltimore", 39.1754, -76.6684, _ET),
        Airport("IAD", "Washington Dulles", 38.9531, -77.4565, _ET),
        Airport("DCA", "Washington National", 38.8512, -77.0402, _ET),
        Airport("SLC", "Salt Lake City", 40.7899, -111.9791, _MT),
        Airport("SAN", "San Diego", 32.7338, -117.1933, _PT),
        Airport("SNA", "Orange County", 33.6762, -117.8675, _PT),
        Airport("TPA", "Tampa", 27.9755, -82.5332, _ET),
        Airport("PDX", "Portland", 45.5898, -122.5951, _PT),
        Airport("BNA", "Nashville", 36.1263, -86.6774, _CT),
        Airport("AUS", "Austin", 30.1975, -97.6664, _CT),
        Airport("SAT", "San Antonio", 29.5337, -98.4698, _CT),
        Airport("STL", "St. Louis", 38.7487, -90.3700, _CT),
        Airport("MCI", "Kansas City", 39.2976, -94.7139, _CT),
        Airport("RDU", "Raleigh-Durham", 35.8801, -78.7880, _ET),
        Airport("MSY", "New Orleans", 29.9934, -90.2580, _CT),
        Airport("SMF", "Sacramento", 38.6951, -121.5908, _PT),
        Airport("CLE", "Cleveland", 41.4058, -81.8539, _ET),
        Airport("PIT", "Pittsburgh", 40.4915, -80.2329, _ET),
        Airport("CMH", "Columbus", 39.9980, -82.8919, _ET),
        Airport("CVG", "Cincinnati", 39.0488, -84.6678, _ET),
        Airport("IND", "Indianapolis", 39.7173, -86.2944, "America/Indiana/Indianapolis"),
        Airport("JAX", "Jacksonville", 30.4941, -81.6879, _ET),
        Airport("RSW", "Fort Myers", 26.5362, -81.7552, _ET),
        Airport("ABQ", "Albuquerque", 35.0402, -106.6091, _MT),
        Airport("BDL", "Hartford", 41.9389, -72.6832, _ET),
        Airport("HVN", "New Haven", 41.2637, -72.8868, _ET),
        Airport("PVD", "Providence", 41.7240, -71.4283, _ET),
        Airport("BUF", "Buffalo", 42.9405, -78.7322, _ET),
        Airport("ANC", "Anchorage", 61.1743, -149.9962, "America/Anchorage"),
        Airport("HNL", "Honolulu", 21.3245, -157.9251, "Pacific/Honolulu"),
    ]
}

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
_STATE_ABBR = {v.lower(): k for k, v in STATES.items()}


class PlaceNotFound(ValueError):
    pass


def haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_airports(lat: float, lon: float, k: int = 3) -> list[tuple[Airport, float]]:
    ranked = sorted(((a, haversine_mi(lat, lon, a.lat, a.lon)) for a in AIRPORTS.values()), key=lambda t: t[1])
    return ranked[:k]


def airport_for(place: Place) -> tuple[Airport, float]:
    """The airport we assess for a place: the one typed, or the nearest major one."""
    if place.iata and place.iata in AIRPORTS:
        return AIRPORTS[place.iata], 0.0
    return nearest_airports(place.lat, place.lon, 1)[0]


def alternate_airports(primary: Airport, within_mi: float = 60) -> list[Airport]:
    return [a for a, d in nearest_airports(primary.lat, primary.lon, 6) if a.iata != primary.iata and d <= within_mi]


async def resolve(trace: Trace, text: str) -> Place:
    q = text.strip()
    code = q.upper()
    if len(code) == 3 and code.isalpha() and code in AIRPORTS:
        a = AIRPORTS[code]
        return Place(query=text, name=f"{a.city} ({a.iata})", lat=a.lat, lon=a.lon, tz=a.tz, kind="airport", iata=a.iata)

    name, _, state = q.partition(",")
    state = state.strip()
    state_full = STATES.get(state.upper(), state).lower() if state else ""

    data = await trace.fetch(
        "Open-Meteo Geocoding",
        q,
        GEOCODE_URL,
        {"name": name.strip(), "count": 10, "language": "en", "format": "json", "countryCode": "US"},
        ttl=86400,
    )
    results = [r for r in (data.get("results") or []) if r.get("country_code") == "US"]
    if state_full:
        results = [r for r in results if (r.get("admin1") or "").lower() == state_full]
    if not results:
        raise PlaceNotFound(f"Couldn't find a U.S. location matching '{text}'. Try 'City, ST' or an airport code.")

    # Among the top matches, prefer the most populous (so "Portland" -> Portland, OR, not a hamlet).
    best = max(results[:5], key=lambda r: r.get("population") or 0)
    abbr = _STATE_ABBR.get((best.get("admin1") or "").lower(), best.get("admin1") or "")
    return Place(
        query=text,
        name=f"{best['name']}, {abbr}".strip(", "),
        lat=best["latitude"],
        lon=best["longitude"],
        tz=best.get("timezone") or "America/New_York",
        kind="city",
    )
