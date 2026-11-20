"""A plot digitised: a figure's curve turned back into the (x, y) points it was drawn from.

The workflow is WebPlotDigitizer's, run here on the figure's own pixels:

1. **The image.** A figure is cut from its page as `figures.crop` cuts it — a PDF paper's
   picture node, or an XML paper's figure on the page of the PDF kept for its figures that
   prints its caption — or an image file is opened as it is. It is saved as a PNG under the
   library (`digitised/`) so the window can draw it.
2. **The axes.** Two points of known value on each axis calibrate it (`Calibration`): linear or
   log10, each axis on its own; the four points need not be square to the image, so a scanned,
   slightly turned plot maps as well as a drawn one. The machine offers a calibration when it
   can read one (`auto_axes`): the frame and the numbers beside its axes, read as `charts.py`
   reads them — the PDF's text layer for a vector figure, OCR for an image — two ticks per
   axis taken from a scale that holds within `charts.MAX_RESIDUAL`. It is an offer: a person
   confirms it or moves the points.
3. **The series.** A person picks a series by its colour — a click on the curve, or one of the
   figure's own colours (`palette`) — and its points are found where that colour is
   (`extract`): along a line, the middle of the colour's run in each column, followed from
   column to column so a crossing curve of the same colour is not jumped to; for markers, each
   blob of the colour, its centre, two run together parted by their area. Gridlines and the
   axes themselves — long straight strokes — are not a series even when they share its colour.
4. **The points.** Every point is kept with its pixel and how it came (`auto`, `added`,
   `moved`), so a person can add, move and delete, and its value is always the calibration's
   reading of its pixel: one source of truth, the pixels.

Saved, a series is rows (`digitised`, `digitised_points`, `digitised_fits`): the figure, the
calibration, the method, every point; exported, a CSV with the axes' units in its header and a
JSON sidecar that traces each value back to the paper, the figure, the page and the pixel
(`export`). A series belongs to the picture it was read from, known by its hash, and a copy of
that picture is kept with it. A person's digitisation is never dropped by a rebuild: while the
paper still draws that picture the series stays with the figure that draws it (`reattach`); when
none does it is kept, listed for a person to review beside its picture, and put on a figure only
by them (`attach`).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from . import charts
from .charts import Word

METHOD = "digitise-1"
MAX_SIDE = 3000  # pixels: a figure rendered larger is scaled down to this on its longer side
TOLERANCE = 60.0  # RGB distance within which a pixel is the series' colour
SCAN_INK = 200  # a scan's pixel darker than this in some channel is ink, for finding its axes
DIRS = "digitised"


# -- the calibration ------------------------------------------------------------------------------------------


@dataclass
class Axis:
    """Two points of known value on one axis: pixels (x, y down) and their values."""

    p1: tuple[float, float]
    v1: float
    p2: tuple[float, float]
    v2: float
    log: bool = False

    def t(self, v: float) -> float:
        if self.log:
            if v <= 0:
                raise ValueError(f"a log axis has no value {v}: its calibration values must be above zero")
            return math.log10(v)
        return v

    def to_dict(self) -> dict[str, Any]:
        return {"p1": [round(self.p1[0], 3), round(self.p1[1], 3)], "v1": self.v1, "p2": [round(self.p2[0], 3), round(self.p2[1], 3)], "v2": self.v2, "log": self.log}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Axis":
        return cls((float(d["p1"][0]), float(d["p1"][1])), float(d["v1"]), (float(d["p2"][0]), float(d["p2"][1])), float(d["v2"]), bool(d.get("log")))


class Calibration:
    """Pixels to values and back. The x axis's two points share a y value (they lie on a line
    of constant y: the x axis, or level with it); the y axis's share an x value. Those four
    facts and the four values fix an affine map from pixels to (log-)values on each axis."""

    def __init__(self, x: Axis, y: Axis):
        self.x, self.y = x, y
        X1, X2, Y1, Y2 = x.p1, x.p2, y.p1, y.p2
        if x.v1 == x.v2 or y.v1 == y.v2:
            raise ValueError("each axis needs two different values")
        if math.dist(X1, X2) < 1 or math.dist(Y1, Y2) < 1:
            raise ValueError("each axis needs two points apart")
        # row for x values: x(X1) = x1, x(X2) = x2, x(Y1) = x(Y2)
        a = np.array([[X1[0], X1[1], 1.0], [X2[0], X2[1], 1.0], [Y1[0] - Y2[0], Y1[1] - Y2[1], 0.0]])
        b = np.array([[Y1[0], Y1[1], 1.0], [Y2[0], Y2[1], 1.0], [X1[0] - X2[0], X1[1] - X2[1], 0.0]])
        if abs(np.linalg.det(a)) < 1e-9 or abs(np.linalg.det(b)) < 1e-9:
            raise ValueError("the calibration points are in line: the x axis's two points and the y axis's two must run in different directions")
        rx = np.linalg.solve(a, np.array([x.t(x.v1), x.t(x.v2), 0.0]))
        ry = np.linalg.solve(b, np.array([y.t(y.v1), y.t(y.v2), 0.0]))
        self.m = np.vstack([rx, ry])  # [[ax, bx, cx], [ay, by, cy]]

    def value(self, px: float, py: float) -> tuple[float, float]:
        u, v = self.m @ np.array([px, py, 1.0])
        return (10 ** u if self.x.log else float(u)), (10 ** v if self.y.log else float(v))

    def to_dict(self) -> dict[str, Any]:
        return {"x": self.x.to_dict(), "y": self.y.to_dict()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Calibration":
        return cls(Axis.from_dict(d["x"]), Axis.from_dict(d["y"]))


# -- the series, found by its colour ----------------------------------------------------------------------------


def hex_of(c: Sequence[int]) -> str:
    return "#" + "".join(f"{int(v):02x}" for v in c[:3])


def rgb_of(h: str | Sequence[int]) -> tuple[int, int, int]:
    if isinstance(h, str):
        s = h.strip().lstrip("#")
        if not re.fullmatch(r"[0-9a-fA-F]{6}", s):
            raise ValueError(f"not a colour: {h!r} (#rrggbb)")
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return int(h[0]), int(h[1]), int(h[2])


def colour_mask(img: np.ndarray, colour: Sequence[int], tol: float = TOLERANCE) -> np.ndarray:
    """The pixels within `tol` (RGB distance) of `colour`."""
    d = img[..., :3].astype(np.int32) - np.array(colour[:3], np.int32)
    return (d * d).sum(axis=2) <= tol * tol


def _straight(mask: np.ndarray, frac: float = 0.75) -> np.ndarray:
    """The long straight strokes of a mask — axes, gridlines, a frame — at least `frac` of the
    region across (a gridline, not a curve's plateau), as a mask of their own."""
    h, w = mask.shape
    out = np.zeros_like(mask)
    thick = max(3, int(0.006 * max(h, w)))
    for ln in charts.lines(mask, max(20, int(frac * w)), thick, horizontal=True):
        out[ln.b0 : ln.b1 + 1, ln.a0 : ln.a1 + 1] = True
    for ln in charts.lines(mask, max(20, int(frac * h)), thick, horizontal=False):
        out[ln.a0 : ln.a1 + 1, ln.b0 : ln.b1 + 1] = True
    return out


def region_box(img: np.ndarray, region: Sequence[float] | None) -> tuple[int, int, int, int]:
    h, w = img.shape[:2]
    if not region:
        return 0, 0, w, h
    x0, y0, x1, y1 = (int(round(v)) for v in region)
    x0, x1 = sorted((max(0, min(w, x0)), max(0, min(w, x1))))
    y0, y1 = sorted((max(0, min(h, y0)), max(0, min(h, y1))))
    if x1 - x0 < 4 or y1 - y0 < 4:
        raise ValueError("the region to search is too small")
    return x0, y0, x1, y1


def series_mask(img: np.ndarray, colour: Sequence[int], tol: float, region: Sequence[float] | None) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """The series' pixels inside the region, without the long straight strokes of its colour."""
    x0, y0, x1, y1 = region_box(img, region)
    sub = img[y0:y1, x0:x1]
    m = colour_mask(sub, colour, tol)
    m &= ~_straight(m)
    return m, (x0, y0, x1, y1)


def _tracks(mask: np.ndarray, max_jump: float, gap: int) -> list[list[tuple[float, float]]]:
    """The colour's runs, column by column, chained into tracks: each run joins the track whose
    last run it touches — their rows overlap, or come within `max_jump` of each other — within
    `gap` columns, one run per track per column, the nearest middles first; a run touching none
    starts a track of its own. Touching, not middles near: on a steep stretch a column's run is
    long and its middle far from the last one's."""
    active: list[list[tuple[float, float, int, int]]] = []
    done: list[list[tuple[float, float, int, int]]] = []
    for c in np.flatnonzero(mask.any(axis=0)):
        runs = charts._runs(mask[:, c], 1)
        still = []
        for tr in active:
            (done if c - tr[-1][0] > gap else still).append(tr)
        active = still
        pairs = []
        for i, (rs, re_) in enumerate(runs):
            for j, tr in enumerate(active):
                _, ly, ls, le = tr[-1]
                apart = max(rs - le, ls - re_, 0)  # rows between the two runs; 0 when they overlap
                if apart <= max_jump:
                    pairs.append((apart, abs((rs + re_) / 2 - ly), i, j))
        pairs.sort()
        took_run, took_track = set(), set()
        for _, _, i, j in pairs:
            if i in took_run or j in took_track:
                continue
            rs, re_ = runs[i]
            active[j].append((float(c), (rs + re_) / 2, rs, re_))
            took_run.add(i)
            took_track.add(j)
        for i, (rs, re_) in enumerate(runs):
            if i not in took_run:
                active.append([(float(c), (rs + re_) / 2, rs, re_)])
    return [[(x, y) for x, y, _, _ in tr] for tr in done + active]


def _continues(a: tuple[float, float], b: tuple[float, float], max_jump: float) -> bool:
    """Whether a track starting at `b` continues one ending at `a`, left of it: little rise or
    fall for the columns between — or, on a steep stretch drawn in pieces, an end close by."""
    dx, dy = b[0] - a[0], abs(b[1] - a[1])
    return dy <= max_jump + 0.25 * dx or math.hypot(dx, dy) <= 1.5 * max_jump


def extract_line(mask: np.ndarray, max_jump: float = 0) -> list[tuple[float, float]]:
    """A line's points: the colour's runs chained into tracks from column to column
    (`_tracks`), the longest track taken, and lengthened by the tracks before and after it that
    continue it — that start within `max_jump` of where it ends — so a gap where another curve
    is drawn over it is crossed, a steep stretch drawn in pieces is joined, and a legend's swatch, a
    word or a stray mark is not. Then
    thinned to one point, their mean, every hundredth of the width along the line (at least 2
    pixels), so a steep rise keeps as many points as a level stretch."""
    h, w = mask.shape
    if not mask.any():
        return []
    max_jump = max_jump or max(8.0, 0.08 * h)
    tracks = [t for t in _tracks(mask, max_jump=max(2.0, 0.01 * h), gap=max(3, int(0.015 * w))) if t]
    if not tracks:
        return []
    best = max(tracks, key=lambda t: (t[-1][0] - t[0][0], len(t)))
    line = list(best)
    rest = [t for t in tracks if t is not best]
    while True:  # rightwards
        end = line[-1]
        # a piece may share a column with the line's end (a steep stretch splits a column's run): its part beyond
        nxt = [t for t in rest if t[-1][0] > end[0] and t[0][0] >= end[0] - 1 and _continues(end, next(p for p in t if p[0] > end[0]), max_jump)]
        if not nxt:
            break
        t = min(nxt, key=lambda t: math.hypot(t[0][0] - end[0], t[0][1] - end[1]))
        line += [p for p in t if p[0] > end[0]]
        rest.remove(t)
    while True:  # leftwards
        start = line[0]
        prv = [t for t in rest if t[0][0] < start[0] and t[-1][0] <= start[0] + 1 and _continues(next(p for p in reversed(t) if p[0] < start[0]), start, max_jump)]
        if not prv:
            break
        t = min(prv, key=lambda t: math.hypot(start[0] - t[-1][0], start[1] - t[-1][1]))
        line = [p for p in t if p[0] < start[0]] + line
        rest.remove(t)
    step = max(2.0, w / 100)
    out: list[tuple[float, float]] = []
    bucket: list[tuple[float, float]] = []
    along = 0.0  # pixels along the line since the last point: a steep rise keeps its points
    prev = line[0]
    for x, y in line:
        along += math.hypot(x - prev[0], y - prev[1])
        prev = (x, y)
        if along >= step and bucket:
            out.append((float(np.mean([p[0] for p in bucket])), float(np.mean([p[1] for p in bucket]))))
            bucket = []
            along = 0.0
        bucket.append((x, y))
    if bucket:
        out.append((float(np.mean([p[0] for p in bucket])), float(np.mean([p[1] for p in bucket]))))
    return out


def _cores(mask: np.ndarray) -> tuple[np.ndarray, float, np.ndarray]:
    """What is thick in a mask: the pixels at least `t` from its edge, where `t` is most of the
    usual marker's radius — the widest point of each of its shapes, their median among the
    shapes thicker than a stroke. A line, however thick, leaves only a narrow band; a marker
    leaves its core. (cores, the marker's radius, the distance transform)."""
    import cv2

    dt = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 3)
    n, lab = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    if n <= 1:
        return np.zeros_like(mask), 0.0, dt
    rmax = np.zeros(n)
    np.maximum.at(rmax, lab.ravel(), dt.ravel())
    rmax = rmax[1:]
    thick = rmax[rmax >= 2.0]
    if not len(thick):
        return np.zeros_like(mask), 0.0, dt
    r = float(np.median(thick))
    t = max(1.5, 0.6 * r)
    return dt >= t, r, dt


def _compact(blobs: list[tuple[int, int, int, int, int, np.ndarray]], r: float) -> list[tuple[int, int, int, int, int, np.ndarray]]:
    out = []
    for b in blobs:
        w, h = b[2] - b[0] + 1, b[3] - b[1] + 1
        if 0.45 <= w / h <= 2.2 and max(w, h) <= 3.0 * max(r, 1.0) * 1.2:
            out.append(b)
    return out


def extract_markers(mask: np.ndarray, min_area: int = 0) -> list[tuple[float, float]]:
    """Markers' centres: the cores of the colour's thick shapes (`_cores`), each grown back to
    the marker it is the core of and centred on its box — the thin strokes joining markers fall
    away; a core about n times the usual area is n markers run together, parted by k-means."""
    import cv2

    if not mask.any():
        return []
    cores, r, dt = _cores(mask)
    if not cores.any():
        return []
    blobs = [b for b in charts._blobs(cores) if b[4] >= max(min_area, 1)]
    if not blobs:
        return []
    med = float(np.median([b[4] for b in blobs]))
    blobs = [b for b in blobs if b[4] >= 0.25 * med]
    t = max(1.5, 0.6 * r)
    grow = int(math.ceil(t)) + 1
    disc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1))
    h, w = mask.shape
    out: list[tuple[float, float]] = []
    for bx0, by0, bx1, by1, area, own in blobs:
        n = int(round(area / med)) if med else 1
        ys, xs = np.nonzero(own)
        if 2 <= n <= 6 and len(xs) >= 2 * n:
            data = np.column_stack([xs, ys]).astype(np.float32)
            crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 0.1)
            cv2.setRNGSeed(0)
            _, _, centres = cv2.kmeans(data, n, None, crit, 3, cv2.KMEANS_PP_CENTERS)
            out += [(bx0 + float(cx), by0 + float(cy)) for cx, cy in centres]
            continue
        # the marker grown back from its core, within the colour: its box's middle
        x0, y0, x1, y1 = max(bx0 - grow, 0), max(by0 - grow, 0), min(bx1 + grow + 1, w), min(by1 + grow + 1, h)
        core = np.zeros((y1 - y0, x1 - x0), np.uint8)
        core[by0 - y0 : by1 - y0 + 1, bx0 - x0 : bx1 - x0 + 1] = own
        back = cv2.dilate(core, disc).astype(bool) & mask[y0:y1, x0:x1]
        yy, xx = np.nonzero(back)
        if len(xx):
            out.append((x0 + (xx.min() + xx.max()) / 2, y0 + (yy.min() + yy.max()) / 2))
        else:
            out.append((bx0 + float(xs.mean()), by0 + float(ys.mean())))
    out.sort()
    return out


def looks_like_markers(mask: np.ndarray) -> bool:
    """Markers, not a line: the colour's thick cores (`_cores`) stand in three or more compact
    blobs that hold most of what is thick in it — a line's core is one long band, a word's
    strokes too thin to have one."""
    if not mask.any():
        return False
    cores, r, _ = _cores(mask)
    if r < 2.5 or not cores.any():
        return False
    blobs = charts._blobs(cores)
    small = _compact(blobs, r)
    if len(small) < 3:
        return False
    return sum(b[4] for b in small) >= 0.6 * sum(b[4] for b in blobs)


def extract(img: np.ndarray, colour: str | Sequence[int], *, tol: float = TOLERANCE, region: Sequence[float] | None = None, mode: str = "auto",
            calibration: Calibration | None = None, words: list[Word] | None = None) -> dict[str, Any]:
    """A series' points found by its colour: `{mode, colour, tolerance, region, pixels,
    legend, points: [{px, py, x, y, how}]}`. `mode` is `line`, `markers` or `auto` (markers when
    the colour stands in compact blobs of one size, else a line). Given the figure's `words`, a
    marker with a word set level just to its right is a legend's, and is left out."""
    rgb = rgb_of(colour)
    mask, (x0, y0, x1, y1) = series_mask(img, rgb, tol, region)
    chosen = mode
    if mode == "auto":
        chosen = "markers" if looks_like_markers(mask) else "line"
    legend = 0
    if chosen == "markers":
        pts = extract_markers(mask)
        if words:
            labels = [w for w in words if charts.number(w.text) is None and len(w.text.strip()) >= 2 and w.h <= 1.2 * w.w]
            kept = [(px, py) for px, py in pts if not any(abs(y0 + py - w.cy) <= 0.7 * w.h and 0 <= w.x0 - (x0 + px) <= 5 * w.h for w in labels)]
            legend = len(pts) - len(kept)
            pts = kept
    elif chosen == "line":
        pts = extract_line(mask)
    else:
        raise ValueError(f"no mode {mode!r}: line, markers or auto")
    out = []
    for px, py in pts:
        # a pixel's index is its left (top) edge: its middle, where the ink is, is half a pixel on
        p = {"px": round(float(x0 + px + 0.5), 2), "py": round(float(y0 + py + 0.5), 2), "how": "auto"}
        if calibration is not None:
            x, y = calibration.value(p["px"], p["py"])
            p["x"], p["y"] = _r(x), _r(y)
        out.append(p)
    return {"mode": chosen, "asked": mode, "colour": hex_of(rgb), "tolerance": tol, "region": [x0, y0, x1, y1], "pixels": int(mask.sum()), "legend": legend, "points": out}


def colour_at(img: np.ndarray, px: float, py: float, reach: int = 2) -> str | None:
    """The colour a click at (px, py) picks, as the window picks it: of the pixels within `reach`,
    the one most like ink — dark and saturated — since a thin line is easy to miss by a pixel;
    None on paper."""
    h, w = img.shape[:2]
    x, y = int(round(px)), int(round(py))
    box = img[max(y - reach, 0) : min(y + reach + 1, h), max(x - reach, 0) : min(x + reach + 1, w), :3].reshape(-1, 3).astype(int)
    if not len(box):
        return None
    ink = 765 - box.sum(axis=1) + 2 * (box.max(axis=1) - box.min(axis=1))
    i = int(np.argmax(ink))
    return hex_of(box[i]) if ink[i] > 40 else None


def palette(img: np.ndarray, region: Sequence[float] | None = None, n: int = 8) -> list[dict[str, Any]]:
    """The figure's own colours, commonest first: what is ink, not paper, quantised to steps of
    32 and averaged within each step; black and greys are one colour each way they are drawn.
    `{colour, share}`."""
    x0, y0, x1, y1 = region_box(img, region)
    sub = img[y0:y1, x0:x1, :3].reshape(-1, 3)
    ink = sub[(sub < charts.BACKGROUND).any(axis=1)]
    if not len(ink):
        return []
    q = (ink // 32).astype(np.int32)
    keys = q[:, 0] * 64 + q[:, 1] * 8 + q[:, 2]
    counts = np.bincount(keys, minlength=512)
    order = np.argsort(-counts)
    out: list[dict[str, Any]] = []
    total = float(len(ink))
    for key in order:
        if counts[key] == 0 or len(out) >= n:
            break
        if counts[key] < 0.003 * total:
            break
        c = tuple(int(v) for v in ink[keys == key].mean(axis=0))
        if any(math.dist(c, rgb_of(o["colour"])) < 40 for o in out):
            continue
        out.append({"colour": hex_of(c), "share": round(counts[key] / total, 4)})
    return out


# -- the axes, offered from the figure's own numbers -----------------------------------------------------------


def auto_axes(img: np.ndarray, words: list[Word] | None, ocr: charts.Ocr | None = None, near: Sequence[float] | None = None) -> dict[str, Any]:
    """A calibration offered from the frame and the numbers beside its axes: `{found, confident,
    calibration, frame: [x0, y0, x1, y1], plot_region, x: {...}, y: {...}, reason}`. `near` (a
    pixel) picks the frame that holds it when the figure has several; else the largest. Each
    axis gives its two ticks farthest apart from a scale that holds; an axis whose numbers do
    not hold gives none, and the offer is partial. Confident only when both hold within a
    quarter of the reader's limit on four ticks or more."""
    import cv2

    img = np.ascontiguousarray(img[..., :3])
    fg = charts.foreground(img)
    found = charts.frames(fg)
    if not found:
        # a scan: speckle and blur fray the axes' edges into paper; what is clearly ink, after a
        # median filter, keeps them straight
        fg = (cv2.medianBlur(img, 3) < SCAN_INK).any(axis=2)
        found = charts.frames(fg)
    out: dict[str, Any] = {"found": False, "confident": False, "calibration": None, "frames": [], "reason": None,
                           "x": {"axis": None, "ticks": 0, "residual": None, "log": False}, "y": {"axis": None, "ticks": 0, "residual": None, "log": False}}
    if not found:
        out["reason"] = "no frame: no y axis meeting an x axis at a corner"
        return out
    boxes = [[int(v.mid), int(v.a0), int(h.a1), int(h.mid)] for v, h in found]
    out["frames"] = boxes
    if near is not None:
        holding = [i for i, b in enumerate(boxes) if b[0] - 10 <= near[0] <= b[2] + 10 and b[1] - 10 <= near[1] <= b[3] + 10]
        pick = holding[0] if holding else min(range(len(boxes)), key=lambda i: math.dist(near, ((boxes[i][0] + boxes[i][2]) / 2, (boxes[i][1] + boxes[i][3]) / 2)))
    else:
        pick = max(range(len(found)), key=lambda i: found[i][0].length * found[i][1].length)
    yaxis, xaxis = found[pick]
    out["frame"] = boxes[pick]
    out["plot_region"] = [int(yaxis.b1 + 2), int(yaxis.a0), int(xaxis.a1), int(xaxis.b0 - 1)]
    used_ocr = False
    reader = ocr
    if words is None:
        reader = ocr or charts.ocr_words
        words = charts._ocr_around(img, reader, yaxis, xaxis)
        used_ocr = True
    ys, yticks, why = charts.y_scale(fg, words, yaxis, xaxis)
    if ys is None and reader is not None:
        strip = charts._strip_words(img, reader, yaxis, xaxis)
        if strip:
            ys2, yt2, why2 = charts.y_scale(fg, strip + words, yaxis, xaxis)
            if ys2 is not None:
                ys, yticks, why = ys2, yt2, why2
    xs, xticks = charts.x_scale(fg, words, yaxis, xaxis)
    if xs is None:
        xs, xticks = _x_powers(fg, words, yaxis, xaxis)
    out["words"] = "ocr" if used_ocr else "text"
    out["_words"] = words
    out["y"] = _axis_offer(ys, vertical=True, at=yaxis.mid)
    out["x"] = _axis_offer(xs, vertical=False, at=xaxis.mid)
    if ys is None:
        out["y"]["reason"] = why
    if xs is None:
        out["x"]["reason"] = "no numbers under the x axis hold a scale"
    y_title = charts.clean_title(charts._y_title(words, yticks, yaxis, img, reader)) if ys is not None else ""
    out["y"]["label"] = y_title or None
    out["y"]["unit"] = charts.unit_of(y_title) if y_title else None
    if xticks:
        below = [w for w in words if w.y0 > max(w2.y1 for w2 in xticks) and w.y0 <= xaxis.mid + 0.4 * (xaxis.mid - yaxis.a0) and yaxis.mid <= w.cx <= xaxis.a1]
        label = " ".join(w.text.strip() for w in sorted(below, key=lambda w: w.x0))
        out["x"]["label"] = label or None
        out["x"]["unit"] = charts.unit_of(label) if label else None
    if out["x"].get("axis") and out["y"].get("axis"):
        cal = {"x": out["x"]["axis"], "y": out["y"]["axis"]}
        try:
            Calibration.from_dict(cal)
            out["calibration"] = cal
            out["found"] = True
            out["confident"] = all(out[a]["ticks"] >= 4 and out[a]["residual"] <= charts.MAX_RESIDUAL / 4 for a in ("x", "y"))
        except ValueError as e:
            out["reason"] = str(e)
    else:
        missing = [a for a in ("x", "y") if not out[a].get("axis")]
        out["reason"] = f"the {' and '.join(missing)} axis could not be read: set {'its' if len(missing) == 1 else 'their'} two points by hand"
    return out


def _x_powers(fg: np.ndarray, words: list[Word], yaxis: charts.Line, xaxis: charts.Line) -> tuple[charts.Scale | None, list[Word]]:
    """A log x axis labelled in powers of ten — `10⁻²`, `10^3` — which `charts.x_scale` reads
    only as numbers. A plain `100` among them is `10⁰` with its superscript run into the base."""
    y = xaxis.mid
    height = yaxis.a1 - yaxis.a0
    under = [w for w in words if y - 3 <= w.y0 <= y + max(0.25 * height, 40) and xaxis.a0 - 2 * w.w <= w.cx <= xaxis.a1 + 2 * w.w]
    pows = [(w, charts.power(w.text)) for w in under]
    got = [(w, 10.0 ** p) for w, p in pows if p is not None]
    if len(got) < 2:
        return None, []
    got += [(w, 10.0 ** int(w.text.strip()[2])) for w, p in pows if p is None and re.fullmatch(r"10\d", w.text.strip())]
    if len(got) < charts.MIN_TICKS:
        return None, []
    top = min(w.y0 for w, _ in got)
    got = [(w, v) for w, v in got if w.y0 <= top + max(w.h for w, _ in got)]
    marks = charts._ticks_marks(fg, xaxis, horizontal_axis=True, reach=10)
    pts = [(charts._snap(w.cx, marks, w.w * 0.6 + 2), v) for w, v in got]
    s = charts.calibrate(pts, rising=1)
    return s, [w for w, _ in got] if s else []


def _axis_offer(s: charts.Scale | None, *, vertical: bool, at: float) -> dict[str, Any]:
    if s is None:
        return {"axis": None, "ticks": 0, "residual": None, "log": False}
    pts = sorted(s.ticks)
    (pa, va), (pb, vb) = pts[0], pts[-1]
    pa, pb, at = pa + 0.5, pb + 0.5, at + 0.5  # pixel indices to the middles of those pixels, as `extract` gives them
    if vertical:
        p1, p2 = (at, pa), (at, pb)
    else:
        p1, p2 = (pa, at), (pb, at)
    return {"axis": {"p1": [round(p1[0], 2), round(p1[1], 2)], "v1": va, "p2": [round(p2[0], 2), round(p2[1], 2)], "v2": vb, "log": s.log},
            "ticks": len(s.ticks), "residual": round(s.residual, 5), "log": s.log, "values": [v for _, v in pts]}


# -- the image ---------------------------------------------------------------------------------------------------


def _fit_side(img: np.ndarray, words: list[Word] | None, scale: float) -> tuple[np.ndarray, list[Word] | None, float]:
    import cv2

    h, w = img.shape[:2]
    f = min(1.0, MAX_SIDE / max(h, w))
    if f >= 1.0:
        return img, words, scale
    small = cv2.resize(img, (int(round(w * f)), int(round(h * f))), interpolation=cv2.INTER_AREA)
    ws = [Word(x.x0 * f, x.y0 * f, x.x1 * f, x.y1 * f, x.text, x.score) for x in words] if words is not None else None
    return small, ws, scale * f


def figure_source(conn: sqlite3.Connection, figure: str) -> dict[str, Any]:
    """Where a figure's pixels are: `{paper, node, format, file, page, bbox, label, how}`; `how`
    is `pdf` (the paper's own PDF, the node's box), `beside` (the PDF kept beside an XML paper,
    the page that prints its caption) or None with `reason`."""
    from . import figures

    n = conn.execute("SELECT node_id, paper, type, page, bbox_l, bbox_t, bbox_r, bbox_b FROM nodes WHERE node_id = ?", (figure,)).fetchone()
    if n is None:
        raise ValueError(f"no figure {figure!r} in this library")
    if n["type"] not in ("picture", "chart"):
        raise ValueError(f"{figure!r} is a {n['type']}, not a figure")
    p = conn.execute("SELECT key, format, file, figures_file, title, doi FROM papers WHERE key = ?", (n["paper"],)).fetchone()
    label, _ = figures.figure_labels(conn, n["paper"]).get(figure, (None, False))
    out: dict[str, Any] = {"paper": n["paper"], "node": figure, "format": p["format"], "title": p["title"], "doi": p["doi"], "label": label, "how": None, "reason": None, "file": None, "page": None, "bbox": None}
    if p["format"] == "pdf" and p["file"] and n["page"] is not None and n["bbox_l"] is not None:
        out.update(how="pdf", file=p["file"], page=n["page"], bbox=[n["bbox_l"], min(n["bbox_t"], n["bbox_b"]), n["bbox_r"], max(n["bbox_t"], n["bbox_b"])])
    elif p["figures_file"]:
        if label is None:
            out["reason"] = "its caption gives no number to find it by in the PDF kept beside the XML"
        else:
            out.update(how="beside", file=p["figures_file"])
    elif p["format"] == "pdf":
        out["reason"] = "the reading gave this picture no place on a page"
    else:
        out["reason"] = "an XML paper draws no figure: no PDF of it is kept beside the XML (the Papers tab asks PMC for one)"
    return out


def _beside_region(pdf: Any, label: str, node: str) -> tuple[int, tuple[float, float, float, float]] | None:
    """The page of the PDF kept beside an XML paper that prints the figure's caption, and the
    figure's region on it: the images its caption claims that stand above it; else, the figure
    drawn as vectors, the plot frames found above the caption (up to the caption before it) with
    a margin for their numbers and titles; failing both, the band above the caption."""
    from . import figures

    for pno, page, words, caps in figures.caption_pages(pdf, {label: node}):
        w, h = page.get_size()
        mine = [c for n, c in caps if n == label]
        if not mine:
            page.close()
            continue
        cap = mine[0]
        boxes = [list(box) for _, box in figures._images(page, w, h)
                 if box[3] <= cap.y0 + 6 and figures.claimed(list(box), caps, {label: node}) == label]  # above it: an image beside the caption is a logo
        above = [c.y1 for n, c in caps if c.y1 <= cap.y0 - 2]
        top = max(above) + 2 if above else 0.03 * h
        bottom = max(cap.y0 - 1, top + 10)
        if not boxes:
            scale = 150 / 72
            band = np.asarray(page.render(scale=scale, crop=(0, h - bottom, 0, top)).to_pil().convert("RGB"))
            for v, hz in charts.frames(charts.foreground(band)):
                x0, y0, x1, y1 = v.mid / scale, top + v.a0 / scale, hz.a1 / scale, top + hz.mid / scale
                boxes.append([x0 - 45, y0 - 18, x1 + 12, y1 + 32])  # the numbers left and under, a title over it
        page.close()
        if boxes:
            return pno + 1, (max(min(b[0] for b in boxes), 0.0), max(min(b[1] for b in boxes), top), min(max(b[2] for b in boxes), w), min(max(b[3] for b in boxes), bottom))
        return pno + 1, (0.04 * w, top, 0.96 * w, bottom)
    return None


def _cut(lib_dir: Path, conn: sqlite3.Connection, figure: str) -> tuple[dict[str, Any], np.ndarray | None, list[Word] | None, float]:
    """The figure cut from its page: `(source, pixels, words, scale)`, the pixels None with
    `source.reason` when nothing here draws it."""
    import pypdfium2 as pdfium

    from . import figures

    src = figure_source(conn, figure)
    if src["how"] is None:
        return src, None, None, 1.0
    path = lib_dir / "papers" / src["file"]
    if not path.exists():
        src["reason"] = f"{src['file']} is not on disk"
        return src, None, None, 1.0
    pdf = pdfium.PdfDocument(str(path))
    try:
        if src["how"] == "beside":
            got = _beside_region(pdf, src["label"], figure)
            if got is None:
                src["reason"] = f"no page of {src['file']} prints the caption of figure {src['label']}"
                return src, None, None, 1.0
            src["page"], box = got
            src["bbox"] = [round(v, 2) for v in box]
        img, words, kind, scale = figures.crop(pdf, src["page"], tuple(src["bbox"]))
    finally:
        pdf.close()
    img, words, scale = _fit_side(np.ascontiguousarray(img[..., :3]), words, scale)
    src["drawn"] = kind
    return src, img, words, scale


def render(lib_dir: Path, conn: sqlite3.Connection, figure: str, *, ocr: charts.Ocr | None = None, offer: bool = True) -> dict[str, Any]:
    """A figure's pixels as a PNG under `<library>/digitised/`, with the offer of its axes and its
    colours: `{source, image: {path, width, height, scale, sha256}, auto, palette}`."""
    src, img, words, scale = _cut(lib_dir, conn, figure)
    if img is None:
        return {"source": src, "image": None, "auto": None, "palette": []}
    return _finish(lib_dir, figure, img, words, scale, src, ocr, offer)


def picture_sha(lib_dir: Path, conn: sqlite3.Connection, figure: str) -> str | None:
    """The hash of the picture the figure is drawn as now — `render`'s `image.sha256`, nothing
    written — or None when nothing here draws it."""
    _, img, _, _ = _cut(lib_dir, conn, figure)
    return hashlib.sha256(_png(img)).hexdigest() if img is not None else None


def picture_path(lib_dir: Path, figure: str | None, file: str | None = None) -> Path:
    """Where the picture of a figure (or of an image file) is written when it is opened."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", figure or file or "image")[-150:]
    return lib_dir / DIRS / f"{name}.png"


def original_path(lib_dir: Path, sha: str) -> Path:
    """Where the copy of a picture that series were read from is kept, by its hash."""
    return lib_dir / DIRS / "originals" / f"{sha}.png"


def _png(img: np.ndarray) -> bytes:
    import cv2

    ok, png = cv2.imencode(".png", img[..., ::-1])
    if not ok:
        raise ValueError("the figure could not be encoded")
    return png.tobytes()


def open_image(path: Path, *, ocr: charts.Ocr | None = None, offer: bool = True, lib_dir: Path | None = None) -> dict[str, Any]:
    """An image file opened to digitise: as `render`, its words read by OCR. Its PNG is kept
    under the library's `digitised/`, or with no library under the machine's temporary folder."""
    import tempfile

    import cv2

    raw = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if raw is None:
        raise ValueError(f"cannot read an image from {path}")
    img = np.ascontiguousarray(raw[..., ::-1])
    img, _, scale = _fit_side(img, None, 1.0)
    src = {"paper": None, "node": None, "file": str(path), "how": "image", "page": None, "bbox": None, "label": None, "title": path.name, "doi": None, "drawn": "raster"}
    return _finish(lib_dir or Path(tempfile.gettempdir()) / "litrag", None, img, None, scale, src, ocr, offer)


def _finish(lib_dir: Path, figure: str | None, img: np.ndarray, words: list[Word] | None, scale: float, src: dict[str, Any], ocr: charts.Ocr | None, offer: bool) -> dict[str, Any]:
    data = _png(img)
    sha = hashlib.sha256(data).hexdigest()
    p = picture_path(lib_dir, figure, src.get("file"))
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists() or p.read_bytes() != data:
        p.write_bytes(data)
    image: dict[str, Any] = {"width": int(img.shape[1]), "height": int(img.shape[0]), "scale": round(scale, 5), "sha256": sha, "path": str(p)}
    auto = auto_axes(img, words, ocr=ocr) if offer else None
    if auto is not None:
        words = auto.pop("_words", None) or words
    region = auto.get("plot_region") if auto else None
    if words is not None:
        # the text layer is every word of the figure; OCR's, only those around the plot it read
        whole = src.get("drawn") == "vector"
        p.with_suffix(".words.json").write_text(json.dumps({"whole": whole, "words": [[round(w.x0, 1), round(w.y0, 1), round(w.x1, 1), round(w.y1, 1), w.text] for w in words]}), encoding="utf-8")
    return {"source": src, "image": image, "auto": auto, "palette": palette(img, region), "_img": img, "_words": words}


def load_words(image_path: str | Path, whole: bool = False) -> list[Word] | None:
    """The words saved beside a rendered image, in its pixels; None when none were — or, asked
    for the `whole` figure's, when they are only what OCR read around one plot."""
    p = Path(image_path).with_suffix(".words.json")
    if not p.exists():
        return None
    d = json.loads(p.read_text("utf-8"))
    if whole and not d.get("whole"):
        return None
    return [Word(a, b, c, e, t) for a, b, c, e, t in d.get("words", [])]


def load_image(path: str | Path) -> np.ndarray:
    import cv2

    raw = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if raw is None:
        raise ValueError(f"cannot read an image from {path}")
    return np.ascontiguousarray(raw[..., ::-1])


# -- the rows ----------------------------------------------------------------------------------------------------


def ensure_schema(conn: sqlite3.Connection) -> None:
    """The digitisations' tables, made on the first call that needs them. A store whose rows
    were kept before a series was bound to its picture is brought along: each row's picture is
    the image it was saved with, and a name is its figure's own among the series that have one."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('digitised', 'digitised_points', 'digitised_fits')")}
    if len(have) == 3:
        if "picture" not in {r[1] for r in conn.execute("PRAGMA table_info(digitised)")}:
            with conn:
                conn.execute("ALTER TABLE digitised ADD COLUMN picture TEXT NOT NULL DEFAULT ''")
                conn.execute("UPDATE digitised SET picture = COALESCE(json_extract(image, '$.sha256'), '')")
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS digitised_of_figure ON digitised(figure, series) WHERE orphan = 0")
        return
    conn.executescript(
        """
CREATE TABLE IF NOT EXISTS digitised (
  id INTEGER PRIMARY KEY,
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  figure TEXT NOT NULL,             -- the picture node the series is with — or, for one to review, the one it was with
  picture TEXT NOT NULL,            -- the sha256 of the picture that binds it to its figure: while the paper draws it, the series is that figure's
  figure_label TEXT,                -- "2" when the figure's caption numbers it: said to a person, never matched on
  page INTEGER, bbox TEXT,          -- where the image was cut: the page and [left, top, right, bottom] in points from the top
  series TEXT NOT NULL,             -- the name a person gave it
  colour TEXT,                      -- the colour it was found by, #rrggbb
  calibration TEXT NOT NULL,        -- JSON {x: {p1, v1, p2, v2, log}, y: {...}}: pixels of the saved image to values
  method TEXT NOT NULL,             -- JSON {reader, mode, tolerance, region, auto_axes}: how the points were found
  image TEXT NOT NULL,              -- JSON {width, height, scale, sha256}: the image the pixels are of, a copy kept as digitised/originals/<sha256>.png
  x_label TEXT, x_unit TEXT, y_label TEXT, y_unit TEXT,
  orphan INTEGER NOT NULL DEFAULT 0,-- 1 when the paper no longer draws its picture: no figure's, for a person to review
  at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS digitised_of_figure ON digitised(figure, series) WHERE orphan = 0;
CREATE TABLE IF NOT EXISTS digitised_points (
  digitised INTEGER NOT NULL REFERENCES digitised(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,         -- in order of x
  x REAL NOT NULL, y REAL NOT NULL, -- the calibration's reading of the pixel
  px REAL NOT NULL, py REAL NOT NULL,
  how TEXT NOT NULL,                -- auto | added | moved
  PRIMARY KEY(digitised, ordinal)
);
CREATE TABLE IF NOT EXISTS digitised_fits (
  digitised INTEGER NOT NULL REFERENCES digitised(id) ON DELETE CASCADE,
  model TEXT NOT NULL,              -- logistic | gompertz
  status TEXT NOT NULL,             -- fit | unfit
  result TEXT NOT NULL,             -- JSON: fit.fit's answer, parameters and derived quantities with their intervals
  plateau REAL, max_rate REAL, t_half REAL, lag REAL, r2 REAL,
  at TEXT NOT NULL,
  PRIMARY KEY(digitised, model)
);
"""
    )
    conn.commit()


def _points(cal: Calibration, pts: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for p in pts:
        px, py = float(p["px"]), float(p["py"])
        x, y = cal.value(px, py)
        how = p.get("how") or "auto"
        if how not in ("auto", "added", "moved"):
            raise ValueError(f"a point comes auto, added or moved, not {how!r}")
        out.append({"px": round(px, 2), "py": round(py, 2), "x": _r(x), "y": _r(y), "how": how})
    out.sort(key=lambda p: (p["x"], p["y"]))
    return out


IMAGE_KEYS = ("width", "height", "scale", "sha256")


def _image(image: dict[str, Any] | None) -> dict[str, Any]:
    """What is kept of an image: its size, its scale and its hash — never where it lay on a disk."""
    return {k: (image or {}).get(k) for k in IMAGE_KEYS}


def save(lib_dir: Path, conn: sqlite3.Connection, figure: str, series: str, calibration: dict[str, Any], points: Sequence[dict[str, Any]], *, image: dict[str, Any], colour: str | None = None,
         method: dict[str, Any] | None = None, axes: dict[str, Any] | None = None, source: dict[str, Any] | None = None, series_id: int | None = None) -> dict[str, Any]:
    """A series saved on a figure: every point's value read again from its pixel through
    `calibration`. A series is its id; its name is a label. With `series_id` the figure's series
    of that id is saved again — renamed, when the name is another; its points replaced, and with
    them its fits, when the points are others — and without one a new series is made. Either
    way the name must be the figure's only one: a second curve never takes the first's place
    by being called the same (one kept for review is no figure's, and in no name's way). `image` is the one the figure was opened as (`render`): the points are pixels
    of it, so it must be the picture of the figure on disk still, and a copy of it is kept by its
    hash, which binds the series to the figure. `source` is the one the figure was opened with:
    where the store knows no place for the figure (an XML paper's, found on the PDF kept beside it
    only when it is rendered), its page and box are the series' place."""
    from .library import now_iso

    ensure_schema(conn)
    name = (series or "").strip()
    if not name:
        raise ValueError("a series needs a name")
    cal = Calibration.from_dict(calibration)
    pts = _points(cal, points)
    src = figure_source(conn, figure)
    if source and src["page"] is None:
        src["page"], src["bbox"] = source.get("page"), source.get("bbox")
    shown = picture_path(lib_dir, figure)
    data = shown.read_bytes() if shown.is_file() else None
    sha = hashlib.sha256(data).hexdigest() if data is not None else None
    if sha is None or (image or {}).get("sha256") != sha:
        raise ValueError("these points are not of the figure's picture as it is now: open the figure again")
    kept = original_path(lib_dir, sha)
    if not kept.exists():
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(data)
    ax = axes or {}
    x, y = ax.get("x") or {}, ax.get("y") or {}
    with conn:
        if series_id is not None and conn.execute("SELECT 1 FROM digitised WHERE id = ? AND figure = ? AND orphan = 0", (series_id, figure)).fetchone() is None:
            raise ValueError(f"the figure has no series {series_id}")
        if conn.execute("SELECT 1 FROM digitised WHERE figure = ? AND series = ? AND orphan = 0 AND id IS NOT ?", (figure, name, series_id)).fetchone() is not None:
            raise ValueError(f"the figure has a series named {name!r} already: give this one a name of its own")
        row = (src["paper"], figure, sha, src.get("label"), src.get("page"), json.dumps(src.get("bbox")) if src.get("bbox") else None, name, colour,
               json.dumps(cal.to_dict()), json.dumps({"reader": METHOD, **(method or {})}), json.dumps(_image(image)),
               x.get("label"), x.get("unit"), y.get("label"), y.get("unit"), now_iso())
        if series_id is not None:
            did = series_id
            was = [tuple(r) for r in conn.execute("SELECT x, y, px, py, how FROM digitised_points WHERE digitised = ? ORDER BY ordinal", (did,))]
            if was != [(p["x"], p["y"], p["px"], p["py"], p["how"]) for p in pts]:
                conn.execute("DELETE FROM digitised_fits WHERE digitised = ?", (did,))  # the points changed: a fit of the old ones is no fit of these
            conn.execute("DELETE FROM digitised_points WHERE digitised = ?", (did,))
            conn.execute("UPDATE digitised SET paper=?, figure=?, picture=?, figure_label=?, page=?, bbox=?, series=?, colour=?, calibration=?, method=?, image=?, x_label=?, x_unit=?, y_label=?, y_unit=?, at=? WHERE id = ?",
                         (*row, did))
        else:
            cur = conn.execute("INSERT INTO digitised(paper, figure, picture, figure_label, page, bbox, series, colour, calibration, method, image, x_label, x_unit, y_label, y_unit, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            did = cur.lastrowid
        conn.executemany("INSERT INTO digitised_points(digitised, ordinal, x, y, px, py, how) VALUES (?,?,?,?,?,?,?)",
                         [(did, i, p["x"], p["y"], p["px"], p["py"], p["how"]) for i, p in enumerate(pts)])
    return one(conn, did)


def save_fit(conn: sqlite3.Connection, did: int, result: dict[str, Any]) -> None:
    from .library import now_iso

    ensure_schema(conn)
    d = result.get("derived") or {}

    def v(name: str) -> float | None:
        return (d.get(name) or {}).get("value")

    with conn:
        conn.execute("INSERT OR REPLACE INTO digitised_fits(digitised, model, status, result, plateau, max_rate, t_half, lag, r2, at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (did, result["model"], result["status"], json.dumps(result), v("plateau"), v("max_rate"), v("t_half"), v("lag"), result.get("r2"), now_iso()))


def one(conn: sqlite3.Connection, did: int) -> dict[str, Any]:
    ensure_schema(conn)
    r = conn.execute("SELECT * FROM digitised WHERE id = ?", (did,)).fetchone()
    if r is None:
        raise ValueError(f"no digitised series {did}")
    pts = [dict(p) for p in conn.execute("SELECT x, y, px, py, how FROM digitised_points WHERE digitised = ? ORDER BY ordinal", (did,))]
    fits = {f["model"]: json.loads(f["result"]) for f in conn.execute("SELECT model, result FROM digitised_fits WHERE digitised = ?", (did,))}
    return {"id": r["id"], "paper": r["paper"], "figure": r["figure"], "figure_label": r["figure_label"], "page": r["page"], "bbox": json.loads(r["bbox"]) if r["bbox"] else None,
            "series": r["series"], "colour": r["colour"], "calibration": json.loads(r["calibration"]), "method": json.loads(r["method"]), "image": json.loads(r["image"]),
            "axes": {"x": {"label": r["x_label"], "unit": r["x_unit"], "log": json.loads(r["calibration"])["x"].get("log", False)},
                     "y": {"label": r["y_label"], "unit": r["y_unit"], "log": json.loads(r["calibration"])["y"].get("log", False)}},
            "orphan": bool(r["orphan"]), "at": r["at"], "points": pts, "fits": fits}


def of_figure(conn: sqlite3.Connection, figure: str) -> list[dict[str, Any]]:
    """The figure's series. One kept for review is no figure's: the id it kept may be another picture's now."""
    ensure_schema(conn)
    return [one(conn, r[0]) for r in conn.execute("SELECT id FROM digitised WHERE figure = ? AND orphan = 0 ORDER BY id", (figure,))]


def orphans(conn: sqlite3.Connection, paper: str) -> list[dict[str, Any]]:
    """The paper's series to review — the paper no longer draws the picture each was read from —
    each with the figure it was with (`figure`, `figure_label`)."""
    ensure_schema(conn)
    return [one(conn, r[0]) for r in conn.execute("SELECT id FROM digitised WHERE paper = ? AND orphan = 1 ORDER BY id", (paper,))]


def delete(conn: sqlite3.Connection, did: int) -> bool:
    ensure_schema(conn)
    with conn:
        return conn.execute("DELETE FROM digitised WHERE id = ?", (did,)).rowcount > 0


def attach(lib_dir: Path, conn: sqlite3.Connection, did: int, figure: str) -> dict[str, Any]:
    """A person puts a series on a figure of its paper — one kept for review, its picture and the
    figure's before them: it is that figure's from now on, bound to the picture the figure was
    opened as. What was saved is not touched: its points and calibration stay pixels of the
    picture it was read from."""
    s = one(conn, did)
    src = figure_source(conn, figure)
    if src["paper"] != s["paper"]:
        raise ValueError("a series is put on a figure of its own paper")
    shown = picture_path(lib_dir, figure)
    if not shown.is_file():
        raise ValueError("open the figure before putting a series on it")
    if conn.execute("SELECT 1 FROM digitised WHERE figure = ? AND series = ? AND orphan = 0 AND id != ?", (figure, s["series"], did)).fetchone() is not None:
        raise ValueError(f"the figure has a series named {s['series']!r} already: delete one of the two first")
    with conn:
        conn.execute("UPDATE digitised SET figure = ?, picture = ?, figure_label = ?, orphan = 0 WHERE id = ?", (figure, hashlib.sha256(shown.read_bytes()).hexdigest(), src.get("label"), did))
    return one(conn, did)


def reattach(lib_dir: Path, conn: sqlite3.Connection, key: str) -> int:
    """After the paper's nodes were replaced: each digitised series is with the figure that is
    drawn as its picture now — the one picture of that hash, or its own figure among several —
    whatever id that figure has in this reading; one whose picture the paper no longer draws is
    kept, for a person to review. No caption's number and no place on a page moves a series: two
    figures can share either. A series is kept for review too when one of its name is already
    where it would go. How many this reading left without a figure."""
    ensure_schema(conn)
    rows = conn.execute("SELECT id, figure, picture, series, orphan FROM digitised WHERE paper = ? ORDER BY orphan, id", (key,)).fetchall()
    if not rows:
        return 0
    drawn: dict[str, list[str]] = {}
    for (node,) in conn.execute("SELECT node_id FROM nodes WHERE paper = ? AND type IN ('picture', 'chart') ORDER BY rowid", (key,)).fetchall():
        try:
            sha = picture_sha(lib_dir, conn, node)
        except Exception:  # noqa: BLE001 — a picture that cannot be cut is no series' picture; never a reason a rebuild fails
            sha = None
        if sha is not None:
            drawn.setdefault(sha, []).append(node)
    to: dict[int, str] = {}
    taken: set[tuple[str, str]] = set()
    for r in rows:
        nodes = drawn.get(r["picture"], [])
        now = nodes[0] if len(nodes) == 1 else r["figure"] if r["figure"] in nodes else None
        if now is not None and (now, r["series"]) not in taken:
            taken.add((now, r["series"]))
            to[r["id"]] = now
    with conn:
        conn.execute("UPDATE digitised SET orphan = 1 WHERE paper = ?", (key,))  # all out of the way, so a figure's id another series leaves is free when it is moved into
        for did, now in to.items():
            conn.execute("UPDATE digitised SET figure = ?, orphan = 0 WHERE id = ?", (now, did))
    return sum(not r["orphan"] and r["id"] not in to for r in rows)


def carry(conn: sqlite3.Connection, stub: str, key: str) -> int:
    """Before the paper `stub` is deleted for being `key` filed twice: its digitised series go to
    `key`, kept for review until a reading of `key` draws their pictures (`reattach`) or a person
    puts them on a figure — a person's work does not go with a stub's rows. How many. Not committed."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'digitised'").fetchone() is None:
        return 0
    return conn.execute("UPDATE digitised SET paper = ?, orphan = 1 WHERE paper = ?", (key, stub)).rowcount


def follow(lib_dir: Path, conn: sqlite3.Connection, key: str) -> list[tuple[str, str]]:
    """Once a paper's nodes are replaced, whatever replaced them: its plots follow their figures
    or are removed (`figures.remap`), its digitised series stay with the figures drawn as their
    pictures or are kept for review (`reattach`). What there is to say of it, each an `events`
    row: `[(logger, message)]`."""
    from . import figures
    from .library import now_iso
    from .store import log_event

    said = []
    removed = figures.removed_note(figures.remap(conn, key))
    if removed:
        said.append(("figures", removed))
    n = reattach(lib_dir, conn, key)
    if n:
        said.append(("digitise", f"{n} digitised series {'are' if n != 1 else 'is'} of a picture this reading no longer draws: kept, for review in the Plots tab"))
    for logger, message in said:
        log_event(conn, key, now_iso(), logger, message)
    return said


# -- the export --------------------------------------------------------------------------------------------------


def _head(label: str | None, unit: str | None, fallback: str) -> str:
    name = fallback
    if label:
        stripped = re.sub(r"\s*\([^()]*\)\s*$", "", label).strip()
        name = stripped or fallback
    return f"{name} ({unit})" if unit else name


def csv_text(series: Sequence[dict[str, Any]]) -> str:
    """The series of one set of axes as CSV. One series: two columns, `x`, `y`, each headed by
    its axis's name and unit when the axis label gave them. Several: a `series` column first,
    then the same."""
    if not series:
        return ""
    ax = series[0]["axes"]
    hx, hy = _head(ax["x"].get("label"), ax["x"].get("unit"), "x"), _head(ax["y"].get("label"), ax["y"].get("unit"), "y")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    if len(series) == 1:
        w.writerow([hx, hy])
        for p in series[0]["points"]:
            w.writerow([_fmt(p["x"]), _fmt(p["y"])])
    else:
        w.writerow(["series", hx, hy])
        for s in series:
            for p in s["points"]:
                w.writerow([s["series"], _fmt(p["x"]), _fmt(p["y"])])
    return buf.getvalue()


def sidecar(series: Sequence[dict[str, Any]], *, library: str | None, paper: dict[str, Any] | None, csv_name: str, fit_names: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """What traces each value in the CSV to its source: the paper and the figure, and for each
    series — a figure's series need not be pixels of one picture — where it was cut, the image,
    the calibration, the method and each point's pixel; `fit_csv` each series' fit files, by model."""
    from .library import now_iso

    first = series[0]
    return {
        "format": "litrag-digitised/1",
        "csv": csv_name,
        "exported_at": now_iso(),
        "library": library,
        "paper": paper,
        "figure": {"node": first["figure"]},
        "axes": first["axes"],
        "series": [{
            "name": s["series"], "colour": s["colour"], "figure_label": s.get("figure_label"), "page": s.get("page"), "bbox_pt": s.get("bbox"), "image": s["image"],
            "calibration": s["calibration"], "method": s["method"], "saved_at": s["at"],
            "points": [{"x": p["x"], "y": p["y"], "px": p["px"], "py": p["py"], "how": p["how"]} for p in s["points"]],
            "fits": {m: {k: v for k, v in f.items() if k not in ("residuals", "curve")} for m, f in (s.get("fits") or {}).items()},
            "fit_csv": (fit_names or {}).get(s["series"], {}),
        } for s in series],
    }


def unsaved(item: dict[str, Any], source: dict[str, Any] | None = None) -> dict[str, Any]:
    """A series not kept in a store — read from an image file — in the shape `one` gives a
    saved one, every value read again from its pixel. Of the file, its name: not where it lay."""
    from .library import now_iso

    cal = Calibration.from_dict(item["calibration"])
    ax = item.get("axes") or {}
    src = source or {}
    return {"id": None, "paper": None, "figure": None, "figure_label": None, "page": None, "bbox": None, "file": Path(str(src["file"])).name if src.get("file") else None,
            "series": str(item.get("series") or "series"), "colour": item.get("colour"), "calibration": cal.to_dict(),
            "method": {"reader": METHOD, **(item.get("method") or {})}, "image": _image(item.get("image")),
            "axes": {"x": {"label": (ax.get("x") or {}).get("label"), "unit": (ax.get("x") or {}).get("unit"), "log": cal.x.log},
                     "y": {"label": (ax.get("y") or {}).get("label"), "unit": (ax.get("y") or {}).get("unit"), "log": cal.y.log}},
            "orphan": False, "at": now_iso(), "points": _points(cal, item.get("points") or []), "fits": item.get("fits") or {}}


def export(conn: sqlite3.Connection | None, ids: Sequence[int], path: Path, *, library: str | None = None, inline: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    """Series written out — saved ones by their ids, or `inline` ones from an image file
    (`unsaved`) — `<path>` the CSV, `<path>.json` beside it the sidecar, and for each series
    with a fit `<stem>.<series>.<model>.fit.csv` its parameters. Series whose axes differ (a
    label, a unit, a log scale: panels of one figure) are not written under one header: each
    set of axes gets files of its own, `<stem>.1.csv`, `<stem>.2.csv`, … in the order given. An
    orphan, whose figure is not known, is left out. `{files: [{csv, sidecar, fits, points}], points}`."""
    from . import fit as fitting

    series = [s for s in (one(conn, int(i)) for i in ids) if not s["orphan"]] if conn is not None else []
    series += list(inline)
    if not series:
        raise ValueError("no series to export")
    figs = {s["figure"] for s in series}
    if len(figs) > 1:
        raise ValueError("one figure's series at a time")
    path = Path(path)
    if path.suffix.lower() != ".csv":
        path = path.with_suffix(".csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    p = conn.execute("SELECT key, doi, pmid, pmcid, title, year, journal FROM papers WHERE key = ?", (series[0]["paper"],)).fetchone() if conn is not None and series[0]["paper"] else None
    paper = dict(p) if p else ({"key": series[0]["paper"]} if series[0]["paper"] else None)
    by_axes: dict[str, list[dict[str, Any]]] = {}
    for s in series:
        by_axes.setdefault(json.dumps(s["axes"], sort_keys=True), []).append(s)
    files = []
    for i, group in enumerate(by_axes.values(), 1):
        out = path if len(by_axes) == 1 else path.with_name(f"{path.stem}.{i}.csv")
        out.write_text(csv_text(group), encoding="utf-8")
        fit_names: dict[str, dict[str, str]] = {}
        safe_names: set[str] = set()
        written = []
        for s in group:
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", s["series"])
            while safe in safe_names:  # two names alike once made safe for a file: each still its own
                safe += "_"
            safe_names.add(safe)
            for model, f in (s.get("fits") or {}).items():
                if f.get("status") != "fit":
                    continue
                fp = out.with_name(f"{out.stem}.{safe}.{model}.fit.csv")
                fp.write_text(fitting.params_csv(f, s["axes"]["x"].get("unit"), s["axes"]["y"].get("unit")), encoding="utf-8")
                fit_names.setdefault(s["series"], {})[model] = fp.name
                written.append(str(fp))
        meta = sidecar(group, library=library, paper=paper, csv_name=out.name, fit_names=fit_names)
        if group[0].get("file"):
            meta["figure"]["file"] = group[0]["file"]
        side = out.with_suffix(".csv.json")
        side.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        files.append({"csv": str(out), "sidecar": str(side), "fits": written, "points": sum(len(s["points"]) for s in group)})
    return {"files": files, "points": sum(f["points"] for f in files)}


# -- finding plots ------------------------------------------------------------------------------------------------

_TREND = re.compile(r"\b(time|kinetic|kinetics|curve|curves|turbidity|absorbance|optical density|OD\s?\d{3}|as a function of|versus|vs\.?|over\s+\d+\s*(?:h|min|days?)|growth|gelation|fibril(?:logenesis| formation)|assembly|polymeri[sz]ation|titration|release|profile|course)\b", re.I)
_NOT_PLOT = re.compile(r"\b(micrograph|SEM|TEM|AFM image|images? of|photograph|schematic|illustration|stained|staining|SDS-PAGE|western blot|gel electrophoresis|microscopy)\b", re.I)


def _fts_query(q: str) -> str | None:
    toks = re.findall(r"[A-Za-z0-9µ]+", q)
    if not toks:
        return None
    return " ".join(f'"{t}"*' for t in toks)


def find(conn: sqlite3.Connection, query: str = "", *, trend: bool = False, limit: int = 50) -> dict[str, Any]:
    """Figures that are data plots, best first, whose captions match `query` (every word, as a
    prefix). A figure is a **plot** when the chart reader found a frame with numbers beside its
    axes in it (`charts`); **no plot** when it looked and found none; else **unknown** — not
    read yet — and its caption's words say how likely it is one. A **trend** is a plot whose x
    axis has numbers (a curve over time or dose, not bars over categories), or an unread
    figure whose caption speaks of one. `{figures: [{figure, paper, title, year, label,
    caption, plot, trend, plots: [...], digitised, image, score}], total}`."""
    from . import figures

    figures.ensure_schema(conn)
    ensure_schema(conn)
    fq = _fts_query(query)
    if fq:
        rows = conn.execute(
            "SELECT c.parent AS figure, bm25(nodes_fts) AS rank FROM nodes_fts JOIN nodes c ON c.rowid = nodes_fts.rowid "
            "WHERE nodes_fts MATCH ? AND c.type = 'caption' ORDER BY rank LIMIT 2000", (fq,)).fetchall()
        ranked: dict[str, float] = {}
        for r in rows:
            ranked.setdefault(r["figure"], float(r["rank"]))
        figs = list(ranked)
        # the figures as a query of their own, not a list of ids: a library has more than a statement binds
        among, args = "(SELECT c.parent FROM nodes_fts JOIN nodes c ON c.rowid = nodes_fts.rowid WHERE nodes_fts MATCH ? AND c.type = 'caption' ORDER BY bm25(nodes_fts) LIMIT 2000)", (fq,)
    else:
        ranked = {}
        figs = [r[0] for r in conn.execute("SELECT node_id FROM nodes WHERE type IN ('picture', 'chart') ORDER BY rowid")]
        among, args = "(SELECT node_id FROM nodes WHERE type IN ('picture', 'chart'))", ()
    if not figs:
        return {"figures": [], "total": 0, "query": query}
    out = []
    info = {r["node_id"]: r for r in conn.execute(
        f"SELECT n.node_id, n.paper, n.type, n.page, n.bbox_l, p.title, p.year, p.format, p.file, p.figures_file, "
        f"(SELECT group_concat(c.text, ' ') FROM nodes c WHERE c.parent = n.node_id AND c.type = 'caption') AS caption "
        f"FROM nodes n JOIN papers p ON p.key = n.paper WHERE n.node_id IN {among} AND n.type IN ('picture', 'chart')", args)}
    plots: dict[str, list[dict[str, Any]]] = {}
    for r in conn.execute(f"SELECT figure, plot, kind, status, reason, x_scale, y_scale, y_label, x_label, panel FROM charts WHERE figure IN {among} ORDER BY plot", args):
        plots.setdefault(r["figure"], []).append(dict(r))
    told = {r["figure"]: r["outcome"] for r in conn.execute(f"SELECT figure, outcome FROM figure_outcomes WHERE figure IN {among}", args)}
    dig: dict[str, int] = {}
    for r in conn.execute(f"SELECT figure, COUNT(*) AS n FROM digitised WHERE figure IN {among} AND orphan = 0 GROUP BY figure", args):
        dig[r["figure"]] = r["n"]
    labels: dict[str, dict[str, tuple[str | None, bool]]] = {}
    for f in figs:
        r = info.get(f)
        if r is None:
            continue
        cap = r["caption"] or ""
        ps = plots.get(f, [])
        if ps:
            plot = "plot"
        elif told.get(f) in ("no-plot", "no-caption"):
            plot = "no plot"
        else:
            plot = "unknown"
        numeric_x = any(p["x_scale"] for p in ps) or any(p["kind"] == "point" for p in ps)
        cue = bool(_TREND.search(cap))
        not_plot = bool(_NOT_PLOT.search(cap)) and not cue
        trend_ = numeric_x or (plot == "unknown" and cue) or (plot == "plot" and cue and not any(p["kind"] == "bar" for p in ps))
        if trend and not trend_:
            continue
        if r["paper"] not in labels:
            labels[r["paper"]] = figures.figure_labels(conn, r["paper"])
        label = labels[r["paper"]].get(f, (None, False))[0]
        has_image = (r["format"] == "pdf" and r["page"] is not None and r["bbox_l"] is not None) or (bool(r["figures_file"]) and label is not None)
        score = -ranked.get(f, 0.0)  # bm25: lower is better
        score += {"plot": 3.0, "unknown": 0.0, "no plot": -3.0}[plot] + (2.0 if trend_ else 0.0) + (1.0 if cue else 0.0) - (2.0 if not_plot else 0.0) + (0.5 if has_image else -1.0)
        out.append({"figure": f, "paper": r["paper"], "title": r["title"], "year": r["year"], "label": label, "caption": cap[:600], "plot": plot, "trend": trend_,
                    "plots": ps, "digitised": dig.get(f, 0), "image": has_image, "format": r["format"], "page": r["page"], "score": round(score, 3)})
    out.sort(key=lambda x: -x["score"])
    return {"figures": out[:limit], "total": len(out), "query": query}


# -- the worker's ops ---------------------------------------------------------------------------------------------


def _image_path(path: str) -> Path:
    """An image a request names: one this module rendered under the library, or a file a person chose."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"no image at {path}")
    return p


def answer(op: str, req: dict[str, Any], get_lib: Any) -> dict[str, Any]:
    """The worker's digitiser ops, each answered with one event (`worker.py` emits it):

    - `plots` {lib, query, trend, limit} → `plots` {figures, total, query}: `find`.
    - `digitise_open` {lib, figure} | {path} → `digitise` {source, image, auto, palette, saved,
      orphans}: the figure rendered (or the image opened), the axes and colours offered, its
      saved series, and its paper's series to review, each with the `picture` it was read from.
    - `digitise_axes` {image, near} → `axes` {auto}: the axes offered again, of the plot that
      holds the pixel `near` (a panel of a figure of several).
    - `digitise_extract` {image, colour, tolerance, region, mode, calibration} → `extracted`
      {mode, colour, points, ...}: a series found by its colour in the rendered image.
    - `digitise_save` {lib, figure, series, calibration, points, colour, method, image, axes, source,
      series_id} → `digitised` {series}: the figure's series of that id saved again, or with no
      id a new one; `series` its name, a label; `source` the open's, for its place.
    - `digitise_delete` {lib, series_id} → `digitised` {deleted}.
    - `digitise_attach` {lib, series_id, figure} → `digitised` {series}: a series put on a figure
      of its paper by a person (`attach`).
    - `digitise_export` {lib, ids, path} | {series, source, path} → `exported` {files: [{csv,
      sidecar, fits, points}], points}: saved series by id, or unsaved ones (an image file's)
      given whole; one set of files for each set of axes.
    - `fit` {points, model} | {lib, series_id, model} → `fit` {fits, preferred}: both models unless
      one is named; a saved series' fits are kept with it.
    """
    from . import fit as fitting
    from .store import open_store

    if op == "plots":
        lib = get_lib()
        conn = open_store(lib.store_path)
        try:
            out = find(conn, str(req.get("query") or ""), trend=bool(req.get("trend")), limit=int(req.get("limit") or 50))
        finally:
            conn.close()
        return {"event": "plots", "lib": lib.id, **out}
    if op == "digitise_open":
        if req.get("path") and not req.get("figure"):
            lib = get_lib() if req.get("lib") else None
            got = open_image(Path(str(req["path"])), lib_dir=lib.dir if lib else None)
            got.pop("_img", None)
            got.pop("_words", None)
            return {"event": "digitise", "lib": lib.id if lib else None, **got, "saved": [], "orphans": []}
        lib = get_lib()
        conn = open_store(lib.store_path)
        try:
            got = render(lib.dir, conn, str(req["figure"]))
            saved = of_figure(conn, str(req["figure"]))
            lost = orphans(conn, got["source"]["paper"])
            for s in lost:
                kept = original_path(lib.dir, s["image"]["sha256"])
                s["picture"] = str(kept) if kept.is_file() else None
        finally:
            conn.close()
        got.pop("_img", None)
        got.pop("_words", None)
        return {"event": "digitise", "lib": lib.id, "figure": str(req["figure"]), **got, "saved": saved, "orphans": lost}
    if op == "digitise_extract":
        path = _image_path(str(req.get("image") or ""))
        img = load_image(path)
        cal = Calibration.from_dict(req["calibration"]) if req.get("calibration") else None
        got = extract(img, str(req.get("colour") or ""), tol=float(req.get("tolerance") or TOLERANCE), region=req.get("region"), mode=str(req.get("mode") or "auto"),
                      calibration=cal, words=load_words(path))
        return {"event": "extracted", **got}
    if op == "digitise_axes":
        path = _image_path(str(req.get("image") or ""))
        img = load_image(path)
        near = req.get("near")
        got = auto_axes(img, load_words(path, whole=True), near=[float(v) for v in near] if near else None)
        got.pop("_words", None)
        return {"event": "axes", "auto": got}
    if op == "fit":
        did = req.get("series_id")  # not `id`: that is the request's own, on the wire
        models = [str(req["model"])] if req.get("model") else fitting.MODELS
        if did is not None:
            lib = get_lib()
            conn = open_store(lib.store_path)
            try:
                out = fitting.both([(p["x"], p["y"]) for p in one(conn, int(did))["points"]], models)
                for f in out["fits"].values():
                    save_fit(conn, int(did), f)
            finally:
                conn.close()
        else:
            out = fitting.both([(float(a), float(b)) for a, b in (req.get("points") or [])], models)
        return {"event": "fit", "series": did, **out}
    if op == "digitise_export" and not req.get("lib"):
        inline = [unsaved(x, req.get("source")) for x in req.get("series") or []]
        return {"event": "exported", "lib": None, **export(None, [], Path(str(req["path"])).expanduser(), inline=inline)}
    lib = get_lib()
    conn = open_store(lib.store_path)
    try:
        if op == "digitise_save":
            saved = save(lib.dir, conn, str(req["figure"]), str(req.get("series") or ""), req["calibration"], req.get("points") or [], image=req.get("image") or {}, colour=req.get("colour"),
                         method=req.get("method"), axes=req.get("axes"), source=req.get("source"), series_id=int(req["series_id"]) if req.get("series_id") is not None else None)
            return {"event": "digitised", "lib": lib.id, "series": saved}
        if op == "digitise_attach":
            return {"event": "digitised", "lib": lib.id, "series": attach(lib.dir, conn, int(req["series_id"]), str(req["figure"]))}
        if op == "digitise_delete":
            return {"event": "digitised", "lib": lib.id, "deleted": delete(conn, int(req["series_id"]))}
        if op == "digitise_export":
            inline = [unsaved(x, req.get("source")) for x in req.get("series") or []]
            return {"event": "exported", "lib": lib.id, **export(conn, [int(i) for i in req.get("ids") or []], Path(str(req["path"])).expanduser(), library=lib.id, inline=inline)}
    finally:
        conn.close()
    raise ValueError(f"Unknown op {op!r}")


# -- helpers ------------------------------------------------------------------------------------------------------


def _r(v: float) -> float:
    if v == 0 or not math.isfinite(v):
        return v
    return round(v, max(0, 6 - int(math.floor(math.log10(abs(v))))))


def _fmt(v: float) -> str:
    return repr(v) if isinstance(v, float) else str(v)

