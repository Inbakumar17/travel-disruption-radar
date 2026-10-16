"""End-to-end through the HTTP API using DEMO_MODE fixtures (no network)."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import fixtures
from app.main import app

client = TestClient(app)
TOMORROW = (fixtures._today() + timedelta(days=1)).isoformat()


def post(**body):
    return client.post("/api/assess", json={"date": TOMORROW, "mode": "fly", **body})


def test_storm_destination_is_high_risk():
    r = post(origin="New York", destination="Denver")
    assert r.status_code == 200
    a = r.json()
    assert a["level"] == "High" and a["confidence"] == "High"
    sources = {f["source"] for f in a["findings"] if f["checkpoint_id"] == "destination" and f["severity"] == 3}
    assert {"NWS Alerts", "NWS Forecast", "Open-Meteo Forecast"} <= sources   # three independent signals
    assert any("Flag this trip" in x for x in a["actions"])
    assert a["demo_mode"] is True


def test_clear_route_is_low_and_says_no_action():
    a = post(origin="Boston", destination="Miami").json()
    assert a["level"] in ("Minimal", "Low")
    assert a["actions"][0].startswith("No action needed")


def test_airport_codes_accepted():
    a = post(origin="jfk", destination="SFO").json()
    assert [c["iata"] for c in a["checkpoints"]] == ["JFK", "SFO"]


def test_drive_mode_samples_route_and_flags_multiday():
    a = post(origin="Chicago", destination="Denver", mode="drive").json()
    roles = [c["role"] for c in a["checkpoints"]]
    assert roles[0] == "origin" and roles[-1] == "destination" and roles.count("en_route") >= 3
    assert a["route"]["distance_mi"] > 900
    assert any("overnight" in x for x in a["actions"])
    assert a["checkpoints"][-1]["date"] > a["checkpoints"][0]["date"]   # arrives next day


@pytest.mark.parametrize("body,msg", [
    ({"origin": "Atlantis", "destination": "Denver"}, "Couldn't find"),
    ({"origin": "Boston", "destination": "Denver", "date": "2020-01-01"}, "in the past"),
    ({"origin": "LGA", "destination": "LGA"}, "Both ends"),
])
def test_validation_errors(body, msg):
    r = post(**body)
    assert r.status_code == 422 and msg in r.json()["detail"]


def test_one_source_down_degrades_gracefully(monkeypatch):
    real = fixtures.respond

    def flaky(url, params):
        if "nasstatus.faa.gov" in url:
            raise RuntimeError("503 Service Unavailable")
        return real(url, params)

    monkeypatch.setattr(fixtures, "respond", flaky)
    r = post(origin="New York", destination="Denver")
    assert r.status_code == 200
    a = r.json()
    assert a["level"] == "High"                         # weather evidence still carries it
    assert a["confidence"] == "Medium"                  # but we say we're less sure
    assert any("FAA" in reason for reason in a["confidence_reasons"])
    assert any(not s["ok"] and s["source"] == "FAA NAS Status" for s in a["sources"])
