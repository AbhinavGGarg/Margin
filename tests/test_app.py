import time

import pytest
from fastapi.testclient import TestClient

from margin import pipeline, readers, server, transcript
from margin.readers import gemini
from margin.readers.base import ReaderError, clamp_box, tidy, NoteLine
from tests.fixtures import lecture
from tests.helpers import FixtureReader, notes_page

VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
def page():
    return notes_page([text for text, _ in lecture.NOTES])


@pytest.fixture
def client(monkeypatch, page):
    image, lines = page
    reader = FixtureReader(lines)
    monkeypatch.setattr(pipeline, "pick_reader", lambda: reader)
    monkeypatch.setattr(pipeline.scoring, "meaning_similarity", lambda *a, **k: None)
    monkeypatch.setattr(server, "_warm_up_model", lambda: None)
    with TestClient(server.app) as test_client:
        test_client.reader = reader
        yield test_client


def wait_ready(client, session_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get("/api/sessions/" + session_id).json()
        if data["status"] != "working":
            return data
        time.sleep(0.05)
    raise AssertionError("session did not finish")


def create(client, image, **fields):
    data = {"video_url": VIDEO, "transcript": lecture.pasted_transcript()}
    data.update(fields)
    return client.post("/api/sessions", data=data, files={"notes": ("notes.jpg", image, "image/jpeg")})


def test_full_session_through_the_api(client, page):
    image, lines = page
    response = create(client, image)
    assert response.status_code == 200
    session = wait_ready(client, response.json()["id"])
    assert session["status"] == "ready"
    assert session["video"] == {"id": "dQw4w9WgXcQ", "title": "Photosynthesis", "author": "Test Channel", "duration": 241.0}
    assert len(session["lines"]) == len(lecture.NOTES)
    assert sum(1 for l in session["lines"] if l["t"] is not None) == len(lecture.NOTES) - 1
    assert all(l["box"] and 0 <= l["box"][0] < l["box"][2] <= 1 for l in session["lines"])
    assert len(session["captions"]) == len(lecture.CAPTIONS)
    assert session["page"]["width"] == 1000

    picture = client.get("/api/sessions/%s/page.jpg" % session["id"])
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/jpeg"
    listing = client.get("/api/sessions").json()["sessions"]
    assert listing[0]["id"] == session["id"] and listing[0]["linked"] == 14 and listing[0]["lines"] == 15

    assert client.delete("/api/sessions/" + session["id"]).json() == {"deleted": True}
    assert client.get("/api/sessions/" + session["id"]).status_code == 404


def test_pin_unpin_and_edit_a_line(client, page):
    image, _ = page
    session = wait_ready(client, create(client, image).json()["id"])
    url = "/api/sessions/%s/lines/13" % session["id"]          # "TEST FRIDAY!! ch. 8", unlinked
    assert session["lines"][13]["t"] is None

    pinned = client.patch(url, json={"t": 201.4}).json()
    assert pinned["lines"][13]["t"] == 201.4 and pinned["lines"][13]["confidence"] == "pinned"
    assert "cam plants" in pinned["lines"][13]["caption"]
    assert client.get("/api/sessions/" + session["id"]).json()["lines"][13]["pinned"] is True

    unpinned = client.patch(url, json={"t": None}).json()
    assert unpinned["lines"][13]["t"] is None and unpinned["lines"][13]["pinned"] is False

    edited = client.patch(url, json={"text": "cellular respiration = reverse process"}).json()
    assert edited["lines"][13]["text"] == "cellular respiration = reverse process"
    assert 220 <= edited["lines"][13]["t"] <= 236
    assert client.patch(url, json={"text": "   "}).status_code == 400
    assert client.patch("/api/sessions/%s/lines/99" % session["id"], json={"t": 1}).status_code == 404


def test_notes_are_read_once_per_photo(client, page):
    image, _ = page
    wait_ready(client, create(client, image).json()["id"])
    wait_ready(client, create(client, image).json()["id"])
    assert client.reader.calls == 1


def test_errors_are_reported_in_plain_words(client, page, monkeypatch):
    image, _ = page
    bad_link = create(client, image, video_url="https://example.com/video")
    assert bad_link.status_code == 400 and bad_link.json()["code"] == "video"

    not_a_photo = wait_ready(client, create(client, b"this is not an image").json()["id"])
    assert not_a_photo["status"] == "error" and not_a_photo["code"] == "image"

    def no_captions(video_id):
        raise transcript.TranscriptError("This video has no captions. You can paste the transcript instead.")
    monkeypatch.setattr(pipeline, "load_transcript", no_captions)
    missing = wait_ready(client, create(client, image, transcript="").json()["id"])
    assert missing["status"] == "error" and missing["code"] == "captions" and "paste" in missing["error"]

    def no_reader():
        raise ReaderError("No handwriting reader is set up yet.")
    monkeypatch.setattr(pipeline, "pick_reader", no_reader)
    unread = wait_ready(client, create(client, image + b" ").json()["id"])
    assert unread["status"] == "error" and unread["code"] == "reader"
    assert client.get("/api/sessions").json()["sessions"] == []


def test_status_and_index(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    status = client.get("/api/status").json()
    assert status["reader"]["gemini"]["key"] is False and "matching" in status
    page_html = client.get("/")
    assert page_html.status_code == 200 and "Margin" in page_html.text
    assert client.get("/static/app.js").status_code == 200 and client.get("/static/app.css").status_code == 200
    assert client.get("/api/sessions/../../etc").status_code in (404, 405)
    assert client.get("/api/sessions/nope-nope").status_code == 404


# --- readers ---------------------------------------------------------------------------

def test_gemini_reply_parsing():
    reply = '```json\n{"lines": [{"text": "second line", "box_2d": [300, 100, 340, 620]}, {"text": " first  line ", "box_2d": [100, 100, 140, 700]}, {"text": "", "box_2d": [0,0,1,1]}, {"text": "no box", "box_2d": [1, 2]}]}\n```'
    lines = gemini.parse_response(reply)
    # One line has no usable box, so the reader's own order is kept.
    assert [l.text for l in lines] == ["second line", "first line", "no box"]
    ordered = gemini.parse_response(reply.replace(', {"text": "no box", "box_2d": [1, 2]}', ""))
    assert [l.text for l in ordered] == ["first line", "second line"]
    boxed = [l for l in lines if l.box]
    assert boxed and all(0 <= v <= 1 for l in boxed for v in l.box)
    first = next(l for l in lines if l.text == "first line")
    assert first.box == (0.1, 0.1, 0.7, 0.14)
    assert gemini.parse_response('Here you go: {"lines": []} hope that helps') == []
    with pytest.raises(ReaderError):
        gemini.parse_response("I could not read this image.")


def test_boxes_are_clamped_and_lines_put_in_reading_order():
    assert clamp_box(0.5, 0.2, 0.1, 0.1) == (0.1, 0.1, 0.5, 0.2)
    assert clamp_box(-0.2, 0.1, 1.4, 0.2) == (0.0, 0.1, 1.0, 0.2)
    assert clamp_box(0.1, 0.1, 0.1, 0.5) is None and clamp_box(0, 0, 1, 1) is None
    lines = tidy([NoteLine("c", (0.1, 0.50, 0.9, 0.54)), NoteLine("b", (0.55, 0.101, 0.9, 0.14)), NoteLine("a", (0.1, 0.10, 0.5, 0.14)), NoteLine("  ", None)])
    assert [l.text for l in lines] == ["a", "b", "c"]


def test_pick_reader_explains_what_is_missing(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(readers.apple, "available", lambda: False)
    with pytest.raises(ReaderError) as error:
        readers.pick_reader()
    assert "GEMINI_API_KEY" in str(error.value)
