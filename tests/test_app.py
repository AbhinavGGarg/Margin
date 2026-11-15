import base64

import pytest
from fastapi.testclient import TestClient

from margin import pipeline, readers, server, transcript
from margin.readers import gemini
from margin.readers.base import NoteLine, ReaderError, clamp_box, tidy
from tests.fixtures import lecture
from tests.helpers import FixtureReader, notes_page

VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
def page():
    return notes_page([text for text, _ in lecture.NOTES])


@pytest.fixture
def client(monkeypatch, page):
    _, lines = page
    reader = FixtureReader(lines)
    monkeypatch.setattr(pipeline, "pick_reader", lambda: reader)
    monkeypatch.setattr(pipeline.scoring, "meaning_similarity", lambda *a, **k: None)
    monkeypatch.setattr(server, "_ensure_model_started", lambda: None)
    with TestClient(server.app) as test_client:
        test_client.reader = reader
        yield test_client


def get_captions(client, **fields):
    data = {"video_url": VIDEO, "transcript": lecture.pasted_transcript()}
    data.update(fields)
    return client.post("/api/captions", data=data)


def read_notes(client, image, **fields):
    return client.post("/api/read", data=fields, files={"notes": ("notes.jpg", image, "image/jpeg")})


def link(client, captions, lines):
    return client.post("/api/link", json={"captions": captions, "lines": lines})


def test_the_three_steps_make_a_session(client, page):
    image, _ = page
    video = get_captions(client)
    assert video.status_code == 200
    assert video.json()["video"] == {"id": "dQw4w9WgXcQ", "title": "Photosynthesis", "author": "Test Channel", "duration": 241.0}
    captions = video.json()["captions"]
    assert len(captions) == len(lecture.CAPTIONS) and captions[1] == {"t": 4.0, "e": 8.0, "text": lecture.CAPTIONS[1]}

    read = read_notes(client, image)
    assert read.status_code == 200
    assert read.json()["reader"] == "Fixture" and read.json()["page"] == {"width": 1000, "height": 1400}
    lines = read.json()["lines"]
    assert len(lines) == len(lecture.NOTES) and "image" not in read.json()
    assert all(l["box"] and 0 <= l["box"][0] < l["box"][2] <= 1 for l in lines)

    linked = link(client, captions, lines).json()
    assert linked["matching"] == "keywords"
    assert sum(1 for l in linked["lines"] if l["t"] is not None) == len(lecture.NOTES) - 1
    assert linked["lines"][4]["box"] == lines[4]["box"] and "thylakoid" in linked["lines"][4]["caption"]
    assert len(linked["map"]["cells"]) == len(lecture.NOTES)


def test_corrections_are_applied_by_linking_again(client, page):
    image, _ = page
    captions = get_captions(client).json()["captions"]
    lines = read_notes(client, image).json()["lines"]
    first = link(client, captions, lines).json()["lines"]
    assert first[13]["t"] is None                              # "TEST FRIDAY!! ch. 8"

    def as_input(linked):
        return [{"text": l["text"], "box": l["box"], "pinned": l["pinned"], "t": l["t"] if l["pinned"] else None} for l in linked]

    pinned_in = as_input(first)
    pinned_in[13].update({"pinned": True, "t": 201.4})
    pinned = link(client, captions, pinned_in).json()["lines"]
    assert pinned[13]["t"] == 201.4 and pinned[13]["confidence"] == "pinned" and pinned[13]["pinned"] is True
    assert "cam plants" in pinned[13]["caption"]

    again = as_input(pinned)                                   # a pin survives later re-links
    again[0]["text"] = "Photosynthesis: plants make food w/ sunlight"
    kept = link(client, captions, again).json()["lines"]
    assert kept[13]["t"] == 201.4 and kept[0]["text"].startswith("Photosynthesis: plants")

    unpinned_in = as_input(kept)
    unpinned_in[13].update({"pinned": False, "t": None})
    assert link(client, captions, unpinned_in).json()["lines"][13]["t"] is None

    edited_in = as_input(first)
    edited_in[13]["text"] = "cellular respiration = reverse process"
    edited = link(client, captions, edited_in).json()["lines"]
    assert 220 <= edited[13]["t"] <= 236


def test_notes_are_read_once_per_photo(client, page):
    image, _ = page
    read_notes(client, image)
    read_notes(client, image)
    assert client.reader.calls == 1


def test_the_upright_photo_can_be_sent_back(client, page):
    image, _ = page
    reply = read_notes(client, image, return_image="1").json()
    assert reply["image"].startswith("data:image/jpeg;base64,")
    assert base64.b64decode(reply["image"].split(",", 1)[1])[:2] == b"\xff\xd8"


def test_errors_are_reported_in_plain_words(client, page, monkeypatch):
    image, _ = page
    bad_link = get_captions(client, video_url="https://example.com/video")
    assert bad_link.status_code == 400 and bad_link.json()["code"] == "video"

    no_times = get_captions(client, transcript="words but no timestamps")
    assert no_times.status_code == 400 and no_times.json()["code"] == "captions"

    def no_captions(video_id):
        raise transcript.TranscriptError("This video has no captions. You can paste the transcript instead.")
    monkeypatch.setattr(pipeline, "load_transcript", no_captions)
    missing = get_captions(client, transcript="")
    assert missing.status_code == 400 and missing.json()["code"] == "captions" and "paste" in missing.json()["error"]

    not_a_photo = read_notes(client, b"this is not an image")
    assert not_a_photo.status_code == 400 and not_a_photo.json()["code"] == "image"
    assert read_notes(client, b"").status_code == 400

    def no_reader():
        raise ReaderError("No handwriting reader is set up yet.")
    monkeypatch.setattr(pipeline, "pick_reader", no_reader)
    unread = read_notes(client, image + b" ")
    assert unread.status_code == 400 and unread.json() == {"error": "No handwriting reader is set up yet.", "code": "reader"}

    assert link(client, [], []).status_code == 400
    assert link(client, [{"t": 0, "e": 4, "text": "hello there"}], [{"text": "   "}]).status_code == 400
    assert client.post("/api/link", json={"captions": "x", "lines": 3}).status_code == 400


def test_status_and_index(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    status = client.get("/api/status").json()
    assert status["reader"]["gemini"]["key"] is False and "matching" in status
    page_html = client.get("/")
    assert page_html.status_code == 200 and "Margin" in page_html.text
    assert client.get("/static/app.js").status_code == 200 and client.get("/static/app.css").status_code == 200


def test_the_hosting_config_points_at_the_app():
    import json
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    pyproject = (root / "pyproject.toml").read_text()
    module, _, variable = re.search(r'entrypoint = "([^"]+)"', pyproject).group(1).partition(":")
    assert getattr(__import__(module, fromlist=[variable]), variable) is server.app
    # The function settings must be keyed by that same file.
    assert list(json.loads((root / "vercel.json").read_text())["functions"]) == [module.replace(".", "/") + ".py"]
    # pyproject.toml and requirements.txt must list the same packages.
    listed = set(re.findall(r'^\s+"([^"]+)",$', pyproject, re.M))
    required = {line.strip() for line in (root / "requirements.txt").read_text().splitlines() if line.strip() and not line.startswith("#")}
    assert listed == required


# --- readers ---------------------------------------------------------------------------

def test_gemini_reply_parsing():
    reply = '```json\n{"lines": [{"text": "second line", "box_2d": [300, 100, 340, 620]}, {"text": " first  line ", "box_2d": [100, 100, 140, 700]}, {"text": "", "box_2d": [0,0,1,1]}, {"text": "no box", "box_2d": [1, 2]}]}\n```'
    lines = gemini.parse_response(reply)
    # One line has no usable box, so the reader's own order is kept.
    assert [l.text for l in lines] == ["second line", "first line", "no box"]
    ordered = gemini.parse_response(reply.replace(', {"text": "no box", "box_2d": [1, 2]}', ""))
    assert [l.text for l in ordered] == ["first line", "second line"]
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
