"""The web server: a small JSON API plus the single-page interface."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from typing import Dict, Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, config, pipeline, readers, scoring, store

log = logging.getLogger("margin.server")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


# Sessions being built right now: id -> {"status": "working" | "error", "step", "error", "code"}
_jobs: Dict[str, dict] = {}
_jobs_lock = threading.Lock()
_model_state = {"status": "idle"}  # idle | loading | ready | unavailable


def _warm_up_model() -> None:
    _model_state["status"] = "loading"
    embedder = scoring.get_embedder()
    _model_state["status"] = "ready" if embedder.ready() else "unavailable"
    if _model_state["status"] == "unavailable":
        log.info("Matching will use keywords only: %s", embedder.unavailable_reason)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    threading.Thread(target=_warm_up_model, name="margin-model", daemon=True).start()
    yield


app = FastAPI(title="Margin", version=__version__, docs_url=None, redoc_url=None, lifespan=_lifespan)


# --- API ---------------------------------------------------------------------------


@app.get("/api/status")
def status() -> dict:
    embedder = scoring.get_embedder()
    return {
        "version": __version__,
        "reader": readers.describe(),
        "matching": {"model": _model_state["status"], "reason": embedder.unavailable_reason},
    }


def _run(session_id: str, video_url: str, image_bytes: bytes, pasted: str) -> None:
    def progress(step: str) -> None:
        with _jobs_lock:
            _jobs[session_id]["step"] = step

    try:
        payload = pipeline.build_session(
            store.session_path(session_id), video_url, image_bytes, pasted, progress
        )
        store.save(session_id, payload)
        with _jobs_lock:
            _jobs.pop(session_id, None)
    except pipeline.PipelineError as exc:
        with _jobs_lock:
            _jobs[session_id] = {"status": "error", "error": str(exc), "code": exc.code}
        store.delete(session_id)
    except Exception:
        log.exception("Session %s failed", session_id)
        with _jobs_lock:
            _jobs[session_id] = {
                "status": "error",
                "error": "Something went wrong while linking your notes. Check the terminal for details.",
                "code": "error",
            }
        store.delete(session_id)


@app.post("/api/sessions")
async def create_session(
    video_url: str = Form(...),
    notes: UploadFile = File(...),
    transcript: str = Form(""),
) -> dict:
    try:
        pipeline.transcript.parse_video_id(video_url)
    except pipeline.TranscriptError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc), "code": "video"})
    image_bytes = await notes.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail={"error": "Add a photo of your notes.", "code": "image"})
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail={"error": "That photo is over 25 MB.", "code": "image"})

    session_id = store.new_id()
    store.session_path(session_id).mkdir(parents=True, exist_ok=True)
    with _jobs_lock:
        _jobs[session_id] = {"status": "working", "step": "starting"}
    threading.Thread(
        target=_run, args=(session_id, video_url, image_bytes, transcript), name="margin-" + session_id, daemon=True
    ).start()
    return {"id": session_id}


@app.get("/api/sessions")
def list_sessions() -> dict:
    return {"sessions": store.recent()}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    with _jobs_lock:
        job = dict(_jobs.get(session_id) or {})
    if job:
        return dict(job, id=session_id)
    session = store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail={"error": "That session doesn't exist any more.", "code": "missing"})
    return dict(session, status="ready")


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> dict:
    with _jobs_lock:
        _jobs.pop(session_id, None)
    return {"deleted": store.delete(session_id)}


@app.get("/api/sessions/{session_id}/page.jpg")
def get_page(session_id: str):
    try:
        path = store.session_path(session_id) / "page.jpg"
    except KeyError:
        raise HTTPException(status_code=404)
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.patch("/api/sessions/{session_id}/lines/{index}")
def update_line(session_id: str, index: int, change: dict = Body(...)) -> dict:
    """Correct one line: fix its text (re-links everything) and/or pin it to a moment.

    Body: {"text": "..."} and/or {"t": seconds} to pin, {"t": null} to unpin.
    """
    session = store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail={"error": "That session doesn't exist any more.", "code": "missing"})
    lines = session.get("lines", [])
    if not 0 <= index < len(lines):
        raise HTTPException(status_code=404, detail={"error": "No such line.", "code": "missing"})
    line = lines[index]

    if "text" in change:
        text = " ".join(str(change["text"] or "").split())
        if not text:
            raise HTTPException(status_code=400, detail={"error": "A line can't be empty.", "code": "text"})
        line["text"] = text[:500]
    if "t" in change:
        if change["t"] is None:
            line["pinned"] = False
        else:
            try:
                moment = float(change["t"])
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail={"error": "Bad time.", "code": "time"})
            line["t"] = round(max(0.0, min(moment, float(session["video"].get("duration") or moment))), 2)
            line["pinned"] = True

    session = pipeline.relink(session)
    store.save(session_id, session)
    return dict(session, status="ready")


@app.exception_handler(HTTPException)
async def _http_error(_request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail), "code": "error"}
    return JSONResponse(status_code=exc.status_code, content=detail)


# --- Interface ------------------------------------------------------------------------


@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=str(config.WEB_DIR)), name="static")
