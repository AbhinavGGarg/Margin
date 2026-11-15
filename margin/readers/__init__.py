"""Handwriting readers. `pick_reader` chooses one based on settings and what is installed."""

from __future__ import annotations

from typing import Optional

from .. import config
from . import apple
from .base import NoteLine, Reader, ReaderError
from .gemini import GeminiReader

__all__ = ["NoteLine", "Reader", "ReaderError", "pick_reader", "describe"]


def _gemini_installed() -> bool:
    try:
        from google import genai  # noqa: F401

        return True
    except Exception:
        return False


def describe() -> dict:
    """What is available, for the status endpoint and the doctor command."""
    gemini_ready = bool(config.gemini_key()) and _gemini_installed()
    apple_ready = apple.available()
    choice = config.reader_choice()
    active: Optional[str] = None
    if choice == "gemini":
        active = "gemini" if gemini_ready else None
    elif choice == "apple":
        active = "apple" if apple_ready else None
    else:
        active = "gemini" if gemini_ready else ("apple" if apple_ready else None)
    return {
        "choice": choice,
        "active": active,
        "label": {"gemini": "Gemini", "apple": "Apple Vision"}.get(active or "", ""),
        "gemini": {"key": bool(config.gemini_key()), "installed": _gemini_installed(), "model": config.gemini_model()},
        "apple": {"available": apple_ready},
    }


def pick_reader() -> Reader:
    info = describe()
    if info["active"] == "gemini":
        return GeminiReader()
    if info["active"] == "apple":
        return apple.AppleVisionReader()
    if info["choice"] == "gemini" or (info["gemini"]["key"] and not info["gemini"]["installed"]):
        if not info["gemini"]["key"]:
            raise ReaderError("No Gemini key found. Add GEMINI_API_KEY to the .env file and restart.")
        raise ReaderError("The Gemini library isn't installed. Run ./start.sh again.")
    raise ReaderError(
        "No handwriting reader is set up yet. Add a GEMINI_API_KEY to the .env file and restart "
        "(or, on a Mac, run ./start.sh to install the Apple Vision reader)."
    )
