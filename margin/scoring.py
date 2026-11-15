"""How much evidence is there that a line of the notes is about a given moment of the video?

Two signals are combined into one evidence score between 0 and 1:

* keywords: the share of the line's informative words that are said in that moment, with rare
  words counting for more. Students mostly write down the terms they heard, so this carries a lot.
  It needs no model and always works.
* meaning: sentence-embedding similarity, for lines that paraphrase ("plants make sugar from
  light" vs. "photosynthesis converts light energy into glucose"). Uses a small local model
  through fastembed when it is installed. A vague line is somewhat similar to everything, so each
  line's similarity is measured against its own typical level across the video.

They are combined like independent witnesses (a "noisy OR"): either one alone can make the case,
and agreement makes it stronger.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from . import config

log = logging.getLogger("margin.scoring")

# --- Text normalisation ----------------------------------------------------------

# Shorthand students use in notes, expanded so it can match spoken words.
_SHORTHAND = [
    (re.compile(r"\bw/o\b", re.I), " without "),
    (re.compile(r"\bw/(?=\s|\w)", re.I), " with "),
    (re.compile(r"\bb/c\b|\bbc\b", re.I), " because "),
    (re.compile(r"\bb/w\b", re.I), " between "),
    (re.compile(r"\be\.?g\.?(?=\W|$)", re.I), " for example "),
    (re.compile(r"\bi\.?e\.?(?=\W|$)", re.I), " that is "),
    (re.compile(r"\bvs\.?\b", re.I), " versus "),
    (re.compile(r"\bdef\b\.?", re.I), " definition "),
    (re.compile(r"\beq\b\.?|\beqn\b\.?", re.I), " equation "),
    (re.compile(r"\bapprox\b\.?", re.I), " approximately "),
    (re.compile(r"\brxns\b", re.I), " reactions "),
    (re.compile(r"\brxn\b", re.I), " reaction "),
    (re.compile(r"\bco2\b", re.I), " carbon dioxide "),
    (re.compile(r"\bh2o\b", re.I), " water "),
    (re.compile(r"\bo2\b", re.I), " oxygen "),
    (re.compile(r"\btemp\b\.?", re.I), " temperature "),
    (re.compile(r"->|=>|→|⇒|➔"), " leads to "),
    (re.compile(r"<-|←"), " comes from "),
    (re.compile(r"↑"), " increases "),
    (re.compile(r"↓"), " decreases "),
    (re.compile(r"≈|~(?=\d)"), " approximately "),
    (re.compile(r"≠"), " is not "),
    (re.compile(r"(?<=\w)\s*=\s*(?=\w)"), " equals "),
    (re.compile(r"&|\+(?=\s)"), " and "),
    (re.compile(r"%"), " percent "),
    (re.compile(r"#(?=\s*\d)"), " number "),
]

_STOPWORDS = frozenset(
    """a an and are as at be been being but by can could did do does doing for from had has have
    having he her here hers him his how i if in into is it its just like me my no not of on or our
    out so some than that the their them then there these they this those to too up us was we were
    what when where which who why will with would you your about also all any because going gonna
    get got just kind know let lets now okay ok one really right see so sort thing things think
    uh um very want way well yeah actually basically called say said says go goes into over again
    more most much many little bit lot something each other only even still here
    equals equal leads lead comes versus example definition approximately number""".split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def normalise(text: str) -> str:
    """Expand note shorthand and symbols into words."""
    out = " " + (text or "") + " "
    for pattern, replacement in _SHORTHAND:
        out = pattern.sub(replacement, out)
    return re.sub(r"\s+", " ", out).strip()


_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "first": "1", "second": "2", "third": "3",
}


def _stem(token: str) -> str:
    """A deliberately small stemmer: enough to match plurals and common verb endings.

    "makes", "making" and "make" all become "mak"; "stages" and "stage" become "stag". The stems
    are never shown to anyone, they only need to agree with each other.
    """
    if token in _NUMBER_WORDS:
        return _NUMBER_WORDS[token]
    if len(token) <= 3 or token.isdigit():
        return token
    stem = token
    if stem.endswith("ies") and len(stem) >= 5:
        stem = stem[:-3] + "y"
    elif stem.endswith(("sses", "ches", "shes", "xes", "zes")):
        stem = stem[:-2]
    elif stem.endswith("s") and not stem.endswith(("ss", "us", "is")):
        stem = stem[:-1]
    for suffix in ("ing", "ed"):
        if stem.endswith(suffix) and len(stem) - len(suffix) >= 3:
            stem = stem[: -len(suffix)]
            if len(stem) >= 4 and stem[-1] == stem[-2] and stem[-1] not in "aeiouls":
                stem = stem[:-1]  # "stopped" -> "stop"
            break
    if stem.endswith("e") and len(stem) >= 4:
        stem = stem[:-1]
    return stem


def tokens(text: str) -> List[str]:
    words = _TOKEN_RE.findall(normalise(text).lower())
    return [_stem(w.replace("'", "")) for w in words if w not in _STOPWORDS and len(w) > 1 or w.isdigit()]


# --- Keyword evidence -------------------------------------------------------------


def keyword_coverage(lines: List[str], windows: List[str]) -> np.ndarray:
    """Share of each line's informative words found in each window, shape [lines, windows].

    Words are weighted by rarity across the video (IDF). A word the video never says still counts
    in the line's total, so "TEST FRIDAY ch. 8" cannot score highly off one stray word.
    """
    window_sets = [set(tokens(w)) for w in windows]
    line_tokens = [list(dict.fromkeys(tokens(line))) for line in lines]
    out = np.zeros((len(lines), len(windows)), dtype=np.float32)
    if not windows:
        return out
    vocabulary: Dict[str, int] = {}
    for toks in line_tokens:
        for tok in toks:
            vocabulary.setdefault(tok, len(vocabulary))
    if not vocabulary:
        return out

    present = np.zeros((len(windows), len(vocabulary)), dtype=np.float32)
    for row, words in enumerate(window_sets):
        for tok in words:
            col = vocabulary.get(tok)
            if col is not None:
                present[row, col] = 1.0
    doc_freq = present.sum(axis=0)
    idf = np.log((len(windows) + 1.0) / (doc_freq + 0.5)).astype(np.float32)
    idf = np.maximum(idf, 0.05)  # a word said in nearly every window is weak evidence, not negative

    for row, toks in enumerate(line_tokens):
        if not toks:
            continue
        cols = [vocabulary[tok] for tok in toks]
        weights = idf[cols].copy()
        said = doc_freq[cols] > 0
        if not said.any():
            continue
        # A word the video never says (a misread word, the student's own aside) counts against the
        # line as much as one of its average matched words, however rare a never-said word looks.
        weights[~said] = float(weights[said].mean())
        out[row] = (present[:, cols] @ weights) / float(weights.sum())
    return out


# --- Meaning similarity ---------------------------------------------------------


class Embedder:
    """Lazy wrapper around a local sentence-embedding model. Thread-safe; fails soft."""

    def __init__(self, model_name: str = config.EMBED_MODEL):
        self.model_name = model_name
        self._model = None
        self._failed: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def unavailable_reason(self) -> Optional[str]:
        return self._failed

    def ready(self) -> bool:
        """Load the model if needed. Returns False (and remembers why) when it can't be used."""
        if self._model is not None:
            return True
        if self._failed:
            return False
        with self._lock:
            if self._model is not None:
                return True
            try:
                from fastembed import TextEmbedding

                self._model = TextEmbedding(self.model_name)
            except ImportError:
                self._failed = "fastembed is not installed"
            except Exception as exc:  # download blocked, unsupported platform, ...
                self._failed = "could not load the model (" + type(exc).__name__ + ")"
                log.warning("Meaning model unavailable: %s", exc)
        return self._model is not None

    def embed(self, texts: List[str]) -> np.ndarray:
        with self._lock:
            vectors = np.array(list(self._model.embed(texts, batch_size=64)), dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.maximum(norms, 1e-8)


_embedder = Embedder()


def get_embedder() -> Embedder:
    return _embedder


def _cache_path(cache_dir: Optional[Path], model_name: str, texts: List[str]) -> Optional[Path]:
    if cache_dir is None:
        return None
    digest = hashlib.sha1(("\n".join(texts) + "|" + model_name).encode("utf-8")).hexdigest()[:20]
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / ("emb_" + digest + ".npy")


def meaning_similarity(
    lines: List[str],
    windows: List[str],
    embedder: Optional[Embedder] = None,
    cache_dir: Optional[Path] = None,
) -> Optional[np.ndarray]:
    """Embedding cosine similarity, or None when no model is available."""
    embedder = embedder or get_embedder()
    if not lines or not windows or not embedder.ready():
        return None
    try:
        path = _cache_path(cache_dir, embedder.model_name, windows)
        if path is not None and path.exists():
            window_vectors = np.load(path)
        else:
            window_vectors = embedder.embed(windows)
            if path is not None:
                np.save(path, window_vectors)
        line_vectors = embedder.embed([normalise(line) for line in lines])
        return (line_vectors @ window_vectors.T).astype(np.float32)
    except Exception as exc:
        log.warning("Meaning similarity failed, falling back to keywords: %s", exc)
        return None


# --- Combining -------------------------------------------------------------------


def meaning_evidence(meaning: np.ndarray) -> np.ndarray:
    """Scale raw cosine similarity to 0..1, measured from each line's typical level."""
    typical = np.median(meaning, axis=1, keepdims=True)
    return np.clip((meaning - typical) / config.MEANING_FULL_MARGIN, 0.0, 1.0).astype(np.float32)


class Scores:
    """What the aligner needs: the combined evidence plus the two signals it came from."""

    def __init__(self, evidence: np.ndarray, keywords: np.ndarray, meaning: Optional[np.ndarray]):
        self.evidence = evidence
        self.keywords = keywords
        self.meaning = meaning

    @property
    def mode(self) -> str:
        return "meaning and keywords" if self.meaning is not None else "keywords"


def score(
    lines: List[str],
    windows: List[str],
    embedder: Optional[Embedder] = None,
    cache_dir: Optional[Path] = None,
    use_meaning: bool = True,
) -> Scores:
    keywords = keyword_coverage(lines, windows)
    meaning = meaning_similarity(lines, windows, embedder, cache_dir) if use_meaning else None
    if meaning is None:
        return Scores(keywords, keywords, None)
    by_meaning = config.MEANING_WEIGHT * meaning_evidence(meaning)
    evidence = 1.0 - (1.0 - keywords) * (1.0 - by_meaning)
    return Scores(evidence.astype(np.float32), keywords, meaning)
