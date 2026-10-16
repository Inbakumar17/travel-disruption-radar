from datetime import date, datetime, timezone

from app import risk
from app.fixtures import FAA_XML
from app.models import Checkpoint, Finding
from app.sources import faa

D = date(2026, 10, 15)


def cp(**kw):
    base = dict(id="destination", label="Denver (DEN)", role="destination", lat=39.86, lon=-104.67,
                tz="America/Denver", date=D, iata="DEN")
    base.update(kw)
    return Checkpoint(**base)


# --- alerts ------------------------------------------------------------------

def test_alert_severity_mapping():
    assert risk.alert_severity("Winter Storm Warning", "fly") == 3
    assert risk.alert_severity("Winter Storm Watch", "fly") == 2
    assert risk.alert_severity("Winter Weather Advisory", "drive") == 2
    assert risk.alert_severity("Dense Fog Advisory", "fly") == 2
    assert risk.alert_severity("Dense Fog Advisory", "drive") == 1
    assert risk.alert_severity("Flash Flood Warning", "fly") == 2
    assert risk.alert_severity("Flash Flood Warning", "drive") == 3
    assert risk.alert_severity("Rip Current Statement", "fly") == 0
    assert risk.alert_severity("Freeze Warning", "drive") == 0   # agricultural, not a travel hazard
    assert risk.alert_severity("Wind Advisory", "drive") == 1


def test_alert_outside_travel_day_is_dropped():
    alert = {"event": "Winter Storm Warning", "onset": "2026-10-17T03:00:00-06:00", "ends": "2026-10-18T03:00:00-06:00"}
    assert risk.from_alerts(cp(), [alert], "fly") == []


def test_alert_overlapping_travel_day_is_kept_and_deduped():
    a = {"event": "Winter Storm Warning", "onset": "2026-10-14T20:00:00-06:00", "ends": "2026-10-15T09:00:00-06:00",
         "headline": "WSW"}
    out = risk.from_alerts(cp(), [a, dict(a)], "fly")
    assert len(out) == 1 and out[0].severity == 3 and out[0].window


# --- forecasts ---------------------------------------------------------------

def test_nws_period_rules():
    snow = {"shortForecast": "Snow Likely", "probabilityOfPrecipitation": {"value": 70}, "windSpeed": "10 mph"}
    assert risk.nws_period_severity(snow, "fly")[0] == 2
    chance = {"shortForecast": "Chance Snow Showers", "probabilityOfPrecipitation": {"value": 30}, "windSpeed": "5 mph"}
    assert risk.nws_period_severity(chance, "fly")[0] == 1
    windy = {"shortForecast": "Sunny", "windSpeed": "20 to 45 mph"}
    assert risk.nws_period_severity(windy, "drive")[0] == 2
    clear = {"shortForecast": "Mostly Sunny", "windSpeed": "5 to 10 mph"}
    assert risk.nws_period_severity(clear, "fly")[0] == 0


def test_nws_forecast_only_uses_travel_day():
    periods = [
        {"name": "Wednesday", "startTime": "2026-10-14T06:00:00-06:00", "shortForecast": "Heavy Snow", "isDaytime": True},
        {"name": "Thursday", "startTime": "2026-10-15T06:00:00-06:00", "shortForecast": "Sunny", "isDaytime": True,
         "windSpeed": "5 mph"},
    ]
    findings, sev, cond = risk.from_nws_forecast(cp(), periods, "fly")
    assert findings == [] and sev == 0 and cond == "Sunny"


def test_openmeteo_thresholds():
    assert risk.openmeteo_severity({"snowfall_sum": 5, "weather_code": 75}, "fly")[0] == 3
    assert risk.openmeteo_severity({"snowfall_sum": 1.2, "weather_code": 73}, "fly")[0] == 2
    assert risk.openmeteo_severity({"weather_code": 95}, "fly")[0] == 2
    assert risk.openmeteo_severity({"weather_code": 66}, "drive")[0] == 3
    assert risk.openmeteo_severity({"wind_gusts_10m_max": 62, "weather_code": 1}, "fly")[0] == 3
    assert risk.openmeteo_severity({"weather_code": 1, "wind_gusts_10m_max": 12}, "fly")[0] == 0


# --- FAA ---------------------------------------------------------------------

def test_faa_live_status_weighting_by_horizon():
    st = faa.parse(FAA_XML, datetime(2026, 10, 14, 17, tzinfo=timezone.utc))
    today = risk.from_faa(cp(date=date(2026, 10, 14)), st, days_ahead=0)
    tomorrow = risk.from_faa(cp(), st, days_ahead=1)
    later = risk.from_faa(cp(date=date(2026, 10, 18)), st, days_ahead=4)
    assert today[0].severity == 2           # DEN ground delay, avg 30 min
    assert tomorrow[0].severity == 1        # live now, may clear
    assert later == []                      # not predictive


def test_faa_closure_respects_window():
    st = faa.parse(FAA_XML, datetime(2026, 10, 14, 17, tzinfo=timezone.utc))
    sna = cp(id="origin", label="Orange County (SNA)", iata="SNA", tz="America/Los_Angeles")
    assert risk.from_faa(sna.model_copy(update={"date": date(2026, 10, 15)}), st, 1)[0].severity == 3
    assert risk.from_faa(sna.model_copy(update={"date": date(2026, 10, 20)}), st, 6) == []


# --- aggregation -------------------------------------------------------------

def _f(cid, sev, src="X"):
    return Finding(checkpoint_id=cid, checkpoint_label=cid, category="forecast", severity=sev, title="t",
                   detail="d", source=src)


def test_corroboration_does_not_inflate_score():
    one_place = [_f("destination", 3, "NWS Alerts"), _f("destination", 3, "NWS Forecast"), _f("destination", 3, "OM")]
    two_places = [_f("destination", 3), _f("origin", 2)]
    assert risk.overall(one_place) == ("High", 80)
    assert risk.overall(two_places) == ("High", 87)
    assert risk.overall([]) == ("Minimal", 5)


def test_confidence_downgrades():
    assert risk.confidence(1, [], [])[0] == "High"
    assert risk.confidence(1, ["Denver"], [])[0] == "Medium"
    assert risk.confidence(4, [], ["FAA airport status"])[0] == "Low"
    assert risk.confidence(9, [], [])[0] == "Low"
