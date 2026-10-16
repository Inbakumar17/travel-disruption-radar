import os
import sys
from pathlib import Path

os.environ["DEMO_MODE"] = "1"
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app import config, net  # noqa: E402

config.DEMO_MODE = True
config.ANTHROPIC_API_KEY = ""


@pytest.fixture(autouse=True)
def _fresh_cache():
    net.clear_cache()
    yield
    net.clear_cache()
