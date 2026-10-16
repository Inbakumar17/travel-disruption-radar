"""Plain-English briefing for the Ops team.

Default: a template built from the scored findings (fast, free, deterministic).
Optional: if ANTHROPIC_API_KEY is set, Claude rewrites the briefing from the SAME
structured findings. The model never sees raw web data and never sets the score -
it only narrates evidence we already verified, and is told not to add facts.
If the call fails or times out we silently fall back to the template.
"""
from __future__ import annotations

import json

import httpx

from . import config
from .models import Finding


def rule_summary(origin: str, dest: str, travel_date: str, mode: str, level: str, findings: list[Finding],
                 conf: str) -> str:
    verb = "flight" if mode == "fly" else "drive"
    key = [f for f in findings if f.severity >= 2]
    if not key:
        minor = [f for f in findings if f.severity == 1]
        tail = f" Minor notes: {minor[0].title} at {minor[0].checkpoint_label}." if minor else ""
        return (f"No significant disruptions expected for the {origin} → {dest} {verb} on {travel_date}.{tail} "
                f"Confidence: {conf.lower()}.")
    lead = key[0]
    s = (f"{level} disruption risk for the {origin} → {dest} {verb} on {travel_date}. "
         f"Main driver: {lead.title} at {lead.checkpoint_label}")
    s += f" ({lead.window})." if lead.window else "."
    backing = sorted({f.source for f in key if f.checkpoint_id == lead.checkpoint_id and f.source != lead.source})
    if backing:
        s += f" Corroborated by {' and '.join(backing)}."
    others = []
    for f in key:
        if f.checkpoint_id != lead.checkpoint_id and f.checkpoint_label not in [o[0] for o in others]:
            others.append((f.checkpoint_label, f.title))
    if others:
        s += " Also: " + "; ".join(f"{t} at {p}" for p, t in others[:3]) + "."
    s += f" Confidence: {conf.lower()}."
    return s


async def llm_summary(context: dict) -> str | None:
    if not config.ANTHROPIC_API_KEY or config.DEMO_MODE:
        return None
    system = (
        "You write travel-disruption briefings for a corporate Operations team. "
        "Use ONLY the JSON facts provided. Do not invent weather, delays, times or advice not supported by the "
        "findings. Do not change the risk level. 2-4 sentences, plain text, no markdown, no greeting. "
        "Lead with the risk level and the single most important reason, name the place and time window, "
        "then say what Ops should do."
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": config.ANTHROPIC_MODEL, "max_tokens": 300, "system": system,
                      "messages": [{"role": "user", "content": json.dumps(context, default=str)}]},
            )
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text").strip()
            return text or None
    except Exception:
        return None
