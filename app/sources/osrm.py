"""OSRM public routing server: driving route geometry, distance and duration. Free, no key.

The public demo server is rate-limited and has no SLA - fine for a prototype,
replace with a self-hosted OSRM or a paid router before production.
"""
from __future__ import annotations

from ..models import Place
from ..net import Trace
from ..places import haversine_mi

URL = "https://router.project-osrm.org/route/v1/driving/{o_lon},{o_lat};{d_lon},{d_lat}"


async def route(trace: Trace, o: Place, d: Place) -> dict:
    url = URL.format(o_lon=f"{o.lon:.5f}", o_lat=f"{o.lat:.5f}", d_lon=f"{d.lon:.5f}", d_lat=f"{d.lat:.5f}")
    data = await trace.fetch("OSRM Routing", f"{o.name} → {d.name}", url,
                             {"overview": "simplified", "geometries": "geojson"}, ttl=86400)
    if data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError(f"No driving route found between {o.name} and {d.name}.")
    r = data["routes"][0]
    return {
        "distance_mi": r["distance"] / 1609.344,
        "duration_h": r["duration"] / 3600,
        "coords": r["geometry"]["coordinates"],  # [lon, lat]
    }


def sample_points(coords: list[list[float]], total_mi: float, total_h: float, every_mi: float = 150,
                  max_points: int = 8) -> list[dict]:
    """Pick evenly spaced points along the route (excluding the endpoints)."""
    if total_mi < every_mi * 1.2 or len(coords) < 3:
        return []
    n = min(max_points, int(total_mi // every_mi))
    spacing = total_mi / (n + 1)

    # cumulative distance along the polyline
    cum = [0.0]
    for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:]):
        cum.append(cum[-1] + haversine_mi(lat1, lon1, lat2, lon2))
    scale = total_mi / cum[-1] if cum[-1] else 1.0  # polyline is simplified; rescale to router distance

    out, target, j = [], spacing, 0
    for i in range(1, len(coords)):
        while j < n and cum[i] * scale >= target:
            lon, lat = coords[i]
            out.append({"lat": lat, "lon": lon, "mile": round(target), "eta_h": total_h * target / total_mi})
            j += 1
            target += spacing
    return out
