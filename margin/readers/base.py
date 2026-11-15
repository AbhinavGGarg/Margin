"""Shared types for handwriting readers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

Box = Tuple[float, float, float, float]  # x0, y0, x1, y1 as fractions of the image, origin top-left


@dataclass
class NoteLine:
    text: str
    box: Optional[Box] = None

    def to_dict(self) -> dict:
        return {"text": self.text, "box": list(self.box) if self.box else None}


class ReaderError(Exception):
    """Raised with a message that is safe to show to the user."""


class Reader:
    name = "reader"
    label = "Reader"

    def read(self, image_path: Path) -> List[NoteLine]:  # pragma: no cover - interface
        raise NotImplementedError


def clamp_box(x0: float, y0: float, x1: float, y1: float) -> Optional[Box]:
    """Order the corners, clamp to the image, and reject boxes that are empty or cover the page."""
    x0, x1 = sorted((float(x0), float(x1)))
    y0, y1 = sorted((float(y0), float(y1)))
    x0, y0 = max(0.0, min(1.0, x0)), max(0.0, min(1.0, y0))
    x1, y1 = max(0.0, min(1.0, x1)), max(0.0, min(1.0, y1))
    width, height = x1 - x0, y1 - y0
    if width < 0.01 or height < 0.004:
        return None
    if width > 0.98 and height > 0.9:
        return None
    return (round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4))


def tidy(lines: List[NoteLine]) -> List[NoteLine]:
    """Drop empty lines and put lines in reading order when every line has a box."""
    kept = [NoteLine(" ".join(line.text.split()), line.box) for line in lines if line.text and line.text.strip()]
    if kept and all(line.box for line in kept):
        heights = sorted(line.box[3] - line.box[1] for line in kept)
        row = max(heights[len(heights) // 2] * 0.6, 0.005)
        kept.sort(key=lambda line: (round(((line.box[1] + line.box[3]) / 2) / row), line.box[0]))
    return kept
