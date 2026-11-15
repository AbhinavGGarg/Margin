"""Read handwritten notes with Apple's Vision framework (macOS only, no key, works offline)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

from .base import NoteLine, Reader, ReaderError, clamp_box, tidy


def available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import ocrmac  # noqa: F401

        return True
    except Exception:
        return False


class AppleVisionReader(Reader):
    name = "apple"
    label = "Apple Vision"

    def read(self, image_path: Path) -> List[NoteLine]:
        if sys.platform != "darwin":
            raise ReaderError("Apple Vision only works on a Mac.")
        try:
            from ocrmac import ocrmac
        except ImportError as exc:
            raise ReaderError("The Apple Vision library isn't installed. Run ./start.sh again.") from exc
        try:
            annotations = ocrmac.OCR(
                str(image_path), recognition_level="accurate", language_preference=["en-US"]
            ).recognize()
        except Exception as exc:
            raise ReaderError("Apple Vision couldn't read the photo (" + type(exc).__name__ + ").") from exc

        lines: List[NoteLine] = []
        for annotation in annotations:
            try:
                text, _confidence, bbox = annotation
                x, y, width, height = [float(v) for v in bbox]
            except (TypeError, ValueError):
                continue
            # Vision boxes are fractions of the image with the origin at the bottom-left.
            box = clamp_box(x, 1.0 - y - height, x + width, 1.0 - y)
            lines.append(NoteLine(str(text), box))
        return tidy(lines)
