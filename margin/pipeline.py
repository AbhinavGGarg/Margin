"""Build a session: page photo + video transcript -> every line linked to a moment."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
from pathlib import Path
from typing import List, Optional

from . import align, config, scoring, transcript
from .readers import NoteLine, Reader, pick_reader
from .readers.base import ReaderError
from .transcript import Segment, TranscriptError

log = logging.getLogger("margin.pipeline")

MAX_IMAGE_SIDE = 2200


class PipelineError(Exception):
    """Raised with a message that is safe to show to the user. `code` lets the UI react."""

    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


# --- Photo ---------------------------------------------------------------------------


def prepare_image(data: bytes, destination: Path) -> dict:
    """Normalise an uploaded photo to an upright JPEG. Returns its size and a content hash."""
    try:
        from PIL import Image, ImageOps

        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except Exception:
            pass
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)
        if image.mode != "RGB":
            image = image.convert("RGB")
    except Exception as exc:
        raise PipelineError(
            "Couldn't open that photo. Use a JPEG or PNG (iPhone HEIC works once pillow-heif is installed).",
            "image",
        ) from exc
    if max(image.size) > MAX_IMAGE_SIDE:
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, "JPEG", quality=88)
    return {
        "width": image.size[0],
        "height": image.size[1],
        "hash": hashlib.sha1(destination.read_bytes()).hexdigest()[:20],
    }


# --- Cached steps ----------------------------------------------------------------------


def _cache_dir(kind: str) -> Path:
    path = config.DATA_DIR / "cache" / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_transcript(video_id: str) -> List[Segment]:
    cache = _cache_dir("transcripts") / (video_id + ".json")
    if cache.exists():
        try:
            return [Segment(**item) for item in json.loads(cache.read_text(encoding="utf-8"))]
        except Exception:
            cache.unlink(missing_ok=True)
    segments = transcript.fetch_youtube(video_id)
    cache.write_text(json.dumps([s.to_dict() for s in segments]), encoding="utf-8")
    return segments


def read_notes(image_path: Path, image_hash: str, reader: Reader) -> List[NoteLine]:
    cache = _cache_dir("notes") / (image_hash + "_" + reader.name + ".json")
    if cache.exists():
        try:
            return [
                NoteLine(item["text"], tuple(item["box"]) if item.get("box") else None)
                for item in json.loads(cache.read_text(encoding="utf-8"))
            ]
        except Exception:
            cache.unlink(missing_ok=True)
    lines = reader.read(image_path)
    if lines:
        cache.write_text(json.dumps([line.to_dict() for line in lines]), encoding="utf-8")
    return lines


# --- Matching ---------------------------------------------------------------------------


def link_lines(lines: List[dict], segments: List[Segment], use_meaning: bool = True) -> dict:
    """Link each line (dicts with "text", "box", optional "pinned"/"t") to a moment.

    Returns {"lines": [...], "map": {...}, "matching": "..."}; safe to call again after edits.
    """
    windows = transcript.build_windows(segments)
    texts = [line["text"] for line in lines]
    scores = scoring.score(
        texts, [w.text for w in windows], cache_dir=_cache_dir("embeddings"), use_meaning=use_meaning
    )
    starts = [w.start for w in windows]
    matches = align.match_lines(scores.evidence, starts)

    def moment(line_text: str, window_index: int, score_value: float) -> dict:
        window = windows[window_index]
        first = align.refine_start(line_text, window, segments)
        return {
            "t": round(max(segments[first].start - config.LEAD_IN_SECONDS, 0.0), 2),
            "seg": first,
            "caption": align.excerpt(segments, first, window.seg_hi + 2),
            "score": round(score_value, 2),
        }

    out = []
    for index, (line, match) in enumerate(zip(lines, matches)):
        entry = {
            "i": index,
            "text": line["text"],
            "box": line.get("box"),
            "t": None,
            "seg": None,
            "caption": "",
            "score": 0.0,
            "confidence": "none",
            "alternatives": [moment(line["text"], alt.window, alt.score) for alt in match.alternatives],
            "pinned": False,
        }
        if match.window is not None:
            entry.update(moment(line["text"], match.window, match.score))
            entry["confidence"] = match.confidence
            # Two windows can refine to the same caption; drop alternatives that land on the pick.
            entry["alternatives"] = [a for a in entry["alternatives"] if abs(a["t"] - entry["t"]) > 8.0]
        entry["alternatives"].sort(key=lambda alternative: alternative["t"])
        if line.get("pinned") and line.get("t") is not None:
            entry.update({"t": line["t"], "pinned": True, "confidence": "pinned"})
            entry["seg"] = nearest_segment(segments, line["t"])
            entry["caption"] = align.excerpt(segments, entry["seg"], entry["seg"] + 6)
        out.append(entry)

    chosen = [m.window for m in matches]
    return {
        "lines": out,
        "map": align.match_map(scores.evidence, starts, chosen),
        "matching": scores.mode,
    }


def nearest_segment(segments: List[Segment], t: float) -> int:
    best = 0
    for index, segment in enumerate(segments):
        if segment.start <= t + 0.05:
            best = index
        else:
            break
    return best


# --- The steps the server exposes ---------------------------------------------------------


def get_captions(video_url: str, pasted_transcript: str = "") -> dict:
    """Video details and timestamped captions. Raises PipelineError."""
    try:
        video_id = transcript.parse_video_id(video_url)
    except TranscriptError as exc:
        raise PipelineError(str(exc), "video") from exc
    try:
        if pasted_transcript.strip():
            segments = transcript.parse_pasted(pasted_transcript)
        else:
            segments = load_transcript(video_id)
    except TranscriptError as exc:
        raise PipelineError(str(exc), "captions") from exc
    meta = transcript.fetch_video_meta(video_id)
    return {
        "video": {
            "id": video_id,
            "title": meta.get("title", ""),
            "author": meta.get("author", ""),
            "duration": round(transcript.duration_of(segments), 1),
        },
        "captions": [{"t": round(s.start, 2), "e": round(s.end, 2), "text": s.text} for s in segments],
    }


def read_page(
    image_bytes: bytes, scratch_path: Path, reader: Optional[Reader] = None, return_image: bool = False
) -> dict:
    """Read the handwriting in a photo. `scratch_path` is where the normalised JPEG is written."""
    page = prepare_image(image_bytes, scratch_path)
    try:
        reader = reader or pick_reader()
        notes = read_notes(scratch_path, page["hash"], reader)
    except ReaderError as exc:
        raise PipelineError(str(exc), "reader") from exc
    if not notes:
        raise PipelineError(
            "Couldn't find any handwriting in that photo. Try a sharper, well-lit photo taken from straight above.",
            "reader",
        )
    result = {
        "reader": reader.label,
        "page": {"width": page["width"], "height": page["height"]},
        "lines": [line.to_dict() for line in notes],
    }
    if return_image:
        result["image"] = "data:image/jpeg;base64," + base64.b64encode(scratch_path.read_bytes()).decode("ascii")
    return result
