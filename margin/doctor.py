"""`python -m margin doctor`: check each part works on this machine, and say how to fix what doesn't."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from . import config, readers, scoring, transcript

TEST_VIDEO = "dQw4w9WgXcQ"  # a video that has had English captions for many years
TEST_TEXT = "Margin test 123"


def _ok(message: str) -> None:
    print("  ok    " + message)


def _bad(message: str, fix: str = "") -> None:
    print("  FAIL  " + message)
    if fix:
        print("        -> " + fix)


def _note(message: str) -> None:
    print("  note  " + message)


def _test_image(path: Path) -> tuple:
    """Draw TEST_TEXT in the upper-left of a white page. Returns the box it was drawn in (fractions)."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1200, 900), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=64)
    except TypeError:  # older Pillow
        font = ImageFont.load_default()
    draw.text((120, 140), TEST_TEXT, fill=(20, 20, 60), font=font)
    left, top, right, bottom = draw.textbbox((120, 140), TEST_TEXT, font=font)
    image.save(path, "JPEG", quality=92)
    return (left / 1200, top / 900, right / 1200, bottom / 900)


def run() -> int:
    failures = 0
    print("Margin doctor\n")

    print("Python")
    if sys.version_info >= (3, 9):
        _ok("Python %d.%d" % sys.version_info[:2])
    else:
        failures += 1
        _bad("Python %d.%d is too old" % sys.version_info[:2], "Install Python 3.10 or newer from python.org.")

    print("\nYouTube captions")
    try:
        segments = transcript.fetch_youtube(TEST_VIDEO)
        _ok("fetched %d captions from a test video" % len(segments))
    except transcript.TranscriptError as exc:
        failures += 1
        _bad(str(exc), "Check your internet connection. If it keeps failing, paste transcripts in the app instead.")

    print("\nHandwriting reader")
    info = readers.describe()
    if not info["gemini"]["key"]:
        _note("no GEMINI_API_KEY set (Gemini reads messy handwriting best)")
    elif not info["gemini"]["installed"]:
        _note("GEMINI_API_KEY is set but the google-genai library is missing; run ./start.sh")
    if not info["active"]:
        failures += 1
        _bad(
            "no reader is available",
            "Add GEMINI_API_KEY to .env, or on a Mac run ./start.sh so the Apple Vision reader installs.",
        )
    else:
        print("  using " + info["label"] + (" (" + info["gemini"]["model"] + ")" if info["active"] == "gemini" else ""))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.jpg"
            drawn = _test_image(path)
            try:
                lines = readers.pick_reader().read(path)
                text = " ".join(line.text for line in lines)
                if "margin" in text.lower() and "123" in text:
                    _ok('read the test page: "' + text + '"')
                else:
                    failures += 1
                    _bad('read the test page as "' + text + '", expected "' + TEST_TEXT + '"')
                box = next((line.box for line in lines if line.box), None)
                if box is None:
                    _note("the reader returned no position for the line; taps will use the text list")
                else:
                    centre_y = (box[1] + box[3]) / 2
                    expected_y = (drawn[1] + drawn[3]) / 2
                    if abs(centre_y - expected_y) < 0.08 and abs(box[0] - drawn[0]) < 0.1:
                        _ok("line position is right")
                    else:
                        failures += 1
                        _bad(
                            "line position is off (got %s, drew at %s)"
                            % ([round(v, 2) for v in box], [round(v, 2) for v in drawn]),
                            "Tell the developer: highlights will not sit on the handwriting.",
                        )
            except readers.ReaderError as exc:
                failures += 1
                _bad(str(exc))

    print("\nMatching")
    embedder = scoring.get_embedder()
    if embedder.ready():
        vectors = embedder.embed(["plants make sugar from sunlight", "photosynthesis", "the French Revolution"])
        related, unrelated = float(vectors[0] @ vectors[1]), float(vectors[0] @ vectors[2])
        if related > unrelated:
            _ok("meaning model loaded (related %.2f, unrelated %.2f)" % (related, unrelated))
        else:
            _note("meaning model loaded but gave odd scores (%.2f vs %.2f)" % (related, unrelated))
    else:
        _note("meaning model unavailable (" + str(embedder.unavailable_reason) + "); matching will use keywords only")
        _note("this still works well when notes reuse the video's words; run ./start.sh to install fastembed")

    print("\n" + ("Everything needed is working." if failures == 0 else "%d thing(s) need fixing." % failures))
    print("Data folder: " + str(config.DATA_DIR))
    return 1 if failures else 0
