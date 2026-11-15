import numpy as np
import pytest

from margin import align, pipeline, scoring, transcript
from tests.fixtures import lecture


# --- transcript ---------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?t=42", "dQw4w9WgXcQ"),
    ("youtube.com/watch?list=PL123&v=dQw4w9WgXcQ&t=10s", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/live/dQw4w9WgXcQ?feature=share", "dQw4w9WgXcQ"),
    ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
])
def test_parse_video_id(url, expected):
    assert transcript.parse_video_id(url) == expected


@pytest.mark.parametrize("url", ["", "https://vimeo.com/12345", "hello", "https://youtube.com/watch?v=short"])
def test_parse_video_id_rejects(url):
    with pytest.raises(transcript.TranscriptError):
        transcript.parse_video_id(url)


def test_parse_pasted_youtube_panel():
    segments = transcript.parse_pasted(lecture.pasted_transcript())
    assert len(segments) == len(lecture.CAPTIONS)
    assert segments[0].start == 0 and segments[1].start == 4
    assert segments[16].text.startswith("the light reactions take place")
    assert segments[15].end == segments[16].start
    assert segments[20].start == 80.0  # "1:20"


def test_parse_pasted_srt_and_vtt_and_inline():
    srt = "1\n00:00:01,000 --> 00:00:04,500\nHello there\nsecond line\n\n2\n00:00:04,500 --> 00:00:08,000\n<i>General</i> Kenobi\n\n3\n00:01:02,000 --> 00:01:05,000\n[Music]\n\n4\n01:00:00,000 --> 01:00:03,000\nlast one\n"
    segments = transcript.parse_pasted(srt)
    assert [s.start for s in segments] == [1.0, 4.5, 3600.0]
    assert segments[0].text == "Hello there second line" and segments[0].end == 4.5
    assert segments[1].text == "General Kenobi"

    vtt = "WEBVTT\n\n00:01.000 --> 00:03.000\none\n\n00:03.000 --> 00:05.000\ntwo\n\n00:05.000 --> 00:07.000\nthree\n"
    assert [s.text for s in transcript.parse_pasted(vtt)] == ["one", "two", "three"]

    inline = "0:00 so today\n0:07 we look at cells\n[0:15] and then mitosis\n(1:02:03) the end"
    segments = transcript.parse_pasted(inline)
    assert [s.start for s in segments] == [0.0, 7.0, 15.0, 3723.0]
    assert segments[2].text == "and then mitosis"


def test_parse_pasted_without_times_is_an_error():
    with pytest.raises(transcript.TranscriptError):
        transcript.parse_pasted("just some words\nwith no timestamps at all")


def test_build_windows_cover_and_cap():
    segments = transcript.parse_pasted(lecture.pasted_transcript())
    windows = transcript.build_windows(segments, span=18)
    assert len(windows) == len(segments)
    assert windows[0].seg_lo == 0 and windows[0].end - windows[0].start >= 18
    assert windows[-1].seg_hi == len(segments) - 1
    assert len(transcript.build_windows(segments, span=18, max_windows=20)) <= 20


# --- scoring -------------------------------------------------------------------

def test_shorthand_and_stemming():
    assert scoring.tokens("light rxns w/ CO2 -> O2") == ["light", "reaction", "carbon", "dioxid", "oxygen"]
    assert scoring.tokens("makes") == scoring.tokens("making") == scoring.tokens("make")
    assert scoring.tokens("stages") == scoring.tokens("stage") and scoring.tokens("two stages")[0] == "2"
    assert scoring.tokens("released") == scoring.tokens("release") == scoring.tokens("releases")
    assert scoring.tokens("photosynthesis") == ["photosynthesis"] and scoring.tokens("cactus") == ["cactus"]
    assert "split" in scoring.tokens("splits") and "stop" in scoring.tokens("stopped")
    assert scoring.tokens("the and of") == []


def test_keyword_coverage():
    windows = ["we talk about cats and dogs", "the mitochondria is the powerhouse of the cell", "rubisco fixes carbon dioxide"]
    cov = scoring.keyword_coverage(["mitochondria = powerhouse", "CO2 fixed by rubisco", "zzz qqq", "TEST on cats friday ch 8"], windows)
    assert cov.shape == (4, 3)
    assert cov[0, 1] == pytest.approx(1.0) and cov[0, 0] == 0.0
    assert cov[1, 2] == pytest.approx(1.0)
    assert float(cov[2].max()) == 0.0
    assert 0 < cov[3, 0] < 0.3       # one shared word out of five is weak evidence


def test_evidence_combines_like_independent_witnesses():
    meaning = np.array([[0.50, 0.50, 0.80, 0.50, 0.50]], dtype=np.float32)
    assert scoring.meaning_evidence(meaning)[0].tolist() == pytest.approx([0, 0, 1, 0, 0])


# --- alignment -------------------------------------------------------------------

def test_align_prefers_in_order_over_a_slightly_better_out_of_order_peak():
    starts = [0, 30, 60, 90, 120, 150]
    z = np.zeros((3, 6))
    z[0, 1] = 0.9
    z[1, 2] = 0.8; z[1, 0] = 0.9   # the earlier window scores a little higher, but is out of order
    z[2, 4] = 0.9
    assert align.align(z, starts) == [1, 2, 4]


def test_align_goes_backwards_when_the_evidence_is_strong():
    starts = [0, 30, 60, 90, 120, 150]
    z = np.zeros((3, 6))
    z[0, 3] = 0.9; z[1, 4] = 0.9
    z[2, 0] = 1.0                  # written last on the page, clearly about the very start
    z[2, 5] = 0.5
    assert align.align(z, starts) == [3, 4, 0]


def test_align_leaves_weak_lines_unlinked():
    starts = [0, 30, 60]
    z = np.array([[0.9, 0, 0], [0.3, 0.35, 0.2], [0, 0, 0.95]])
    assert align.align(z, starts) == [0, None, 2]
    assert align.align(np.full((2, 3), 0.3), starts) == [None, None]
    assert align.align(np.zeros((0, 3)), starts) == []


def test_align_matches_brute_force_on_random_small_cases():
    import itertools
    from margin import config
    rng = np.random.default_rng(7)
    for _ in range(60):
        n, m = int(rng.integers(1, 5)), int(rng.integers(1, 6))
        starts = np.sort(rng.uniform(0, 600, size=m))
        z = rng.uniform(0, 1, size=(n, m)).round(3)
        best_total = 0.0
        for combo in itertools.product([None] + list(range(m)), repeat=n):
            total, last = 0.0, None
            for i, j in enumerate(combo):
                if j is None:
                    continue
                total += z[i, j] - config.MATCH_THRESHOLD
                if last is not None and j < last:
                    total -= float(align.backward_penalty(np.array(starts[last] - starts[j])))
                last = j
            best_total = max(best_total, total)
        got = align.align(z, starts)
        total, last = 0.0, None
        for i, j in enumerate(got):
            if j is None:
                continue
            total += z[i, j] - config.MATCH_THRESHOLD
            if last is not None and j < last:
                total -= float(align.backward_penalty(np.array(starts[last] - starts[j])))
            last = j
        assert total == pytest.approx(best_total, abs=1e-6), (z, starts, got)


# --- the lecture, end to end (keywords only: no model download in tests) ---------------

@pytest.fixture(scope="module")
def linked():
    segments = transcript.parse_pasted(lecture.pasted_transcript())
    lines = [{"text": text, "box": None} for text, _ in lecture.NOTES]
    return pipeline.link_lines(lines, segments, use_meaning=False)


def test_lecture_lines_land_on_the_right_moment(linked):
    wrong = []
    for line, (text, expected) in zip(linked["lines"], lecture.NOTES):
        if expected is None:
            if line["t"] is not None:
                wrong.append((text, "should be unlinked", line["t"]))
            continue
        target = expected * lecture.SECONDS_PER_CAPTION
        if line["t"] is None or abs(line["t"] - target) > 10:
            wrong.append((text, target, line["t"]))
    assert not wrong, wrong


def test_lecture_repeated_terms_use_the_first_explanation_not_the_recap(linked):
    atp = next(l for l in linked["lines"] if l["text"].startswith("makes ATP"))
    assert atp["t"] < 110
    assert any(alt["t"] > 120 for alt in atp["alternatives"])  # the later mentions are offered instead


def test_lecture_payload_shape(linked):
    assert linked["matching"] == "keywords"
    grid = linked["map"]
    assert len(grid["cells"]) == len(lecture.NOTES) and len(grid["path"]) == len(lecture.NOTES)
    assert all(0 <= v <= 255 for row in grid["cells"] for v in row)
    line = linked["lines"][4]
    assert line["confidence"] in {"high", "medium", "low"} and "thylakoid" in line["caption"]
