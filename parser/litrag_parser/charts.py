"""Charts read from a figure's pixels: the number each bar or point stands for, with its error bar.

A figure is an image and, beside it, the words on it — the PDF's own text layer when the figure
was drawn as vectors, else what OCR reads (RapidOCR, with the models its package ships: nothing
is fetched). `read(image, words)` finds every plot in it and reads each one:

- **The frame.** A plot is a y axis and an x axis meeting at the bottom left: a thin vertical
  line whose foot is the left end of a thin horizontal one. A photograph, a scale bar or a
  panel's border has no numbers beside it, and fails the next step.
- **The scale.** The y axis is read from its tick labels: at least `MIN_TICKS` numbers beside
  it, each placed at its tick mark when one is drawn, fitted to a straight line in pixels —
  or, when they rise by powers of ten, to a log scale — and the fit must hold within
  `MAX_RESIDUAL` of the axis's range. One misread label may be left out; more and the plot is
  `unread`, with the reason. An x axis with numbers is read the same way.
- **The marks.** Inside the frame, what is solid survives an opening that removes thin
  strokes (error bars, connecting lines, outlines, a dashed reference line). A solid shape
  standing on the baseline is a bar — touching bars are told apart by their colour and their
  height; a compact shape off it is a marker. A bar's value is its top, a marker's its centre.
- **The error bars.** A thin vertical stroke rising from a bar's top (and, drawn over the bar
  in another colour, falling into it), or leaving a marker above and below: its end, read on
  the same scale. A whisker that cannot be seen is null, not assumed symmetric.
- **The names.** Bars are grouped by the category labels under the axis, in order; a group's
  bars by their colour into series, named by the legend's swatches when there is a legend.
  The y axis's title and its unit, in parentheses, and the panel's letter are read from the
  words around the frame.

Every value is a measurement of the pixels on a calibrated scale; nothing is estimated by a
model. A plot is `read` when its scale holds and something was measured in it; otherwise it
is `unread` with the reason, and no value is given.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import numpy as np

MIN_TICKS = 3
NOT_A_CHART = "no numbers beside the y axis"
MAX_RESIDUAL = 0.01  # of the axis's range
BACKGROUND = 238  # a pixel lighter than this in every channel is paper
SAME_COLOUR = 45.0  # RGB distance within which two fills are one colour


@dataclass
class Word:
    """A word or a run of words on the figure, in its pixels (y down)."""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    score: float = 1.0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def w(self) -> float:
        return self.x1 - self.x0


@dataclass
class Line:
    """A thin straight stroke: `a0..a1` along it, `b0..b1` across it (rows for a horizontal one)."""

    a0: int
    a1: int
    b0: int
    b1: int

    @property
    def length(self) -> int:
        return self.a1 - self.a0 + 1

    @property
    def mid(self) -> float:
        return (self.b0 + self.b1) / 2


@dataclass
class Scale:
    """Pixels to values on one axis: linear, or log10."""

    log: bool
    a: float
    b: float
    residual: float
    ticks: list[tuple[float, float]] = field(default_factory=list)  # (pixel, value) that made it

    def value(self, px: float) -> float:
        v = self.a + self.b * px
        return 10**v if self.log else v

    def pixel(self, value: float) -> float:
        v = math.log10(value) if self.log else value
        return (v - self.a) / self.b


# -- the words ----------------------------------------------------------------------------------------------

_NUMBER = re.compile(r"^[(\[]?([−–\-+]?\d+(?:[.,]\d+)?)\s*%?[)\]]?$")
_POWER = re.compile(r"^10\s*\^?\s*[\{(]?([−–\-]?\d{1,2})[\})]?$")
_SUPER = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
_PANEL = re.compile(r"^[(\[]?([A-Za-z])[)\]]?\.?$")
_UNIT = re.compile(r"\(([^()]{1,20})\)\s*$")


def number(text: str) -> float | None:
    """A tick label's value: `40`, `2.5`, `−1`, `0,5`, `50%`; None for anything else."""
    t = re.sub(r"[-–—]+$", "", text.strip().replace(" ", ""))  # OCR reads a tick mark into its label: "0.4-"
    m = _NUMBER.match(t)
    if not m:
        return None
    s = m.group(1).replace("−", "-").replace("–", "-")
    if "," in s:
        head, _, tail = s.partition(",")
        if len(tail) == 3 and head not in ("0", "-0"):  # 1,000
            s = head + tail
        else:  # 0,5
            s = head + "." + tail
    try:
        return float(s)
    except ValueError:
        return None


def power(text: str) -> int | None:
    """A log axis's label as its exponent: `10^3`, `10³`, `10{3}`; and `103` (a superscript the
    text layer or OCR ran into the base) only where `powers` finds a run of them."""
    raw_ = text.strip().replace(" ", "")
    if not any(c in raw_ for c in "^{(") and raw_ == raw_.translate(_SUPER):
        return None  # "103" alone is a number; a run of them is read in y_scale
    t = raw_.translate(_SUPER)
    m = _POWER.match(t)
    if not m:
        return None
    try:
        return int(m.group(1).replace("−", "-").replace("–", "-"))
    except ValueError:
        return None


# -- the strokes --------------------------------------------------------------------------------------------


def foreground(img: np.ndarray) -> np.ndarray:
    """What is ink, not paper."""
    return (img < BACKGROUND).any(axis=2)


def _runs(row: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    """The runs of True at least `min_len` long in one row: (start, end) inclusive."""
    if not row.any():
        return []
    d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1
    keep = ends - starts + 1 >= min_len
    return list(zip(starts[keep].tolist(), ends[keep].tolist()))


def lines(mask: np.ndarray, min_len: int, max_thick: int, horizontal: bool = True) -> list[Line]:
    """Thin straight strokes: runs at least `min_len` long on consecutive rows (columns) with
    the same ends, no more than `max_thick` of them."""
    m = mask if horizontal else mask.T
    open_: list[Line] = []
    done: list[Line] = []
    for b in range(m.shape[0]):
        runs = _runs(m[b], min_len)
        nxt: list[Line] = []
        used = set()
        for ln in open_:
            hit = next((i for i, (s, e) in enumerate(runs) if i not in used and abs(s - ln.a0) <= 2 and abs(e - ln.a1) <= 2), None)
            if hit is None:
                done.append(ln)
            else:
                used.add(hit)
                s, e = runs[hit]
                nxt.append(Line(min(ln.a0, s), max(ln.a1, e), ln.b0, b))
        for i, (s, e) in enumerate(runs):
            if i not in used:
                nxt.append(Line(s, e, b, b))
        open_ = nxt
    done.extend(open_)
    return [ln for ln in done if ln.b1 - ln.b0 + 1 <= max_thick]


def _opening(mask: np.ndarray, k: int, round_: bool = False) -> np.ndarray:
    import cv2

    if k <= 1:
        return mask.copy()
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE if round_ else cv2.MORPH_RECT, (k, k))
    return cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, kernel).astype(bool)


def _blobs(mask: np.ndarray) -> list[tuple[int, int, int, int, int, np.ndarray]]:
    """Connected shapes: (x0, y0, x1, y1, area, their own mask within the bbox)."""
    import cv2

    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        out.append((x, y, x + w - 1, y + h - 1, area, lab[y : y + h, x : x + w] == i))
    return out


def _colour(img: np.ndarray, sel: np.ndarray) -> tuple[int, int, int]:
    px = img[sel]
    if not len(px):
        return (255, 255, 255)
    return tuple(int(v) for v in np.median(px, axis=0))


def _dist(a: Iterable[float], b: Iterable[float]) -> float:
    return float(math.dist(tuple(a), tuple(b)))


# -- the scale ----------------------------------------------------------------------------------------------


def _fit(points: list[tuple[float, float]], log: bool) -> Scale | None:
    if len(points) < MIN_TICKS:
        return None
    px = np.array([p for p, _ in points], dtype=float)
    vals = np.array([v for _, v in points], dtype=float)
    if log:
        if (vals <= 0).any():
            return None
        vals = np.log10(vals)
    if np.ptp(px) == 0 or np.ptp(vals) == 0:
        return None
    b, a = np.polyfit(px, vals, 1)
    resid = float(np.max(np.abs(a + b * px - vals)) / np.ptp(vals))
    return Scale(log, float(a), float(b), resid, list(points))


def calibrate(points: list[tuple[float, float]], rising: int) -> Scale | None:
    """A scale from (pixel, value) ticks: linear, else log when the values go by powers of ten;
    `rising` +1 when values grow with the pixel (x), -1 when they fall (y). One tick may be
    left out to make it hold; it must hold within MAX_RESIDUAL."""
    pts = sorted(set(points))
    best: Scale | None = None
    candidates = [pts] + ([[p for j, p in enumerate(pts) if j != i] for i in range(len(pts))] if len(pts) > MIN_TICKS else [])
    for cand in candidates:
        vals = [v for _, v in cand]
        if len(set(vals)) != len(vals):
            continue
        decades = min(vals) > 0 and max(vals) / min(vals) >= 100
        for log in ((False, True) if decades else (False,)):
            s = _fit(cand, log)
            if s is None or s.residual > MAX_RESIDUAL or (s.b > 0) != (rising > 0):
                continue
            if best is None or len(s.ticks) > len(best.ticks) or (len(s.ticks) == len(best.ticks) and s.residual < best.residual):
                best = s
        if best is not None and cand is pts:
            break  # every tick holds: no need to leave one out
    return best


def _ticks_marks(fg: np.ndarray, axis: Line, horizontal_axis: bool, reach: int) -> list[float]:
    """Where tick marks cross an axis: short strokes on either side of it, as pixel centres
    along the axis."""
    if horizontal_axis:  # the x axis: ticks are short vertical strokes below or above it
        rows_below = fg[axis.b1 + 1 : axis.b1 + 1 + reach, axis.a0 : axis.a1 + 1]
        rows_above = fg[max(axis.b0 - reach, 0) : axis.b0, axis.a0 : axis.a1 + 1]
        hits = np.zeros(axis.length, bool)
        if rows_below.shape[0] >= 3:
            hits |= rows_below[:3].all(axis=0)
        if rows_above.shape[0] >= 3:
            hits |= rows_above[-3:].all(axis=0)
        offset = axis.a0
    else:  # the y axis: ticks are short horizontal strokes left or right of it
        left = fg[axis.a0 : axis.a1 + 1, max(axis.b0 - reach, 0) : axis.b0]
        right = fg[axis.a0 : axis.a1 + 1, axis.b1 + 1 : axis.b1 + 1 + reach]
        hits = np.zeros(axis.length, bool)
        if left.shape[1] >= 3:
            hits |= left[:, -3:].all(axis=1)
        if right.shape[1] >= 3:
            hits |= right[:, :3].all(axis=1)
        offset = axis.a0
    out, start = [], None
    for i, h in enumerate(np.append(hits, False)):
        if h and start is None:
            start = i
        elif not h and start is not None:
            out.append(offset + (start + i - 1) / 2)
            start = None
    return out


def _snap(at: float, marks: list[float], tol: float) -> float:
    near = [m for m in marks if abs(m - at) <= tol]
    return min(near, key=lambda m: abs(m - at)) if near else at


def y_scale(fg: np.ndarray, words: list[Word], yaxis: Line, xaxis: Line) -> tuple[Scale | None, list[Word], str]:
    """The y axis read from the numbers left of it: (scale, the tick words, why not)."""
    x = yaxis.mid
    top, bottom = yaxis.a0, yaxis.a1
    width = xaxis.a1 - xaxis.a0
    beside = [w for w in words if w.x1 <= x + 3 and w.x1 >= x - max(0.35 * width, 60) and top - w.h <= w.cy <= bottom + w.h]
    nums = [(w, number(w.text)) for w in beside]
    nums = [(w, v) for w, v in nums if v is not None]
    pows = [(w, power(w.text)) for w in beside]
    pows = [(w, p) for w, p in pows if p is not None]
    if len(pows) >= MIN_TICKS and len(pows) >= len(nums) * 0.6:
        nums = [(w, 10.0**p) for w, p in pows]
    elif nums and all(re.fullmatch(r"10\d", w.text.strip()) for w, _ in nums) and len(nums) >= MIN_TICKS:
        exps = sorted(int(w.text.strip()[2]) for w, _ in nums)
        if exps == list(range(exps[0], exps[0] + len(exps))):  # 102, 103, 104: superscripts run into the base
            nums = [(w, 10.0 ** int(w.text.strip()[2])) for w, _ in nums]
    # a tick label is set level, at the size of the others: not a piece of a title on its side
    nums = [(w, v) for w, v in nums if not (len(w.text.strip()) >= 2 and w.h > 1.2 * w.w)]
    if nums:
        hmed = float(np.median([w.h for w, _ in nums]))
        nums = [(w, v) for w, v in nums if 0.6 * hmed <= w.h <= 1.4 * hmed]
    if not nums:
        return None, [], NOT_A_CHART
    # the column of labels nearest the axis: their right edges line up
    right = max(w.x1 for w, _ in nums)
    nums = [(w, v) for w, v in nums if w.x1 >= right - max(3 * max(w.h for w, _ in nums), 12)]
    marks = _ticks_marks(fg, yaxis, horizontal_axis=False, reach=max(4, int(max(w.h for w, _ in nums))))
    pts = [(_snap(w.cy, marks, w.h * 0.6), v) for w, v in nums]
    s = calibrate(pts, rising=-1)
    if s is None:
        return None, [w for w, _ in nums], f"{len(nums)} numbers beside the y axis, and no scale holds within {MAX_RESIDUAL:.0%}"
    used = {(round(p, 3), v) for p, v in s.ticks}
    return s, [w for w, v in nums if (round(_snap(w.cy, marks, w.h * 0.6), 3), v) in used], ""


def x_scale(fg: np.ndarray, words: list[Word], yaxis: Line, xaxis: Line) -> tuple[Scale | None, list[Word]]:
    """The x axis read from the numbers under it, when it has numbers."""
    y = xaxis.mid
    height = yaxis.a1 - yaxis.a0
    under = [w for w in words if y - 3 <= w.y0 <= y + max(0.25 * height, 40) and xaxis.a0 - 2 * w.w <= w.cx <= xaxis.a1 + 2 * w.w]
    nums = [(w, number(w.text)) for w in under]
    nums = [(w, v) for w, v in nums if v is not None]
    if len(nums) < MIN_TICKS:
        return None, []
    top = min(w.y0 for w, _ in nums)
    nums = [(w, v) for w, v in nums if w.y0 <= top + max(w.h for w, _ in nums)]
    marks = _ticks_marks(fg, xaxis, horizontal_axis=True, reach=10)
    pts = [(_snap(w.cx, marks, w.w * 0.6 + 2), v) for w, v in nums]
    s = calibrate(pts, rising=1)
    return s, [w for w, _ in nums] if s else []


# -- the frame ----------------------------------------------------------------------------------------------


def frames(fg: np.ndarray) -> list[tuple[Line, Line]]:
    """Every (y axis, x axis) pair meeting at a bottom-left corner."""
    h, w = fg.shape
    min_len = max(25, int(0.05 * max(h, w)))
    thick = max(3, int(0.006 * max(h, w)))
    hs = lines(fg, min_len, thick, horizontal=True)  # a0..a1 columns, b rows
    vs = lines(fg, min_len, thick, horizontal=False)  # a0..a1 rows, b columns
    out = []
    for v in vs:
        tol = max(4, 2 * (v.b1 - v.b0 + 1))
        best = None
        for hz in hs:
            if -tol <= v.a1 - hz.mid <= max(tol, 0.06 * v.length) and hz.a0 - tol <= v.mid <= hz.a0 + max(tol, 0.15 * hz.length) and hz.a1 > v.mid + min_len:
                if best is None or hz.length > best.length:
                    best = hz
        if best is not None:
            out.append((v, best))
    # one frame per x axis — the vertical nearest its left end (a bar's edge or outline stands on
    # it too) — and per corner (a thick axis can be found twice)
    by_axis: dict[tuple[int, int, int], tuple[Line, Line]] = {}
    for v, hz in out:
        key = (hz.a0, hz.a1, hz.b0)
        if key not in by_axis or abs(v.mid - hz.a0) < abs(by_axis[key][0].mid - hz.a0):
            by_axis[key] = (v, hz)
    kept: list[tuple[Line, Line]] = []
    for v, hz in sorted(by_axis.values(), key=lambda f: (-f[0].length - f[1].length)):
        if not any(abs(v.mid - k[0].mid) < 10 and abs(hz.mid - k[1].mid) < 10 for k in kept):
            kept.append((v, hz))
    return kept


# -- the marks ----------------------------------------------------------------------------------------------


def _stroke(img: np.ndarray, plot: np.ndarray) -> int:
    """The opening that removes thin strokes but keeps bars and markers: twice the commonest
    stroke width inside the plot, read from its short runs across rows and columns."""
    widths: list[int] = []
    for m in (plot, plot.T):
        for row in m[:: max(1, m.shape[0] // 200)]:
            widths += [e - s + 1 for s, e in _runs(row, 1) if e - s + 1 <= 12]
    if not widths:
        return 3
    common = int(np.bincount(widths).argmax())
    return max(3, 2 * common + 1)


def _fill(img: np.ndarray, own: np.ndarray, cols: list[int], t: int, by1: int) -> tuple[int, int, int]:
    """A bar's fill: its lower half, away from its edges (an outline) and its middle (a whisker)."""
    n = len(cols)
    side = [cols[i] for i in list(range(n // 5, 2 * n // 5)) + list(range(3 * n // 5, 4 * n // 5))] or [cols[n // 2]]
    sel = np.zeros(own.shape, bool)
    lo = int(t + (by1 - t) / 2)
    if by1 - 1 > lo:
        sel[lo : by1 - 1, side] = True
    sel &= own
    if not sel.any():
        sel = own
    return _colour(img, sel)


def _bars(img: np.ndarray, fg: np.ndarray, x0: int, x1: int, top: int, base: int, k: int) -> list[dict[str, Any]]:
    """Solid shapes standing on the baseline. A shape is one bar unless it holds two or more wide
    runs of columns of different fills — bars drawn touching — when it is parted between them;
    a narrow run (an outline, an edge, a whisker down the middle) belongs to the bar around it."""
    import cv2

    region = fg[top:base, x0:x1]
    sub = img[top:base, x0:x1]
    # a pale fill, compressed, lets columns of paper through: closed across, never up to a line above it
    closed = cv2.morphologyEx(region.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((1, 3), np.uint8)).astype(bool)
    solid = _opening(closed, k)
    out = []
    tol = max(3, k)
    for bx0, by0, bx1, by1, area, own in _blobs(solid):
        if by1 < region.shape[0] - tol or bx1 - bx0 + 1 < k:
            continue  # not standing on the baseline
        width = bx1 - bx0 + 1
        tops = [int(np.argmax(own[:, c])) + by0 for c in range(width)]
        colours = []
        for c in range(width):
            r = tops[c]
            lo = r + max(2, (by1 - r) // 4)
            col = sub[lo : by1 - 1, bx0 + c] if by1 - 1 > lo else sub[r : by1 + 1, bx0 + c]
            colours.append(tuple(int(v) for v in np.median(col, axis=0)) if len(col) else (0, 0, 0))
        runs: list[list[int]] = []
        for c in range(width):
            if runs and _dist(colours[c], colours[runs[-1][0]]) <= SAME_COLOUR:
                runs[-1].append(c)
            else:
                runs.append([c])
        least = max(3, k // 2, int(0.25 * max(len(r) for r in runs)), int(0.2 * width) if width < 6 * k else 0)
        wide = [r for r in runs if len(r) >= least]
        parts: list[list[int]] = []
        if len(wide) >= 2 and any(_dist(colours[wide[i][0]], colours[wide[i + 1][0]]) > SAME_COLOUR for i in range(len(wide) - 1)):
            # touching bars: each wide run with the narrow runs nearest it; neighbours of one fill stay one bar
            owner = [min(range(len(wide)), key=lambda j: min(abs(c - wide[j][0]), abs(c - wide[j][-1]))) for c in range(width)]
            for j in range(len(wide)):
                cols = [c for c in range(width) if owner[c] == j]
                if parts and _dist(colours[wide[j][0]], colours[parts[-1][len(parts[-1]) // 2]]) <= SAME_COLOUR:
                    parts[-1] += cols
                else:
                    parts.append(cols)
        else:
            parts = [list(range(width))]
        for cols in parts:
            inner = cols[2:-2] if len(cols) > 6 else cols
            # the top where most columns have it: a cap or a whisker over the middle sits higher
            t = float(np.percentile([tops[c] for c in inner], 80))
            fill = _fill(sub, own_full := _place(own, region.shape, bx0, by0), [bx0 + c for c in cols], int(t), by1)
            # an outline across the top: a band of another colour than the fill, just under the first ink
            n = len(cols)
            col = bx0 + cols[max(n // 5, 0)]
            r0 = int(t)
            band = [r for r in range(r0, min(r0 + max(8, 2 * k), by1)) if _dist(sub[r, col], fill) > SAME_COLOUR]
            if band and band[0] - r0 <= 2 and band[-1] - band[0] + 1 == len(band) and len(band) <= max(6, k):
                edge = (band[0] + band[-1]) / 2  # an outline: its middle
            else:
                edge = t - 0.5  # a bare fill: its edge
            out.append({"x0": x0 + bx0 + cols[0], "x1": x0 + bx0 + cols[-1], "top": top + edge, "colour": fill})
    out.sort(key=lambda b: b["x0"])
    return out


def _place(own: np.ndarray, shape: tuple[int, ...], bx0: int, by0: int) -> np.ndarray:
    """A blob's own mask, set back in the region it was cut from."""
    full = np.zeros(shape[:2], bool)
    full[by0 : by0 + own.shape[0], bx0 : bx0 + own.shape[1]] = own
    return full


def _thin_run(fg: np.ndarray, x: int, y: int, step: int, limit: int, half: int = 1, max_width: int = 4, cross: int = 0) -> float | None:
    """From (x, y), the farthest row a thin vertical stroke reaches going up (step -1) or down
    (+1), one gap of a pixel allowed, ending in the middle of its cap when it has one; None when
    no stroke leaves there. Up to `cross` wide rows at the start (an outline) are crossed."""
    h, w = fg.shape
    last, gap, r = None, 0, y
    while 0 <= r < h and abs(r - y) <= limit:
        lo, hi = max(x - half, 0), min(x + half + 1, w)
        if fg[r, lo:hi].any():
            row = fg[r]
            c = lo + int(np.argmax(row[lo:hi]))
            a = c
            while a > 0 and row[a - 1]:
                a -= 1
            b = c
            while b < w - 1 and row[b + 1]:
                b += 1
            if b - a + 1 > max_width:
                if last is None:
                    if abs(r - y) < cross:
                        r += step
                        continue
                    break
                cap = 0  # a cap: the stroke ends in its middle
                while 0 <= r + step * cap < h and abs(r + step * cap - y) <= limit and fg[r + step * cap, x] and cap < 3 * max_width:
                    cap += 1
                return r + step * (cap - 1) / 2
            last, gap = r, 0
        else:
            gap += 1
            if gap > 1:
                break
        r += step
    return last


def _error_bars(img: np.ndarray, fg: np.ndarray, bar: dict[str, Any], top_limit: int, max_width: int) -> tuple[float | None, float | None]:
    """The rows a bar's error bar reaches above its top and below it into the bar."""
    xc = int(round((bar["x0"] + bar["x1"]) / 2))
    t = int(round(bar["top"]))
    best = None
    for dx in range(-max(1, (bar["x1"] - bar["x0"]) // 8), max(1, (bar["x1"] - bar["x0"]) // 8) + 1):
        r = _thin_run(fg, xc + dx, t, -1, t - top_limit, max_width=max_width, cross=max(4, 2 * max_width))
        if r is not None and t - r >= 3 and (best is None or r < best[0]):
            best = (r, xc + dx)
    up = float(best[0]) if best else None
    x = best[1] if best else xc
    # below the top, inside the bar: a stroke of another colour than the fill, centred
    down = None
    fill = bar["colour"]
    off = max(2, (bar["x1"] - bar["x0"]) // 4)
    r = t + 1
    seen = None
    while r < img.shape[0] - 1:
        centre = img[r, max(x - 1, 0) : x + 2].astype(float)
        side = img[r, min(x + off, img.shape[1] - 1)].astype(float)
        c_off = max(_dist(p, fill) for p in centre)
        if _dist(side, fill) > SAME_COLOUR:  # an outline row across the bar: not yet inside it
            if seen is not None:
                break
            r += 1
            continue
        if c_off > SAME_COLOUR * 1.5:
            seen = r
        elif seen is not None or r > t + max(6, (bar["x1"] - bar["x0"]) // 2):
            break
        r += 1
    if seen is not None and seen - t >= 3:
        down = float(seen)
    return up, down


def _markers(img: np.ndarray, fg: np.ndarray, x0: int, x1: int, top: int, base: int, k: int) -> list[dict[str, Any]]:
    """The markers of a point plot: round shapes, at the opening that first leaves most of them
    standing apart from the lines joining them. Two markers drawn over each other are told apart
    by their colour, each centred from the sides of it that show; one cut by the x axis, from
    its top."""
    region = fg[top:base, x0:x1]
    sub = img[top:base, x0:x1]
    for kk in sorted({int(round(k * f)) for f in (1, 1.25, 1.5, 1.8, 2.2, 2.7, 3.3)}):
        blobs = _blobs(_opening(region, kk, round_=True))
        if not blobs:
            return []
        # markers come out square once the lines through them are gone; two drawn over each other do not
        square = [b for b in blobs if 0.85 <= (b[2] - b[0] + 1) / (b[3] - b[1] + 1) <= 1.18 and b[4] >= 0.6 * (b[2] - b[0] + 1) * (b[3] - b[1] + 1)]
        if len(square) < 2 or len(square) < 0.6 * len(blobs):
            continue
        med = float(np.median([min(b[2] - b[0], b[3] - b[1]) + 1 for b in square]))
        r = (med - 1) / 2
        cut = [b for b in blobs if b not in square and b[3] >= region.shape[0] - 2 and 0.85 * med <= b[2] - b[0] + 1 <= 1.18 * med and b[3] - b[1] + 1 < 0.9 * med]
        compact = [b for b in square if max(b[2] - b[0], b[3] - b[1]) + 1 <= 1.2 * med] + cut  # one marker each, some cut by the x axis
        out: list[dict[str, Any]] = []
        for bx0, by0, bx1, by1, area, own in compact:
            ys, xs = np.nonzero(own)
            sel = np.zeros(region.shape, bool)
            sel[by0 : by1 + 1, bx0 : bx1 + 1] = own
            cy = by0 + float(ys.mean())
            cx = bx0 + float(xs.mean())
            if by1 >= region.shape[0] - 2 and by1 - by0 + 1 < 0.9 * med:
                cy = by0 + r  # cut by the x axis: centred from its top
            if bx0 <= 1 and bx1 - bx0 + 1 < 0.9 * med:
                cx = bx1 - r  # cut by the y axis: centred from its right
            out.append({"cx": x0 + cx, "cy": top + cy, "y0": top + by0, "y1": top + by1, "colour": _colour(sub, sel)})
        colours = []
        for m in out:
            if not any(_dist(m["colour"], c) <= SAME_COLOUR for c in colours):
                colours.append(m["colour"])
        for bx0, by0, bx1, by1, area, own in blobs:  # markers over each other: one colour at a time
            w_, h_ = bx1 - bx0 + 1, by1 - by0 + 1
            if any(b[0] == bx0 and b[1] == by0 for b in compact) or max(w_, h_) > 3.2 * med or max(w_, h_) < 1.2 * med:
                continue
            box = sub[by0 : by1 + 1, bx0 : bx1 + 1].astype(float)
            parts = []
            for c in colours:
                # a marker's own colour, opened at most of its size: not the other's anti-aliased rim, not a line through it
                mine = _opening(own & (np.linalg.norm(box - np.array(c, float), axis=2) <= SAME_COLOUR), max(5, int(0.4 * med)), round_=True)
                pieces = _blobs(mine)
                if not pieces:
                    continue
                px0, py0, px1, py1, parea, pown = max(pieces, key=lambda b: b[4])
                mine = np.zeros_like(mine)
                mine[py0 : py1 + 1, px0 : px1 + 1] = pown
                if parea >= 0.25 * med * med:
                    parts.append((c, mine))
            for c, mine in parts:
                ys, xs = np.nonzero(mine)
                others = [m for cc, m in parts if cc != c]
                ox = float(np.nonzero(others[0])[1].mean()) if others else None
                oy = float(np.nonzero(others[0])[0].mean()) if others else None
                if bx0 + xs.min() <= 1:
                    cx = xs.max() - r  # cut by the y axis
                elif xs.max() - xs.min() + 1 >= 0.85 * med or ox is None:
                    cx = (xs.min() + xs.max()) / 2
                else:
                    cx = xs.min() + r if ox > xs.mean() else xs.max() - r
                if by0 + ys.max() >= region.shape[0] - 2:
                    cy = ys.min() + r
                elif ys.max() - ys.min() + 1 >= 0.85 * med or oy is None:
                    cy = (ys.min() + ys.max()) / 2
                else:
                    cy = ys.min() + r if oy > ys.mean() else ys.max() - r
                out.append({"cx": x0 + bx0 + float(cx), "cy": top + by0 + float(cy), "y0": top + by0 + float(cy) - r, "y1": top + by0 + float(cy) + r, "colour": c})
        return out if len(out) >= 2 else []
    return []


# -- the names ----------------------------------------------------------------------------------------------


def legend(img: np.ndarray, fg: np.ndarray, words: list[Word], taken: set[int], near: tuple[float, float, float, float] | None = None) -> list[tuple[str, tuple[int, int, int]]]:
    """Legend entries: a short label with a swatch of colour just left of it, (name, colour) —
    within `near` (x0, y0, x1, y1) when given: a legend stands by its plot, not in the text
    around the figure."""
    out = []
    for i, w in enumerate(words):
        if i in taken or number(w.text) is not None or len(w.text.strip()) < 2 or w.h > 1.2 * w.w or not any(c.isalnum() for c in w.text):
            continue  # a tick, a lone mark, a significance star, a title set on its side
        if len(w.text.strip()) > 30 or len(w.text.split()) > 4:
            continue  # a line of prose
        if near is not None and not (near[0] <= w.cx <= near[2] and near[1] <= w.cy <= near[3]):
            continue
        x1 = int(w.x0) - 1
        x0 = int(max(w.x0 - 4 * w.h, 0))
        y0, y1 = int(w.cy - 0.35 * w.h), int(w.cy + 0.35 * w.h) + 1
        box = fg[y0:y1, x0:x1]
        if box.size == 0 or box.sum() < 0.15 * box.size:
            continue
        sel = np.zeros(fg.shape, bool)
        sel[y0:y1, x0:x1] = box
        cols = np.flatnonzero(box.any(axis=0))
        if not len(cols) or x1 - (x0 + cols[-1]) > 1.5 * w.h:
            continue  # nothing close to the word's left
        out.append((w.text.strip(), _colour(img, sel)))
    return out


def _name_of(colour: tuple[int, int, int], entries: list[tuple[str, tuple[int, int, int]]]) -> str | None:
    if not entries:
        return None
    name, c = min(entries, key=lambda e: _dist(e[1], colour))
    return name if _dist(c, colour) <= SAME_COLOUR else None


def _panel(words: list[Word], yaxis: Line, xaxis: Line) -> str | None:
    """The panel's letter: a lone letter above and left of the frame, nearest its corner."""
    cands = []
    for w in words:
        m = _PANEL.match(w.text.strip())
        if m and w.cy <= yaxis.a0 + 0.15 * (yaxis.a1 - yaxis.a0) and w.cx <= yaxis.mid + 0.25 * (xaxis.a1 - xaxis.a0):
            d = math.hypot(w.cx - yaxis.mid, w.cy - yaxis.a0)
            if d <= 0.8 * max(yaxis.length, xaxis.length):
                cands.append((d, m.group(1).upper()))
    return min(cands)[1] if cands else None


def _y_title(words: list[Word], ticks: list[Word], yaxis: Line, img: np.ndarray | None = None, ocr: "Ocr | None" = None) -> str:
    """The words left of the tick labels along the axis: its title. Read by OCR, a title set
    on its side is read again from the region turned upright."""
    left = min((w.x0 for w in ticks), default=yaxis.mid)
    reach = 6 * max((t.h for t in ticks), default=20)
    if ocr is not None and img is not None:
        x0, x1 = int(max(left - reach, 0)), int(max(left - 1, 0))
        crop = img[max(yaxis.a0 - 10, 0) : yaxis.a1 + 10, x0:x1]
        if crop.size and foreground(crop).any():
            upright = [w for w in ocr(np.ascontiguousarray(np.rot90(crop, k=-1))) if w.text.strip()]
            if upright:
                lines_: dict[int, list[Word]] = {}
                for w in sorted(upright, key=lambda w: w.cy):
                    key = next((k for k in lines_ if abs(k - w.cy) <= 0.6 * w.h), int(w.cy))
                    lines_.setdefault(key, []).append(w)
                return " ".join(" ".join(x.text.strip() for x in sorted(ws, key=lambda w: w.x0)) for _, ws in sorted(lines_.items()))
    near = [w for w in words if w.x1 <= left + 2 and w.y1 >= yaxis.a0 - 10 and w.y0 <= yaxis.a1 + 10 and w not in ticks and number(w.text) is None
            and left - w.x1 <= reach and not _PANEL.match(w.text.strip())]
    if not near:
        return ""
    near.sort(key=lambda w: -w.x1)
    column = [w for w in near if w.x1 >= near[0].x0 - 2]
    column.sort(key=lambda w: w.y1, reverse=True)  # a rotated title reads bottom to top
    return " ".join(w.text.strip() for w in column)


def _plot_title(words: list[Word], yaxis: Line, xaxis: Line, others: list[tuple[Line, Line]] = ()) -> str:
    """The words printed above the frame and over it: the plot's own title ("Day 1", "Pore Size") —
    not the labels under the axis of a plot above it."""
    h = yaxis.a1 - yaxis.a0

    def under_another(w: Word) -> bool:
        return any(v is not yaxis and v.mid - 0.1 * (hz.a1 - v.mid) <= w.cx <= hz.a1 and hz.mid - 2 <= w.y0 <= hz.mid + 0.4 * (v.a1 - v.a0) for v, hz in others)

    above = [w for w in words if yaxis.a0 - 0.3 * h <= w.y1 <= yaxis.a0 + 0.05 * h and yaxis.mid - 0.05 * h <= w.cx <= xaxis.a1
             and number(w.text) is None and not _PANEL.match(w.text.strip()) and any(c.isalpha() for c in w.text) and w.w >= w.h and not under_another(w)
             and not re.search(r"www\.|https?:|doi|©|\bpage\b", w.text, re.I)]  # a page's running head is no title
    if not above:
        return ""
    low = max(w.y1 for w in above)  # the line nearest the frame
    return " ".join(w.text.strip() for w in sorted((w for w in above if w.y1 >= low - 0.8 * w.h), key=lambda w: w.x0))


def _entitle(p: dict[str, Any], words: list[Word], yaxis: Line, xaxis: Line, frames_: list[tuple[Line, Line]]) -> None:
    """A plot's title from above it, and a y title that is only a unit named after it: "µm" under
    "Pore Size" reads "Pore Size (µm)"."""
    p["title"] = _plot_title(words, yaxis, xaxis, frames_) or None
    label = p["y"].get("label") or ""
    if p["title"] and len(re.sub(r"\([^)]*\)", "", label).strip()) < 3:
        p["y"]["label"] = f"{p['title']} ({unit_of(label) or label.strip()})" if label.strip() else p["title"]
        p["y"]["unit"] = unit_of(p["y"]["label"])


def clean_title(text: str) -> str:
    """A title as OCR gives it, without what is not one: significance stars, stray marks read as
    other scripts ("水水水" for "***")."""
    keep = [t for t in text.split() if re.search(r"[A-Za-z0-9µμ%°]", t) and not re.search(r"[\u3000-\u9fff\uac00-\ud7af]", t)]
    return " ".join(keep).strip()


def _near(yaxis: Line, xaxis: Line) -> tuple[float, float, float, float]:
    """Where a plot's legend can stand: over it, or a little beside it, chiefly to its right."""
    w, h = xaxis.a1 - yaxis.mid, xaxis.mid - yaxis.a0
    return (yaxis.mid - 0.1 * w, yaxis.a0 - 0.25 * h, xaxis.a1 + 0.6 * w, xaxis.mid + 0.1 * h)


def share_legend(plots: list[dict[str, Any]]) -> None:
    """The plots of one figure share a legend: a series left unnamed takes the name another plot
    gave the same fill ("TCP", "Circle 50" printed once, beside the last panel)."""
    named = [(s_["colour"], s_["name"]) for p in plots for s_ in p.get("series", []) if s_.get("name")]
    for p in plots:
        for s_ in p.get("series", []):
            if not s_.get("name") and s_.get("colour") is not None:
                near = [(_dist(c, s_["colour"]), n) for c, n in named if _dist(c, s_["colour"]) <= SAME_COLOUR]
                if near and len({n for _, n in near}) == 1:
                    s_["name"] = near[0][1]


def unit_of(title: str) -> str | None:
    m = _UNIT.search(title)
    return m.group(1).strip() if m else None


# -- a plot -------------------------------------------------------------------------------------------------


def read_plot(img: np.ndarray, fg: np.ndarray, words: list[Word], yaxis: Line, xaxis: Line, ocr: "Ocr | None" = None, others: list[tuple[Line, Line]] = ()) -> dict[str, Any]:
    """One frame read: `{status, reason, kind, panel, bbox, y: {label, unit, scale, residual,
    ticks}, x: {...}, categories, series: [{name, colour, values: [{category|x, y, err_lo,
    err_hi}]}]}`."""
    out: dict[str, Any] = {"status": "unread", "reason": "", "kind": None, "panel": _panel(words, yaxis, xaxis), "words": "ocr" if ocr is not None else "text",
                           "bbox": [int(yaxis.mid), int(yaxis.a0), int(xaxis.a1), int(xaxis.mid)], "series": [], "categories": []}
    ys, ticks, why = y_scale(fg, words, yaxis, xaxis)
    if ys is None and ocr is not None and len(_ticks_marks(fg, yaxis, horizontal_axis=False, reach=12)) >= MIN_TICKS:
        # tick marks on the axis, and OCR missed their labels: the strip beside it read again alone
        strip = _strip_words(img, ocr, yaxis, xaxis)
        if strip:
            ys2, ticks2, why2 = y_scale(fg, strip + [w for w in words if not (w.x1 <= yaxis.mid + 3 and yaxis.a0 <= w.cy <= yaxis.a1)], yaxis, xaxis)
            if ys2 is not None:
                ys, ticks, why = ys2, ticks2, why2
    if ys is None:
        out["y"] = {"label": _y_title(words, ticks, yaxis) if ticks else "", "unit": None}
        out["reason"] = why
        return out
    title = clean_title(_y_title(words, ticks, yaxis, img, ocr))
    out["y"] = {"label": title, "unit": unit_of(title)}
    out["_words"] = words  # for the title, once every plot of the figure is known (read)
    out["y"].update({"scale": "log" if ys.log else "linear", "residual": round(ys.residual, 5), "ticks": [round(v, 6) for _, v in ys.ticks]})
    x0, x1 = yaxis.b1 + 1, xaxis.a1 + 1
    top, base = yaxis.a0, xaxis.b0
    inner = fg[top:base, x0:x1]
    k = max(_stroke(img, inner), int(round(0.015 * (x1 - x0))) | 1)  # wider than any error bar's stroke, at any resolution
    ebar_width = max(3, k - 1)
    xs, xticks = x_scale(fg, words, yaxis, xaxis)
    bars = [] if xs is not None else _bars(img, fg, x0, x1, top, base, k)  # bars stand over categories, markers over numbers
    taken = {id(w) for w in ticks + xticks}
    if bars:
        out["kind"] = "bar"
        # under the axis; a slanted label ends at its tick, so its right end is what lies over the plot
        under = [w for w in words if base - 2 <= w.y0 <= base + 0.3 * (base - top) and x0 - 0.05 * (x1 - x0) <= w.x1 and w.x0 <= x1 + 0.05 * (x1 - x0)
                 and id(w) not in taken and not _PANEL.match(w.text.strip())]
        if under:
            first = min(w.y0 for w in under)
            under = sorted((w for w in under if w.y0 <= first + 1.2 * float(np.median([w.h for w in under]))), key=lambda w: w.cx)
        # bars in groups: by their fills when they repeat (one bar of each series in a group); else
        # evenly spaced bars stand alone, and uneven gaps part groups at the wide ones
        groups: list[list[dict[str, Any]]] = []
        period = _period([b["colour"] for b in bars])
        gaps = [bars[i + 1]["x0"] - bars[i]["x1"] for i in range(len(bars) - 1)]
        if period > 1:
            groups = [bars[i : i + period] for i in range(0, len(bars), period)]
        elif not gaps or max(gaps) <= 1.5 * max(min(gaps), 1):
            groups = [[b] for b in bars]
        else:
            cut = (min(gaps) + max(gaps)) / 2
            groups = [[bars[0]]]
            for b, g in zip(bars[1:], gaps):
                if g > cut:
                    groups.append([b])
                else:
                    groups[-1].append(b)
        if len(under) != len(groups) and ocr is not None and under and all(w.w > w.h for w in under):
            under = _labels_apart(img, ocr, groups, under, x0, x1)
        if len(under) == len(groups):
            names = [w.text.strip() for w in under]
        elif len(under) == len(bars) and all(len(g) == 1 for g in groups):
            names = [w.text.strip() for w in under]
        else:
            names = [None] * len(groups)
            for g in groups:  # nearest label under the group's middle
                mid = (g[0]["x0"] + g[-1]["x1"]) / 2
                near = min(under, key=lambda w: abs(w.cx - mid), default=None)
                names[groups.index(g)] = near.text.strip() if near is not None and abs(near.cx - mid) <= (g[-1]["x1"] - g[0]["x0"]) else None
        out["categories"] = names
        def band(w: Word) -> bool:  # under an x axis, this plot's or another's: a category, not a legend
            return any(hz.mid - 2 <= w.y0 <= hz.mid + 0.3 * (hz.mid - v.a0) and v.mid - 0.05 * (hz.a1 - v.mid) <= w.x1 and w.x0 <= hz.a1
                       for v, hz in list(others) + [(yaxis, xaxis)])
        entries = legend(img, fg, words, {i for i, w in enumerate(words) if id(w) in taken or w in under or band(w)}, _near(yaxis, xaxis))
        width = max(len(g) for g in groups)
        series: dict[Any, dict[str, Any]] = {}
        for gi, g in enumerate(groups):
            for bi, b in enumerate(g):
                name = _name_of(b["colour"], entries) if width > 1 else None
                key = name if name is not None else (bi if width > 1 else 0)
                s = series.setdefault(key, {"name": name, "colour": list(b["colour"]), "values": []})
                up, down = _error_bars(img, fg, b, top, ebar_width)
                y = ys.value(b["top"])
                s["values"].append({"category": names[gi], "y": _round(y),
                                    "err_hi": _round(ys.value(up) - y) if up is not None else None,
                                    "err_lo": _round(y - ys.value(down)) if down is not None else None})
        out["series"] = list(series.values())
    else:
        marks = _markers(img, fg, x0, x1, top, base, k)
        if not marks:
            out["reason"] = "the scale holds, and no bar or marker was found in the plot"
            return out
        out["kind"] = "point"
        if xs is not None:
            under = []
            out["x"] = {"scale": "log" if xs.log else "linear", "residual": round(xs.residual, 5), "ticks": [round(v, 6) for _, v in xs.ticks]}
            below = [w for w in words if w.y0 > max(w2.y1 for w2 in xticks) and w.y0 <= base + 0.4 * (base - top) and x0 <= w.cx <= x1]
            out["x"]["label"] = " ".join(w.text.strip() for w in sorted(below, key=lambda w: w.x0))
            out["x"]["unit"] = unit_of(out["x"]["label"])
        entries = legend(img, fg, words, {i for i, w in enumerate(words) if id(w) in taken}, _near(yaxis, xaxis))
        labels = [w for w in words if id(w) not in taken and number(w.text) is None and len(w.text.strip()) >= 2 and w.h <= 1.2 * w.w]  # level words: a legend's
        marks = [m for m in marks if not any(abs(m["cy"] - w.cy) <= 0.7 * w.h and 0 <= w.x0 - m["cx"] <= 5 * w.h for w in labels)]  # a legend's own marker
        groups_by_colour: list[dict[str, Any]] = []
        for m in sorted(marks, key=lambda m: m["cx"]):
            g = next((g for g in groups_by_colour if _dist(g["colour"], m["colour"]) <= SAME_COLOUR), None)
            if g is None:
                g = {"name": _name_of(m["colour"], entries), "colour": list(m["colour"]), "values": []}
                groups_by_colour.append(g)
            y = ys.value(m["cy"])
            half = (m["y1"] - m["y0"]) / 2
            cx, cy = int(round(m["cx"])), int(round(m["cy"]))
            thin = max(3, int(0.4 * 2 * half))  # an error bar is thinner than the marker; its cap, as wide
            a, b_ = int(cy - half - 2), int(cy + half + 2)
            up = _thin_run(fg, cx, a, -1, a - top - 1, max_width=thin) if a > top else None
            down = _thin_run(fg, cx, b_, +1, base - b_ - 1, max_width=thin) if b_ < base else None
            g["values"].append({"x": _round(xs.value(m["cx"])) if xs is not None else None, "y": _round(y),
                                "err_hi": _round(ys.value(up) - y) if up is not None and a - up >= 2 else None,
                                "err_lo": _round(y - ys.value(down)) if down is not None and down - b_ >= 2 else None})
        out["series"] = groups_by_colour
    if not any(s["values"] for s in out["series"]):
        out["reason"] = "the scale holds, and every marker found was a legend's"
        out["series"] = []
        return out
    out["status"] = "read"
    return out


def _strip_words(img: np.ndarray, ocr: "Ocr", yaxis: Line, xaxis: Line) -> list[Word]:
    """The tick labels read again from the strip left of the y axis alone, padded and enlarged:
    small digits a reading of the whole figure missed."""
    import cv2

    width = xaxis.a1 - xaxis.a0
    x0 = int(max(yaxis.b0 - max(0.2 * width, 60), 0))
    y0, y1 = int(max(yaxis.a0 - 20, 0)), int(min(yaxis.a1 + 20, img.shape[0]))
    crop = np.ascontiguousarray(img[y0:y1, x0 : yaxis.b0])
    if crop.size == 0:
        return []
    pad, f = 16, 2
    big = cv2.resize(cv2.copyMakeBorder(crop, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(255, 255, 255)), None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    return [Word(x0 + w.x0 / f - pad, y0 + w.y0 / f - pad, x0 + w.x1 / f - pad, y0 + w.y1 / f - pad, w.text, w.score) for w in ocr(big)]


def _ocr_around(img: np.ndarray, ocr: "Ocr", yaxis: Line, xaxis: Line, most: int = 1600) -> list[Word]:
    """The words of one plot and its margins — its ticks, its titles — by OCR, the crop made no
    larger than `most` pixels a side."""
    import cv2

    w, h = xaxis.a1 - yaxis.mid, xaxis.mid - yaxis.a0
    x0, x1 = int(max(yaxis.mid - 0.45 * w, 0)), int(min(xaxis.a1 + 0.4 * w, img.shape[1]))  # a legend often stands to the right
    y0, y1 = int(max(yaxis.a0 - 0.12 * h, 0)), int(min(xaxis.mid + 0.35 * h, img.shape[0]))
    crop = np.ascontiguousarray(img[y0:y1, x0:x1])
    if crop.size == 0:
        return []
    f = min(1.0, most / max(crop.shape[:2]))
    if f < 1.0:
        crop = cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return [Word(x0 + w_.x0 / f, y0 + w_.y0 / f, x0 + w_.x1 / f, y0 + w_.y1 / f, w_.text, w_.score) for w_ in ocr(crop)]


def _period(colours: list[tuple[int, int, int]]) -> int:
    """The number of series in a grouped bar chart, read from its fills: the shortest run of
    colours, all different, that repeats to the end. 1 when they do not repeat."""
    n = len(colours)
    for p in range(2, min(7, n // 2) + 1):
        if n % p:
            continue
        head = colours[:p]
        if any(_dist(head[i], head[j]) <= SAME_COLOUR for i in range(p) for j in range(i + 1, p)):
            continue
        if all(_dist(colours[i], head[i % p]) <= SAME_COLOUR for i in range(n)):
            return p
    return 1


def _labels_apart(img: np.ndarray, ocr: "Ocr", groups: list[list[dict[str, Any]]], under: list[Word], x0: int, x1: int) -> list[Word]:
    """Category labels OCR ran together, read again one group at a time: the strip under the
    axis parted at its blank gaps, each run of glyphs given to the group it stands under (one
    spanning two groups cut between them), each group's read on its own, padded and enlarged."""
    import cv2

    y0, y1 = int(min(w.y0 for w in under)) - 2, int(max(w.y1 for w in under)) + 2
    h = max(1, y1 - y0)
    lo, hi = max(int(min(w.x0 for w in under)) - 4, 0), min(int(max(w.x1 for w in under)) + 4, img.shape[1])
    ink = foreground(img[max(y0, 0) : y1, lo:hi]).any(axis=0)
    gap = max(2, int(0.3 * h))
    runs: list[list[int]] = []
    for i, on in enumerate(ink):
        if on:
            if runs and i - runs[-1][1] <= gap:
                runs[-1][1] = i
            else:
                runs.append([i, i])
    centres = [(g[0]["x0"] + g[-1]["x1"]) / 2 - lo for g in groups]
    spans: list[list[float]] = [[] for _ in groups]
    for a, b in runs:
        inside = [j for j, c in enumerate(centres) if a <= c <= b]
        if len(inside) > 1:  # one run under two groups: cut it halfway between them
            cuts = [a] + [int((centres[j] + centres[j + 1]) / 2) for j in inside[:-1]] + [b]
            for j, (ca, cb) in zip(inside, zip(cuts, cuts[1:])):
                spans[j] += [ca, cb]
        else:
            j = min(range(len(centres)), key=lambda j: abs(centres[j] - (a + b) / 2))
            spans[j] += [a, b]
    out = []
    for j, sp in enumerate(spans):
        if not sp:
            return under
        a, b = int(min(sp)), int(max(sp)) + 1
        crop = cv2.copyMakeBorder(np.ascontiguousarray(img[max(y0, 0) : y1, lo + a : lo + b]), 16, 16, 16, 16, cv2.BORDER_CONSTANT, value=(255, 255, 255))
        crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        got = sorted((w for w in ocr(crop) if w.text.strip()), key=lambda w: w.x0)
        if not got:
            return under
        text = re.sub(r"\s*_\s*", "_", " ".join(w.text.strip() for w in got))  # "PLA _Dry": OCR's space before an underscore
        out.append(Word(lo + a, y0, lo + b, y1, text))
    return out


def _round(v: float) -> float:
    if v == 0 or not math.isfinite(v):
        return v
    return round(v, max(0, 4 - int(math.floor(math.log10(abs(v))))))


# -- a figure -----------------------------------------------------------------------------------------------


Ocr = Callable[[np.ndarray], list[Word]]


def ocr_words(img: np.ndarray) -> list[Word]:
    """The words on an image as RapidOCR reads them, with the models its package ships."""
    eng = _engine()
    r = eng(img)
    out = []
    for box, text, score in zip(r.boxes if r.boxes is not None else [], r.txts or [], r.scores or []):
        xs = [float(p[0]) for p in box]
        ys_ = [float(p[1]) for p in box]
        out.append(Word(min(xs), min(ys_), max(xs), max(ys_), str(text), float(score)))
    return out


_ENGINE: Any = None


def _engine() -> Any:
    global _ENGINE
    if _ENGINE is None:
        import logging

        from rapidocr import RapidOCR

        logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        _ENGINE = RapidOCR(params={"Global.log_level": "warning"})
    return _ENGINE


def read(img: np.ndarray, words: list[Word] | None = None, ocr: Ocr | None = None) -> list[dict[str, Any]]:
    """Every plot in a figure image (RGB, uint8), top to bottom and left to right. `words` are
    the figure's own text; without them, OCR (`ocr`, else RapidOCR) reads it. Given both, OCR
    reads only the plots the words cannot calibrate."""
    img = np.ascontiguousarray(img[..., :3])
    fg = foreground(img)
    found = frames(fg)
    if not found:
        return []
    used_ocr = None
    if words is None:
        used_ocr = ocr or ocr_words
        words = used_ocr(img)
    plots = [read_plot(img, fg, words, v, h, used_ocr, found) for v, h in found]
    if used_ocr is None and ocr is not None:
        # the given words (a PDF's text layer) have nothing beside some axis: a raster panel in a
        # vector figure; those plots are read again with OCR, of the plot and its margins alone
        plots = [read_plot(img, fg, _ocr_around(img, ocr, v, h) + words, v, h, ocr, found)
                 if p["reason"] == NOT_A_CHART and len(_ticks_marks(fg, v, horizontal_axis=False, reach=12)) >= MIN_TICKS else p
                 for p, (v, h) in zip(plots, found)]  # only an axis with tick marks is worth reading; a photograph's edge has none
    charts_ = [(v, h) for p, (v, h) in zip(plots, found) if p["reason"] != NOT_A_CHART]
    for p, (v, h) in zip(plots, found):
        ws = p.pop("_words", None)
        if ws is not None:
            _entitle(p, ws, v, h, charts_)
    plots = [p for p in plots if p["reason"] != NOT_A_CHART]  # a photograph's edges, a panel's border
    share_legend(plots)
    plots.sort(key=lambda p: (round(p["bbox"][1] / 50), p["bbox"][0]))
    return plots
