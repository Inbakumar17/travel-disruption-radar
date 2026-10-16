# Travel Disruption Radar

Enter a U.S. trip — origin, destination, date, fly or drive — and get one answer instead of five browser tabs:
**how likely the trip is to be disrupted, the evidence behind it, and what Ops should do.**

```
New York → Denver, tomorrow, fly
  HIGH risk (80/100) · confidence High
  Main driver: Winter Storm Warning at Denver (DEN), Thu 3 AM → Fri 6 PM.
  Corroborated by NWS Forecast and Open-Meteo Forecast.
  → Flag to traveller and manager today; consider moving the flight; check airline waiver.
```

---

## Run it

Requires Python 3.10+.

```bash
# macOS / Linux
./run.sh            # live data
./run.sh demo       # built-in demo data (works offline)

# Windows (PowerShell), from the project folder
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:DEMO_MODE="1"     # demo data; use "0" for live data
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Then open http://127.0.0.1:8000. API docs are at http://127.0.0.1:8000/docs.

**Windows notes:** the server is started with `python -m uvicorn` rather than `uvicorn.exe` because some managed
machines (Device Guard) block pip's generated `.exe` launchers. `tzdata` is in the requirements because Windows has
no built-in time-zone database. (A `run.bat` helper isn't included in the emailed zip, as mail filters block `.bat` files.)
Optional settings go in `.env` (copy `.env.example`). Tests: `pytest -q` (24 tests, no network needed).

---

## Data sources (all free, no paid keys)

| Source | What it gives us | Why it's here |
|---|---|---|
| **NWS Alerts** — api.weather.gov | Official watches/warnings/advisories at a point | The authoritative "this is dangerous" signal in the U.S. |
| **NWS Forecast** — api.weather.gov | 7-day, 12-hour-period forecast text, precip chance, wind | Human-curated forecast; covers hazards before alerts are issued |
| **Open-Meteo** | Daily model forecast up to 16 days: snow, precip, gusts, weather code | Second independent opinion; the only forecast beyond 7 days |
| **FAA NAS Status** — nasstatus.faa.gov | Live ground stops, ground-delay programs, delays, closures | Airport problems that aren't weather (construction, volume, equipment) |
| **OSRM** — router.project-osrm.org | Driving route, distance, duration | Lets us check conditions *along* a drive, not just at the ends |
| **Open-Meteo Geocoding** | Free text → coordinates and time zone | Accepts "Austin, TX" as well as airport codes |

Every response includes a **Sources checked** table: each call, what it was for, success/failure and latency.

---

## How it works

```
input ──► resolve places ──► build checkpoints ──► fetch all sources in parallel ──► score ──► explain
          (geocode / IATA)   fly: 2 airports        (one retry, TTL cache,          (rules)   (template,
                             drive: route sampled    failures tolerated)                       or LLM)
                             every ~150 mi
```

- **`app/sources/*`** — one adapter per source. They only fetch and parse.
- **`app/risk.py`** — all judgement lives here: readable rules turning raw data into *findings* with a 0–3 severity
  (Info / Low / Moderate / High), each tagged with its source and time window.
- **`app/assess.py`** — orchestration: concurrency, partial failure, assumptions, confidence.
- **`app/summary.py`** — briefing text. Optional Claude call (see "Where AI is used").
- **`app/fixtures.py`** — demo-mode data that mirrors the real API shapes.
- **`static/index.html`** — single-page UI (vanilla JS + Leaflet map).

### Scoring

- **Level = worst finding** (Minimal / Low / Moderate / High). One blizzard is enough to cancel a trip; averaging would hide it.
- **Score (0–100)** adds a little when *several places* have problems (both ends of a flight). Several sources agreeing
  about the *same place* is **corroboration, not extra risk** — it raises trust, not the score.
- **Confidence** is separate from risk:
  - starts from how far out the trip is (0–2 days High, 3–5 Medium, 6+ Low),
  - drops a notch if NWS and Open-Meteo disagree by 2+ levels at a checkpoint,
  - drops a notch if any source failed — and says which.

### Decisions worth calling out

1. **Rules decide the risk; AI only narrates.** Ops needs to know *why* a trip was flagged and get the same answer twice.
   Thresholds are at the top of each rule function so Ops can tune them.
2. **Live FAA status is time-aware.** A ground stop *now* matters for today, is a weak hint for tomorrow (capped at Low),
   and is ignored 2+ days out. Scheduled closures are checked against their actual start/reopen window.
3. **Filtering noise matters as much as finding signal.** The FAA feed lists "closures" every day that only apply to
   general aviation or oversized aircraft (LAX, SAN, PHL on 14 Oct). Showing those as "LAX CLOSED" would destroy trust,
   so they're filtered and counted in Assumptions. Same for non-travel NWS products (rip currents, frost).
4. **Drives are checked along the route, on the right day.** A 19-hour Chicago → Denver drive assumes 10 driving
   hours/day, so the Denver end is checked for *day two*.
5. **Fail soft, say so.** One source down never blocks an answer; it lowers confidence and is shown in red.
6. **Show the "not relevant" items too** (collapsed). It proves the alert was seen and deliberately discounted.

### Assumptions (also shown in the UI per trip)

- Flights are assessed at the airport typed, or the nearest of ~55 major U.S. airports to the city typed.
- Nonstop flights only — connections aren't modelled.
- Weather beyond 7 days is model-only and low confidence; nothing beyond 15 days.
- Drives start at 8 am and cover 10 hours/day.

---

## Where AI is used

**How I Used AI**

I used AI as a supporting tool during development. I mainly used it to verify information sources, check the status of running processes, and get recommendations when needed. I also used AI to assist with coding, simplify development tasks, identify bugs, and find solutions to technical issues more efficiently.

**What AI Helped With:** AI helped me check relevant sources, understand technical issues, get recommendations, and make coding and debugging easier.

**What I Did:** I defined the project requirements, ran and tested the application on my Windows machine, reviewed the results, identified issues during testing, and worked on fixing them. I also made decisions about the application's functionality and interface and reviewed the implementation to ensure it met the project requirements.

**Where I Did Not Use AI:** I kept the risk-scoring logic based on predefined rules rather than using AI to determine risk levels. This made the results more consistent, transparent, and easier to understand and verify.


---

## Before real users: what I'd change

**Data & coverage**
- Add **flight-specific data** (FlightAware / Cirium / airline APIs): the actual flight's cancellation history and
  inbound aircraft. Weather at airports is a proxy; this would be the biggest accuracy win.
- Model **connections**, and add **state DOT 511 road closures** and wildfire/smoke for drives.
- **Hurricanes:** NHC cone data for multi-day tropical risk.
- Replace the public OSRM demo server (no SLA) with a hosted router, and the free OpenStreetMap map tiles with a
  commercial tile provider (OSM's tile servers aren't meant for production traffic).

**Accuracy**
- **Backtest the thresholds** against historical BTS on-time data + archived NWS alerts, then tune per airport
  (1" of snow means little in Denver, a lot in Atlanta).
- Use hourly forecasts and the traveller's actual departure time instead of the whole day.

**Product**
- **Integrate with the travel-booking system** (Concur/Navan etc.) so trips are checked automatically, and
  **notify** (Slack/email) when a trip's risk *changes*, instead of someone remembering to check.
- A dashboard of all upcoming trips sorted by risk.
- Feedback button ("was this right?") to measure precision/recall.

**Engineering**
- Shared cache (Redis) and a background poller so we stay well inside NWS/FAA rate limits.
- Auth (SSO), audit log of who looked at which trip — travel itineraries are personal data.
- Structured logging, metrics per source (error rate, latency), alerting when a source breaks or changes format.
- Contract tests that run nightly against the live APIs to catch schema changes.
- Containerise and deploy behind the company's gateway.

---

## Project layout

```
app/
  main.py         FastAPI routes
  assess.py       orchestration
  risk.py         scoring rules (the part to read first)
  summary.py      briefing text, optional LLM
  places.py       geocoding + airport table
  net.py          HTTP client, cache, retry, per-request trace
  fixtures.py     demo-mode data
  sources/        nws.py, openmeteo.py, faa.py, osrm.py
static/index.html UI
tests/            unit + end-to-end (demo mode)
```
