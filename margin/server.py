"""The web server: three stateless endpoints plus the single-page interface.

Nothing is stored on the server. The browser keeps each session (the photo, the lines, the links)
and sends back what is needed when a line is corrected. That keeps notes private and lets the same
code run on a laptop or on a serverless host.
"""

from __future__ import annotations

import importlib.util
import logging
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, config, pipeline, readers, scoring

config.load_env_file()
log = logging.getLogger("margin.server")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_LINES = 200
MAX_CAPTIONS = 20000

_model_state = {"status": "idle"}  # idle | loading | ready | unavailable
_model_lock = threading.Lock()


def _warm_up_model() -> None:
    embedder = scoring.get_embedder()
    _model_state["status"] = "ready" if embedder.ready() else "unavailable"
    if _model_state["status"] == "unavailable":
        log.info("Matching will use keywords only: %s", embedder.unavailable_reason)


def _ensure_model_started() -> None:
    """Start loading the meaning model once. Safe to call from any request."""
    with _model_lock:
        if _model_state["status"] != "idle":
            return
        if importlib.util.find_spec("fastembed") is None:
            _model_state["status"] = "unavailable"
            return
        _model_state["status"] = "loading"
    threading.Thread(target=_warm_up_model, name="margin-model", daemon=True).start()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    _ensure_model_started()
    yield


app = FastAPI(title="Margin", version=__version__, docs_url=None, redoc_url=None, lifespan=_lifespan)


def _fail(status: int, message: str, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": message, "code": code})


# --- API ---------------------------------------------------------------------------


@app.get("/api/status")
def status() -> dict:
    _ensure_model_started()
    embedder = scoring.get_embedder()
    return {
        "version": __version__,
        "reader": readers.describe(),
        "matching": {
            "model": _model_state["status"],
            "reason": embedder.unavailable_reason or ("fastembed is not installed" if _model_state["status"] == "unavailable" else None),
        },
    }


@app.post("/api/captions")
def captions(video_url: str = Form(...), transcript: str = Form("")) -> dict:
    """The video's timestamped captions, from YouTube or from a transcript the user pasted."""
    try:
        return pipeline.get_captions(video_url, transcript)
    except pipeline.PipelineError as exc:
        raise _fail(400, str(exc), exc.code)


@app.post("/api/read")
async def read(notes: UploadFile = File(...), return_image: str = Form("")) -> dict:
    """Read the handwriting in a photo: each line's text and where it sits on the page.

    With `return_image`, the upright JPEG is sent back too, for photos the browser could not
    convert itself (HEIC outside Safari).
    """
    image_bytes = await notes.read()
    if not image_bytes:
        raise _fail(400, "Add a photo of your notes.", "image")
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        raise _fail(400, "That photo is over 25 MB.", "image")
    with tempfile.TemporaryDirectory() as folder:
        try:
            return pipeline.read_page(image_bytes, Path(folder) / "page.jpg", return_image=bool(return_image))
        except pipeline.PipelineError as exc:
            raise _fail(400, str(exc), exc.code)


@app.post("/api/link")
def link(body: dict = Body(...)) -> dict:
    """Link lines to moments. Called once when a session is made, and again after a correction.

    Body: {"captions": [{"t", "e", "text"}], "lines": [{"text", "box", "pinned"?, "t"?}]}
    """
    raw_captions, raw_lines = body.get("captions"), body.get("lines")
    if not isinstance(raw_captions, list) or not isinstance(raw_lines, list) or not raw_captions or not raw_lines:
        raise _fail(400, "Nothing to link.", "input")
    if len(raw_lines) > MAX_LINES or len(raw_captions) > MAX_CAPTIONS:
        raise _fail(400, "That's too much to link in one go.", "input")
    try:
        segments = [
            pipeline.Segment(float(c["t"]), float(c.get("e", c["t"])), str(c["text"])) for c in raw_captions
        ]
        lines = []
        for item in raw_lines:
            text = " ".join(str(item.get("text", "")).split())[:500]
            if not text:
                raise ValueError("empty line")
            box = item.get("box")
            pinned = bool(item.get("pinned")) and item.get("t") is not None
            lines.append(
                {
                    "text": text,
                    "box": [float(v) for v in box] if isinstance(box, list) and len(box) == 4 else None,
                    "pinned": pinned,
                    "t": float(item["t"]) if pinned else None,
                }
            )
    except (KeyError, TypeError, ValueError):
        raise _fail(400, "A line can't be empty.", "input")
    return pipeline.link_lines(lines, segments)


@app.exception_handler(HTTPException)
async def _http_error(_request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail), "code": "error"}
    return JSONResponse(status_code=exc.status_code, content=detail)


# --- Interface ------------------------------------------------------------------------


@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=str(config.WEB_DIR)), name="static")
