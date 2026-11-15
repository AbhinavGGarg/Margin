"""Read handwritten notes with Google's Gemini vision models."""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import List

from .. import config
from .base import NoteLine, Reader, ReaderError, clamp_box, tidy

log = logging.getLogger("margin.readers.gemini")

PROMPT = """You are reading a photo of a student's handwritten study notes.

Return JSON with this exact shape:
{"lines": [{"text": "...", "box_2d": [ymin, xmin, ymax, xmax]}]}

Rules:
- Each item is one note: a bullet, a sentence, a heading, or an equation with its label.
- If one bullet wraps onto several physical lines, return it as ONE item whose box covers all of its lines.
- Go in reading order: top to bottom, and the left column before the right if there are columns.
- Transcribe exactly what is written. Keep the student's abbreviations, arrows and symbols.
  Do not correct, expand, summarise or add anything.
- Skip doodles, page numbers, and crossed-out text.
- box_2d is the tight bounding box of the handwriting, with each value scaled to 0-1000
  relative to the image height (y) and width (x).
- If the photo contains no handwriting, return {"lines": []}.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "box_2d": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["text", "box_2d"],
            },
        }
    },
    "required": ["lines"],
}


def parse_response(text: str) -> List[NoteLine]:
    """Turn the model's reply into note lines. Tolerates code fences and stray prose."""
    body = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", body, re.S)
    if fenced:
        body = fenced.group(1).strip()
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        start, end = body.find("{"), body.rfind("}")
        if start < 0 or end <= start:
            raise ReaderError("The handwriting reader returned something unreadable. Try again.")
        try:
            data = json.loads(body[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ReaderError("The handwriting reader returned something unreadable. Try again.") from exc
    items = data.get("lines", []) if isinstance(data, dict) else data
    lines: List[NoteLine] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        text_value = str(item.get("text", "")).strip()
        if not text_value:
            continue
        box = None
        raw = item.get("box_2d") or item.get("box")
        if isinstance(raw, (list, tuple)) and len(raw) == 4:
            try:
                ymin, xmin, ymax, xmax = [float(v) / 1000.0 for v in raw]
                box = clamp_box(xmin, ymin, xmax, ymax)
            except (TypeError, ValueError):
                box = None
        lines.append(NoteLine(text_value, box))
    return tidy(lines)


class GeminiReader(Reader):
    name = "gemini"
    label = "Gemini"

    def __init__(self, api_key: str = "", model: str = ""):
        self.api_key = api_key or config.gemini_key()
        self.model = model or config.gemini_model()

    def _client(self):
        if not self.api_key:
            raise ReaderError("No Gemini key found. Add GEMINI_API_KEY to the .env file.")
        try:
            from google import genai
        except ImportError as exc:
            raise ReaderError("The Gemini library isn't installed. Run ./start.sh again.") from exc
        return genai.Client(api_key=self.api_key)

    def _ask(self, client, model: str, image_bytes: bytes) -> str:
        """Try the current Interactions API first, then the older generate_content call."""
        errors = []
        encoded = base64.b64encode(image_bytes).decode("ascii")
        if hasattr(client, "interactions"):
            content = [
                {"type": "text", "text": PROMPT},
                {"type": "image", "data": encoded, "mime_type": "image/jpeg"},
            ]
            for extra in (
                {"response_format": {"type": "text", "mime_type": "application/json", "schema": SCHEMA}},
                {},
            ):
                try:
                    interaction = client.interactions.create(model=model, input=content, **extra)
                    text = getattr(interaction, "output_text", None)
                    if text:
                        return text
                    errors.append("empty reply")
                except Exception as exc:
                    errors.append(type(exc).__name__ + ": " + str(exc)[:300])
                    if _is_fatal(exc):
                        raise
        try:
            from google.genai import types

            reply = client.models.generate_content(
                model=model,
                contents=[types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"), PROMPT],
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
            )
            if getattr(reply, "text", None):
                return reply.text
            errors.append("empty reply")
        except Exception as exc:
            errors.append(type(exc).__name__ + ": " + str(exc)[:300])
            if _is_fatal(exc):
                raise
        raise RuntimeError(" | ".join(errors) or "no reply")

    def _fallback_models(self, client) -> List[str]:
        """Other vision-capable flash models this key can use, newest first."""
        names = []
        try:
            for model in client.models.list():
                name = str(getattr(model, "name", "")).split("/")[-1]
                lowered = name.lower()
                if "flash" not in lowered or name == self.model:
                    continue
                if any(skip in lowered for skip in ("image", "live", "tts", "audio", "embedding", "lite")):
                    continue
                names.append(name)
        except Exception:
            return []

        def version(name: str):
            found = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
            return (float(found.group(1)) if found else 0.0, "preview" not in name)

        return sorted(set(names), key=version, reverse=True)[:3]

    def read(self, image_path: Path) -> List[NoteLine]:
        client = self._client()
        image_bytes = Path(image_path).read_bytes()
        attempts = [self.model]
        last_error = "unknown error"
        tried_fallbacks = False
        while attempts:
            model = attempts.pop(0)
            try:
                return parse_response(self._ask(client, model, image_bytes))
            except ReaderError:
                raise
            except Exception as exc:
                last_error = str(exc)
                log.warning("Gemini model %s failed: %s", model, last_error)
                lowered = last_error.lower()
                if "api key" in lowered or "api_key" in lowered or "permission" in lowered or "401" in lowered:
                    raise ReaderError("Gemini rejected the API key. Check GEMINI_API_KEY in the .env file.") from exc
                if "429" in lowered or "quota" in lowered or "rate limit" in lowered or "exhausted" in lowered:
                    raise ReaderError("Gemini's free limit was hit. Wait a minute and try again.") from exc
                if not tried_fallbacks:
                    tried_fallbacks = True
                    attempts.extend(self._fallback_models(client))
        raise ReaderError("Gemini couldn't read the photo (" + last_error[:160] + ").")


def _is_fatal(exc: Exception) -> bool:
    """Errors where trying a different call shape won't help."""
    lowered = str(exc).lower()
    return any(word in lowered for word in ("api key", "api_key", "permission", "quota", "exhausted", "429", "401", "403"))
