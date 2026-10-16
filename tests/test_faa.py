from datetime import datetime, timezone

from app.fixtures import FAA_XML
from app.sources import faa

NOW = datetime(2026, 10, 14, 17, 0, tzinfo=timezone.utc)


def test_to_minutes():
    assert faa.to_minutes("1 hour and 6 minutes") == 66
    assert faa.to_minutes("30 minutes") == 30
    assert faa.to_minutes("2 hours") == 120
    assert faa.to_minutes("") is None


def test_parse_programs_and_delays():
    st = faa.parse(FAA_XML, NOW)
    assert st.events["AUS"][0].kind == "ground_stop"
    bos = st.events["BOS"][0]
    assert bos.kind == "ground_delay" and bos.avg_minutes == 66 and bos.max_minutes == 167
    assert st.events["ORD"][0].kind == "delay" and st.events["ORD"][0].max_minutes == 30


def test_general_aviation_closures_are_ignored():
    st = faa.parse(FAA_XML, NOW)
    assert st.restricted_closures_ignored == 3
    assert "LAX" not in st.events and "PHL" not in st.events
    assert all(e.kind != "closure" for e in st.events.get("SAN", []))


def test_full_closure_window_parsed_with_inferred_year():
    st = faa.parse(FAA_XML, NOW)
    sna = st.events["SNA"][0]
    assert sna.kind == "closure"
    assert sna.start == datetime(2026, 10, 13, 6, 30, tzinfo=timezone.utc)
    assert sna.end == datetime(2026, 10, 17, 13, 15, tzinfo=timezone.utc)


def test_year_rollover():
    dec = datetime(2026, 12, 20, tzinfo=timezone.utc)
    assert faa.parse_faa_time("Jan 01 at 08:00 UTC.", dec).year == 2027


def test_humanize_reason():
    assert faa.humanize_reason("WX:Thunderstorms") == "Weather - Thunderstorms"
    assert faa.humanize_reason("runway construction") == "runway construction"
