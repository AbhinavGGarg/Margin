"""Timestamped transcripts: fetch YouTube captions, or parse a transcript the user pasted."""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import List, Optional

from . import config


@dataclass
class Segment:
    start: float
    end: float
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Window:
    """A stretch of consecutive segments, used as one candidate 'moment'."""

    start: float
    end: float
    text: str
    seg_lo: int  # first segment index, inclusive
    seg_hi: int  # last segment index, inclusive


class TranscriptError(Exception):
    """Raised with a message that is safe to show to the user."""


# --- YouTube ---------------------------------------------------------------

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_video_id(url_or_id: str) -> str:
    """Accept any common YouTube URL shape, or a bare 11-character id."""
    value = (url_or_id or "").strip()
    if _ID_RE.match(value):
        return value
    if "://" not in value:
        value = "https://" + value
    parsed = urllib.parse.urlparse(value)
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    candidate = ""
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/")[0]
    elif host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        query = urllib.parse.parse_qs(parsed.query)
        if "v" in query:
            candidate = query["v"][0]
        else:
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live", "v"}:
                candidate = parts[1]
    if _ID_RE.match(candidate):
        return candidate
    raise TranscriptError("That doesn't look like a YouTube link. Paste the full address of the video.")


def fetch_video_meta(video_id: str) -> dict:
    """Title and channel via YouTube's public oEmbed endpoint. Never raises."""
    url = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(
        "https://www.youtube.com/watch?v=" + video_id, safe=""
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
        return {"title": data.get("title", ""), "author": data.get("author_name", "")}
    except Exception:
        return {}


def fetch_youtube(video_id: str, languages: tuple = ("en", "en-US", "en-GB")) -> List[Segment]:
    """Fetch timestamped captions for a video. Raises TranscriptError with a readable reason."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as exc:  # pragma: no cover - dependency is in requirements.txt
        raise TranscriptError("The caption library isn't installed. Run ./start.sh again.") from exc

    raw = None
    try:
        if hasattr(YouTubeTranscriptApi, "fetch"):  # version 1.x
            api = YouTubeTranscriptApi()
            try:
                fetched = api.fetch(video_id, languages=list(languages))
            except Exception:
                # No English track: take whatever exists, translated to English when possible.
                listing = api.list(video_id)
                track = next(iter(listing))
                if getattr(track, "is_translatable", False) and not track.language_code.startswith("en"):
                    try:
                        track = track.translate("en")
                    except Exception:
                        pass
                fetched = track.fetch()
            raw = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else list(fetched)
        else:  # version 0.x
            raw = YouTubeTranscriptApi.get_transcript(video_id, languages=list(languages))
    except TranscriptError:
        raise
    except Exception as exc:
        name = type(exc).__name__
        if name in {"TranscriptsDisabled", "NoTranscriptFound", "NoTranscriptAvailable"}:
            reason = "This video has no captions."
        elif name in {"VideoUnavailable", "InvalidVideoId", "VideoUnplayable"}:
            reason = "YouTube says this video is unavailable."
        elif name in {"RequestBlocked", "IpBlocked", "TooManyRequests"}:
            reason = "YouTube is refusing caption requests from this network right now."
        else:
            reason = "Couldn't get the captions from YouTube (" + name + ")."
        raise TranscriptError(reason + " You can paste the transcript instead.") from exc

    segments = []
    for item in raw or []:
        get = item.get if isinstance(item, dict) else (lambda k, d=None, _i=item: getattr(_i, k, d))
        start = float(get("start", 0.0) or 0.0)
        duration = float(get("duration", 0.0) or 0.0)
        segments.append(Segment(start=start, end=start + max(duration, 0.0), text=str(get("text", ""))))
    segments = clean_segments(segments)
    if not segments:
        raise TranscriptError("This video's captions are empty. You can paste the transcript instead.")
    return segments


# --- Pasted transcripts ------------------------------------------------------

_TIME = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?"
_CUE_RE = re.compile(r"^\s*" + _TIME + r"\s*-->\s*" + _TIME)
_LEADING_TIME_RE = re.compile(r"^\s*\[?\(?" + _TIME + r"\)?\]?\s*[-–—:]?\s*(.*)$")


def _seconds(hours, minutes, seconds, millis=None) -> float:
    total = int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
    if millis:
        total += int(millis.ljust(3, "0")) / 1000.0
    return float(total)


def parse_pasted(text: str) -> List[Segment]:
    """Parse SRT, WebVTT, or text copied from YouTube's 'Show transcript' panel.

    The YouTube panel copies as a timestamp line followed by a text line; some sites put both on
    one line ("0:15 so today we're going to..."). All of these are accepted.
    """
    lines = [ln.rstrip() for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    segments: List[Segment] = []
    current: Optional[Segment] = None

    def flush():
        nonlocal current
        if current is not None and current.text.strip():
            segments.append(current)
        current = None

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.upper().startswith("WEBVTT") or stripped.isdigit():
            if not stripped:
                flush()
            continue
        cue = _CUE_RE.match(stripped)
        if cue:
            flush()
            g = cue.groups()
            current = Segment(_seconds(*g[0:4]), _seconds(*g[4:8]), "")
            continue
        timed = _LEADING_TIME_RE.match(stripped)
        if timed:
            g = timed.groups()
            rest = g[4].strip()
            flush()
            current = Segment(_seconds(*g[0:4]), -1.0, rest)
            continue
        if current is None:
            continue  # text before the first timestamp carries no time; drop it
        current.text = (current.text + " " + stripped).strip()
    flush()

    segments.sort(key=lambda s: s.start)
    for index, segment in enumerate(segments):
        if segment.end < 0:
            nxt = segments[index + 1].start if index + 1 < len(segments) else segment.start + 5.0
            segment.end = max(nxt, segment.start + 0.5)
    segments = clean_segments(segments)
    if len(segments) < 3:
        raise TranscriptError(
            "Couldn't find timestamps in that text. Paste the transcript with its times, "
            "for example from YouTube's \"Show transcript\" panel."
        )
    return segments


# --- Cleaning and windowing ----------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_NOISE_RE = re.compile(r"\[(?:music|applause|laughter|laughs|silence|inaudible|foreign)[^\]]*\]", re.I)
_SPACE_RE = re.compile(r"\s+")


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = _TAG_RE.sub(" ", text)
    text = _NOISE_RE.sub(" ", text)
    text = text.replace(">>", " ").replace("​", "")
    return _SPACE_RE.sub(" ", text).strip()


def clean_segments(segments: List[Segment]) -> List[Segment]:
    cleaned: List[Segment] = []
    for segment in sorted(segments, key=lambda s: s.start):
        text = clean_text(segment.text)
        if not text:
            continue
        if cleaned and text == cleaned[-1].text and segment.start - cleaned[-1].start < 1.0:
            continue  # rolling captions sometimes repeat a line
        cleaned.append(Segment(segment.start, max(segment.end, segment.start), text))
    # Auto-captions overlap; clip each segment at the start of the next for display.
    for index in range(len(cleaned) - 1):
        nxt = cleaned[index + 1].start
        if cleaned[index].end > nxt > cleaned[index].start:
            cleaned[index].end = nxt
    return cleaned


def build_windows(
    segments: List[Segment],
    span: float = config.WINDOW_SECONDS,
    max_windows: int = config.MAX_WINDOWS,
) -> List[Window]:
    """Overlapping windows of about `span` seconds, one starting at each segment.

    Very long videos are thinned so that at most `max_windows` windows are produced.
    """
    count = len(segments)
    if count == 0:
        return []
    stride = max(1, -(-count // max_windows))
    windows: List[Window] = []
    hi = 0
    for lo in range(0, count, stride):
        hi = max(hi, lo)
        while hi + 1 < count and segments[hi].end - segments[lo].start < span:
            hi += 1
        text = " ".join(segments[k].text for k in range(lo, hi + 1))
        windows.append(Window(segments[lo].start, segments[hi].end, text, lo, hi))
    return windows


def duration_of(segments: List[Segment]) -> float:
    return max((s.end for s in segments), default=0.0)
