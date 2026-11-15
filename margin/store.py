"""Sessions on disk: one folder per session holding session.json and the page photo."""

from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import List, Optional

from . import config

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,24}$")
_lock = threading.Lock()


def sessions_dir() -> Path:
    path = config.DATA_DIR / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_id() -> str:
    return secrets.token_urlsafe(6)


def valid_id(session_id: str) -> bool:
    return bool(_ID_RE.match(session_id or ""))


def session_path(session_id: str) -> Path:
    if not valid_id(session_id):
        raise KeyError(session_id)
    return sessions_dir() / session_id


def save(session_id: str, payload: dict) -> dict:
    folder = session_path(session_id)
    folder.mkdir(parents=True, exist_ok=True)
    payload = dict(payload, id=session_id)
    payload.setdefault("created", time.time())
    temporary = folder / "session.json.tmp"
    with _lock:
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(folder / "session.json")
    return payload


def load(session_id: str) -> Optional[dict]:
    try:
        path = session_path(session_id) / "session.json"
    except KeyError:
        return None
    if not path.exists():
        return None
    with _lock:
        return json.loads(path.read_text(encoding="utf-8"))


def delete(session_id: str) -> bool:
    try:
        folder = session_path(session_id)
    except KeyError:
        return False
    if not folder.exists():
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return True


def recent(limit: int = 12) -> List[dict]:
    """Finished sessions, newest first, as short summaries."""
    items = []
    for folder in sessions_dir().iterdir():
        path = folder / "session.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        lines = data.get("lines", [])
        items.append(
            {
                "id": data.get("id", folder.name),
                "created": data.get("created", 0),
                "title": data.get("video", {}).get("title", ""),
                "author": data.get("video", {}).get("author", ""),
                "video_id": data.get("video", {}).get("id", ""),
                "lines": len(lines),
                "linked": sum(1 for line in lines if line.get("t") is not None),
            }
        )
    items.sort(key=lambda item: item["created"], reverse=True)
    return items[:limit]
