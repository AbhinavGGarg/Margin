"""Choose one moment of the video for each line of the notes.

Picking each line's best-scoring moment independently goes wrong whenever a word is said more than
once: "ATP" comes up six times in a biology lecture. Notes, though, are written in roughly the
order things were said. So the lines are aligned together, with dynamic programming, to find the
set of links that scores highest overall while

* keeping lines in time order where it can (going backwards costs something, more for big jumps,
  so a line added later at the bottom of the page can still link to an earlier moment), and
* leaving a line unlinked when nothing in the video is clearly about it (a heading, a doodle
  caption, the student's own aside).

This is the same family of algorithm used to align DNA sequences or subtitles to audio. It runs in
O(lines x windows) time using running prefix and suffix maxima.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

import numpy as np

from . import config
from .scoring import tokens
from .transcript import Segment, Window

NEG = -1e9


@dataclass
class Alternative:
    window: int
    score: float


@dataclass
class LineMatch:
    """The link for one line. `window` is None when the line was left unlinked."""

    window: Optional[int]
    score: float = 0.0
    confidence: str = "none"  # "high", "medium", "low" or "none"
    alternatives: List[Alternative] = field(default_factory=list)


def backward_penalty(seconds_back: np.ndarray) -> np.ndarray:
    return np.minimum(
        config.BACKWARD_BASE + config.BACKWARD_PER_MINUTE * (seconds_back / 60.0),
        config.BACKWARD_MAX,
    )


def align(
    evidence: np.ndarray, starts: Sequence[float], threshold: float = config.MATCH_THRESHOLD
) -> List[Optional[int]]:
    """Return, for each line, the index of its window, or None.

    evidence: [lines, windows] scores between 0 and 1.
    starts:   start time in seconds of each window, ascending.
    """
    z = evidence
    n_lines, n_windows = z.shape
    if n_lines == 0 or n_windows == 0:
        return [None] * n_lines
    t = np.asarray(starts, dtype=np.float64)
    gain = z.astype(np.float64) - threshold
    a, b, cap = config.BACKWARD_BASE, config.BACKWARD_PER_MINUTE / 60.0, config.BACKWARD_MAX

    # prev[j]: best total so far with the most recent link at window j. "No link yet" scores 0.
    prev = np.full(n_windows, NEG)
    came_from = np.full((n_lines, n_windows), -2, dtype=np.int32)  # -1: first link, >=0: previous window
    skipped = np.zeros((n_lines, n_windows), dtype=bool)
    index = np.arange(n_windows)

    for i in range(n_lines):
        # Forward (or same window): best of prev[0..j], free of charge.
        fwd_arg = np.maximum.accumulate(np.where(prev == np.maximum.accumulate(prev), index, 0))
        fwd_val = np.maximum.accumulate(prev)

        # Backward: best of prev[j'] - penalty(t[j'] - t[j]) over j' > j, where
        # penalty = min(a + b*dt, cap), i.e. the better of two suffix maxima.
        lin = prev - b * t
        lin_val, lin_arg = _suffix_max_exclusive(lin)
        cap_val, cap_arg = _suffix_max_exclusive(prev)
        back_lin = lin_val + b * t - a
        back_cap = cap_val - cap
        use_lin = back_lin >= back_cap
        back_val = np.where(use_lin, back_lin, back_cap)
        back_arg = np.where(use_lin, lin_arg, cap_arg)

        best_prev = np.zeros(n_windows)            # start a new chain: nothing linked before
        best_from = np.full(n_windows, -1, dtype=np.int32)
        better = fwd_val > best_prev
        best_prev = np.where(better, fwd_val, best_prev)
        best_from = np.where(better, fwd_arg, best_from)
        better = back_val > best_prev
        best_prev = np.where(better, back_val, best_prev)
        best_from = np.where(better, back_arg, best_from)

        linked = gain[i] + best_prev
        skip = prev  # leave this line unlinked; the most recent link stays where it was
        take_skip = skip > linked
        skipped[i] = take_skip
        came_from[i] = np.where(take_skip, index, best_from)
        prev = np.where(take_skip, skip, linked)

    result: List[Optional[int]] = [None] * n_lines
    j = int(np.argmax(prev))
    if prev[j] <= 0:  # linking nothing at all is at least as good
        return result
    for i in range(n_lines - 1, -1, -1):
        if skipped[i, j]:
            continue
        result[i] = j
        j = int(came_from[i, j])
        if j < 0:
            break
    return result


def _suffix_max_exclusive(values: np.ndarray):
    """For each j, the max (and its index) of values[j+1:]. NEG where the suffix is empty."""
    n = len(values)
    out_val = np.full(n, NEG)
    out_arg = np.zeros(n, dtype=np.int32)
    best, best_index = NEG, 0
    for j in range(n - 1, -1, -1):
        out_val[j], out_arg[j] = best, best_index
        if values[j] > best:
            best, best_index = values[j], j
    return out_val, out_arg


def confidence_of(
    score: float,
    row: Optional[np.ndarray] = None,
    starts: Optional[Sequence[float]] = None,
    chosen: Optional[int] = None,
) -> str:
    """How sure the link is: strong evidence, and not many other moments that look just as good."""
    level = 2 if score >= config.CONFIDENCE_HIGH - 1e-6 else (1 if score >= config.CONFIDENCE_MEDIUM - 1e-6 else 0)
    if row is not None and starts is not None and len(row) > 1:
        # Count distinct stretches of the video (not overlapping windows) that score nearly as well.
        rivals = alternatives_for(row, starts, chosen, limit=6, floor=score - 0.08)
        if len(rivals) >= 5:
            level = 0
        elif len(rivals) >= 3:
            level = min(level, 1)
    return ("low", "medium", "high")[level]


def alternatives_for(
    z_row: np.ndarray,
    starts: Sequence[float],
    chosen: Optional[int],
    limit: int = 3,
    floor: float = config.ALTERNATIVE_FLOOR,
    min_gap: float = config.ALTERNATIVE_MIN_GAP_SECONDS,
) -> List[Alternative]:
    """Other strong moments for a line, far enough apart in time to be genuinely different."""
    taken = [starts[chosen]] if chosen is not None else []
    picks: List[Alternative] = []
    for j in np.argsort(-z_row):
        j = int(j)
        if z_row[j] < floor or len(picks) >= limit:
            break
        if any(abs(starts[j] - other) < min_gap for other in taken):
            continue
        taken.append(starts[j])
        picks.append(Alternative(j, float(z_row[j])))
    return picks


def match_lines(evidence: np.ndarray, starts: Sequence[float]) -> List[LineMatch]:
    z = evidence
    chosen = align(z, starts)
    matches: List[LineMatch] = []
    for i, j in enumerate(chosen):
        alternatives = alternatives_for(z[i], starts, j)
        if j is None:
            matches.append(LineMatch(None, 0.0, "none", alternatives))
        else:
            value = float(z[i, j])
            matches.append(LineMatch(j, value, confidence_of(value, z[i], starts, j), alternatives))
    return matches


# --- From a window to an exact second ---------------------------------------------


def refine_start(line: str, window: Window, segments: List[Segment]) -> int:
    """Index of the caption inside the window where the line's content begins.

    A window is ~18 seconds; jumping to its start can mean a long wait before the relevant
    sentence. This picks the earliest caption (looked at together with the one after it) that
    shares most of the words the line has in common with the window.
    """
    wanted = set(tokens(line))
    if not wanted:
        return window.seg_lo
    seg_tokens = [set(tokens(segments[k].text)) for k in range(window.seg_lo, window.seg_hi + 1)]
    overlaps = []
    for offset in range(len(seg_tokens)):
        pair = seg_tokens[offset] | (seg_tokens[offset + 1] if offset + 1 < len(seg_tokens) else set())
        overlaps.append(len(wanted & pair))
    best = max(overlaps)
    if best == 0:
        return window.seg_lo
    needed = max(1.0, 0.6 * best)
    for offset, value in enumerate(overlaps):
        if value >= needed:
            return window.seg_lo + offset
    return window.seg_lo


def excerpt(segments: List[Segment], first: int, last: int, limit: int = 260) -> str:
    """Caption text from segment `first` on, for showing what was said at a moment."""
    parts, size = [], 0
    for k in range(first, min(last, len(segments) - 1) + 1):
        parts.append(segments[k].text)
        size += len(segments[k].text) + 1
        if size >= limit:
            break
    text = " ".join(parts)
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


# --- A picture of the alignment ------------------------------------------------------


def match_map(
    evidence: np.ndarray, starts: Sequence[float], chosen: List[Optional[int]], columns: int = 180
) -> dict:
    """Downsample the evidence matrix for display: rows are lines, columns are stretches of video."""
    z = evidence
    n_lines, n_windows = z.shape
    columns = max(1, min(columns, n_windows))
    edges = np.linspace(0, n_windows, columns + 1).astype(int)
    grid = np.zeros((n_lines, columns), dtype=np.float32)
    for c in range(columns):
        lo, hi = edges[c], max(edges[c + 1], edges[c] + 1)
        grid[:, c] = z[:, lo:hi].max(axis=1)
    scaled = np.clip(grid, 0.0, 1.0)
    path = []
    for j in chosen:
        path.append(None if j is None else int(min(np.searchsorted(edges, j, side="right") - 1, columns - 1)))
    return {
        "columns": columns,
        "times": [float(starts[int(edges[c])]) for c in range(columns)],
        "cells": [[int(round(v * 255)) for v in row] for row in scaled],
        "path": path,
    }
