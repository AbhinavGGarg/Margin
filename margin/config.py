"""Settings, read from the environment and an optional .env file next to the project."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("MARGIN_DATA_DIR", ROOT / "data"))
WEB_DIR = Path(__file__).resolve().parent / "web"


def load_env_file(path: Path = ROOT / ".env") -> None:
    """Load KEY=VALUE lines from .env without overriding variables that are already set."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def gemini_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()


def gemini_model() -> str:
    return os.environ.get("GEMINI_MODEL", "").strip() or "gemini-3.8-flash"


def reader_choice() -> str:
    return (os.environ.get("MARGIN_READER", "auto").strip().lower()) or "auto"


def port() -> int:
    try:
        return int(os.environ.get("MARGIN_PORT", "8000"))
    except ValueError:
        return 8000


# --- Matching -------------------------------------------------------------

EMBED_MODEL = "BAAI/bge-small-en-v1.5"

# A transcript window is about this many seconds of speech; windows start at every caption.
WINDOW_SECONDS = 18.0
MAX_WINDOWS = 1600

# Evidence that a line is about a moment runs from 0 (nothing in common) to 1 (clearly the same).
# Keywords: share of the line's informative words found in the moment.
# Meaning: embedding similarity above the line's own typical level, scaled so that
# MEANING_FULL_MARGIN above typical counts as full evidence.
MEANING_FULL_MARGIN = 0.30
MEANING_WEIGHT = 0.9

MATCH_THRESHOLD = 0.42       # a line is only linked if its moment has at least this much evidence
BACKWARD_BASE = 0.25         # cost of linking a line to a moment earlier than the line above it
BACKWARD_PER_MINUTE = 0.05   # extra cost per minute jumped backwards
BACKWARD_MAX = 0.60

CONFIDENCE_HIGH = 0.75
CONFIDENCE_MEDIUM = 0.58

ALTERNATIVE_FLOOR = 0.45
ALTERNATIVE_MIN_GAP_SECONDS = 25.0
LEAD_IN_SECONDS = 1.0
