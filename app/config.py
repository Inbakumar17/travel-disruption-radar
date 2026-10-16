"""Runtime configuration, read from environment variables (or a local .env file)."""
import os

try:  # optional: load a .env file if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

# DEMO_MODE=1 serves recorded/synthetic responses instead of calling live APIs.
# Useful offline, in CI, and as a fallback during a live demo.
DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1"

# api.weather.gov asks every client to send a User-Agent with contact info.
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "ops-team@example.com")
USER_AGENT = f"TravelDisruptionRadar/0.1 ({CONTACT_EMAIL})"

HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))

# Optional: if set, an LLM writes the plain-English briefing from the scored findings.
# The risk score itself is always computed by deterministic rules.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
