"""FastAPI entry point. Run:  uvicorn app.main:app --reload"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .assess import AssessmentError, assess
from .models import Assessment, AssessRequest
from .net import close_client
from .places import AIRPORTS, PlaceNotFound

STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await close_client()


app = FastAPI(title="Travel Disruption Radar", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
async def health():
    return {"ok": True, "demo_mode": config.DEMO_MODE, "llm_summary": bool(config.ANTHROPIC_API_KEY)}


@app.get("/api/airports")
async def airports():
    return sorted(({"iata": a.iata, "city": a.city} for a in AIRPORTS.values()), key=lambda x: x["iata"])


@app.post("/api/assess", response_model=Assessment)
async def assess_trip(req: AssessRequest):
    try:
        return await assess(req)
    except (PlaceNotFound, AssessmentError) as e:
        raise HTTPException(status_code=422, detail=str(e))
