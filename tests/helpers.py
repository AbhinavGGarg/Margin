"""Draw a fake page of notes and return it with the box of every line."""

import io

from PIL import Image, ImageDraw, ImageFont

from margin.readers.base import NoteLine, Reader


def _font(size):
    for name in ("DejaVuSans-Oblique.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def notes_page(texts, width=1000, height=1400):
    image = Image.new("RGB", (width, height), (252, 250, 244))
    draw = ImageDraw.Draw(image)
    for y in range(120, height - 40, 72):
        draw.line([(0, y + 52), (width, y + 52)], fill=(205, 220, 236), width=2)
    draw.line([(96, 0), (96, height)], fill=(226, 150, 150), width=2)
    font, lines = _font(30), []
    for index, text in enumerate(texts):
        x, y = 120 + (24 if text.startswith(("-", "*")) else 0), 132 + index * 72
        draw.text((x, y), text, fill=(28, 34, 78), font=font)
        left, top, right, bottom = draw.textbbox((x, y), text, font=font)
        lines.append(NoteLine(text, (left / width, top / height, right / width, bottom / height)))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    return buffer.getvalue(), lines


class FixtureReader(Reader):
    name = "fixture"
    label = "Fixture"

    def __init__(self, lines):
        self.lines = lines
        self.calls = 0

    def read(self, image_path):
        self.calls += 1
        return list(self.lines)
