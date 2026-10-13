"""A paper's figures read into numbers, kept as rows.

Each picture node of a PDF paper is cut from its page (`crop`) and its charts read
(`charts.read`). How depends on how the figure was drawn:

- **Raster** — one embedded image covers it and no text is drawn over it: the region is
  rendered at the image's own resolution (at most `MAX_DPI`), and the words are read by OCR.
- **Vector**, or a mix — the region is rendered at `VECTOR_DPI`, and the words are the PDF's
  own text layer, character boxes joined into phrases; a plot those words cannot calibrate (a
  raster panel inside) is read again with OCR.

The rows are `charts`, one per plot found in a figure — read or unread, with the reason — and
`chart_values`, one per bar or point: its series, its category or x, its y, and the ends of its
error bar when they show. A plot's place on the page is kept beside the figure's node, so a
rebuild or a reparse that renumbers the nodes finds the figure again, or removes the plot whose
figure this reading has not (`remap`). Reading a paper again replaces its rows; `READER` names
the reader, and a paper read by this one is not read again unless asked.

What each figure a read looked for came to is a row too, `figure_outcomes`: plots, no plot, no
caption in the PDF, or skipped. A figure no read has a row for is told without one — no PDF,
no number, a number taken, too small, no place, not read (`outcomes`) — and a paper's figures
are counted from both (`states`), with SQL alone, for the papers, tree and charts ops.

Nothing leaves the machine: the PDF is rendered here, and OCR runs on the models RapidOCR's
package ships.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np

from . import charts
from .charts import Word

READER = "charts-1"
VECTOR_DPI = 600
MAX_DPI = 600
MIN_SIDE_PT = 72  # a picture smaller than an inch each way is a logo or an icon: not read
SAME_PLACE = 0.8  # how alike (`overlap`) a picture's box must be to the one a plot's figure had, to be that figure read again


def enabled() -> bool:
    """Figures are read as papers are, unless `LITRAG_FIGURES=off`."""
    import os

    return os.environ.get("LITRAG_FIGURES", "").strip().lower() not in ("off", "0", "false", "no")


# -- the figure, cut from its page -----------------------------------------------------------------------------


def _inside(b: tuple[float, float, float, float], box: tuple[float, float, float, float], slack: float = 2.0) -> bool:
    l, bt, r, t = b
    L, B, R, T = box
    return l >= L - slack and r <= R + slack and bt >= B - slack and t <= T + slack


def text_words(page: Any, box: tuple[float, float, float, float], scale: float) -> list[Word]:
    """The PDF's own words inside `box` (left, bottom, right, top in points), in the crop's
    pixels: characters joined into phrases along their baseline, at whatever angle they are set
    — level, on their side, slanted — and parted where a gap opens, the baseline moves or the
    next character starts behind the last. A smaller character raised off the baseline right
    after a number is its exponent: `10^3`."""
    import ctypes

    import pypdfium2.raw as raw

    L, B, R, T = box
    tp = page.get_textpage()
    x, y = ctypes.c_double(), ctypes.c_double()
    phrases: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for i in range(tp.count_chars()):
        ch = tp.get_text_range(i, 1)
        if ch in ("\r", "\n", "\x00", "\ufffe", "\x02"):
            continue  # pdfium's guess at a line break: the geometry below decides
        l, b, r, t = tp.get_charbox(i)
        if not _inside((l, b, r, t), box, slack=1.0):
            cur = None
            continue
        if ch == " ":
            if cur is not None:
                cur["space"] = True
            continue
        a = raw.FPDFText_GetCharAngle(tp.raw, i)
        a = 0.0 if a < 0 else float(a)
        m = raw.FS_MATRIX()
        raw.FPDFText_GetMatrix(tp.raw, i, ctypes.byref(m))
        # the size the glyph is drawn at: some PDFs set every font at 1 point and scale it by the matrix
        fs = float(raw.FPDFText_GetFontSize(tp.raw, i)) * (math.sqrt(abs(m.a * m.d - m.b * m.c)) or 1.0)
        if fs <= 0:
            fs = max(t - b, r - l)
        raw.FPDFText_GetCharOrigin(tp.raw, i, ctypes.byref(x), ctypes.byref(y))
        ox, oy = x.value, y.value
        d = (math.cos(a), -math.sin(a))  # along the baseline (pdfium turns its angles clockwise)
        n = (-d[1], d[0])  # up from it
        corners = [(l, b), (l, t), (r, b), (r, t)]
        start, end_ = min(cx * d[0] + cy * d[1] for cx, cy in corners), max(cx * d[0] + cy * d[1] for cx, cy in corners)
        if cur is not None and abs(math.remainder(a - cur["a"], 2 * math.pi)) < 0.15:
            size = max(cur["fs"], fs)
            gap = start - cur["end"]
            lift = (ox - cur["ox"]) * n[0] + (oy - cur["oy"]) * n[1]
            limit = (0.6 if cur["space"] else 0.35) * size + 0.5
            if abs(math.remainder(a, math.pi / 2)) > 0.1:
                # slanted: boxes square to the page overlap, so the step from one origin to the next
                # decides — a glyph's advance, a space's more, and never backwards
                step = (ox - cur["ox"]) * d[0] + (oy - cur["oy"]) * d[1]
                gap = 0.0 if 0.15 * cur["fs"] <= step <= (1.7 if cur["space"] else 1.25) * cur["fs"] else 10 * size
            if abs(lift) <= 0.25 * size and -0.2 * size <= gap <= limit:
                kind = "same"
            elif fs < 0.85 * cur["fs"] and 0.15 * cur["fs"] <= lift <= 0.8 * cur["fs"] and -0.2 * size <= gap <= 0.3 * size and cur["text"][-1:].isdigit():
                kind = "power"
            else:
                kind = None
            if kind is not None:
                cur["text"] += ("^" if kind == "power" and not cur["raised"] else (" " if cur["space"] else "")) + ch
                cur["raised"] = kind == "power"
                cur["l"], cur["r"], cur["b"], cur["t"] = min(cur["l"], l), max(cur["r"], r), min(cur["b"], b), max(cur["t"], t)
                cur["end"], cur["space"] = max(cur["end"], end_), False
                if kind == "same":
                    cur["ox"], cur["oy"], cur["fs"] = ox, oy, fs
                continue
        cur = {"text": ch, "l": l, "r": r, "b": b, "t": t, "a": a, "fs": fs, "ox": ox, "oy": oy, "end": end_, "space": False, "raised": False}
        phrases.append(cur)
    tp.close()
    out = []
    for p in phrases:
        if p["text"].strip():
            out.append(Word((p["l"] - L) * scale, (T - p["t"]) * scale, (p["r"] - L) * scale, (T - p["b"]) * scale, p["text"].strip()))
    return out


def crop(pdf: Any, page_no: int, bbox: tuple[float, float, float, float]) -> tuple[np.ndarray, list[Word] | None, str, float]:
    """A figure from its page: (RGB pixels, the text layer's words or None for OCR, `raster` |
    `vector`, pixels per point). `bbox` is the node's (left, top, right, bottom) from the top
    of the page."""
    import pypdfium2.raw as raw

    page = pdf[page_no - 1]
    w, h = page.get_size()
    l, t, r, b = bbox
    box = (l, h - b, r, h - t)  # left, bottom, right, top from the bottom
    images, texts, paths = [], 0, 0
    for o in page.get_objects(max_depth=4):
        ob = o.get_bounds()
        if not _inside(ob, box):
            continue
        if o.type == raw.FPDF_PAGEOBJ_IMAGE:
            images.append((o, ob))
        elif o.type == raw.FPDF_PAGEOBJ_TEXT:
            texts += 1
        elif o.type == raw.FPDF_PAGEOBJ_PATH:
            paths += 1
    area = max((r - l) * (b - t), 1.0)
    big = [(o, ob) for o, ob in images if (ob[2] - ob[0]) * (ob[3] - ob[1]) >= 0.8 * area]
    if big and texts == 0:
        o, ob = big[0]
        try:
            px_w, _ = o.get_px_size()
            dpi = min(MAX_DPI, max(150.0, px_w / max((ob[2] - ob[0]) / 72, 1e-6)))
        except Exception:  # noqa: BLE001 — an image pdfium cannot size: render it as a page would be
            dpi = 300.0
        scale, source, words = dpi / 72, "raster", None
    else:
        scale, source = VECTOR_DPI / 72, "vector"
        words = text_words(page, box, scale)
    img = page.render(scale=scale, crop=(l, h - b, w - r, t)).to_pil().convert("RGB")
    page.close()
    return np.asarray(img), words, source, scale


# -- the rows -------------------------------------------------------------------------------------------------


def ensure_schema(conn: sqlite3.Connection) -> None:
    """The figures' tables, made on the first call that needs them; a call that finds them made
    writes nothing (the papers op calls it, beside the ingest thread's writes)."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('charts', 'chart_values', 'figure_reads', 'figure_outcomes')")}
    if len(tables) == 4 and {"pages", "pages_read"} <= {r[1] for r in conn.execute("PRAGMA table_info(figure_reads)")}:
        conn.commit()  # as the script would: what is pending is committed before the caller's writes
        return
    conn.executescript(
        """
CREATE TABLE IF NOT EXISTS charts (
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  figure TEXT NOT NULL,             -- the picture node it was read from
  figure_label TEXT,                -- "2" or "Scheme 2" when the plot was pinned to "Figure 2" or "Scheme 2" by its caption (an XML paper's PDF)
  plot INTEGER NOT NULL,            -- its plots, top to bottom, left to right
  page INTEGER, bbox TEXT,          -- the figure's place: page, [left, top, right, bottom] in points from the top
  panel TEXT,                       -- the letter printed beside it, when one is
  title TEXT,                       -- the words printed over it ("Day 1", "Pore Size")
  kind TEXT,                        -- bar | point
  status TEXT NOT NULL,             -- read | unread
  reason TEXT,                      -- why it is unread
  source TEXT NOT NULL,             -- vector: the PDF's drawing and its text; raster: an image and OCR
  y_label TEXT, y_unit TEXT, y_scale TEXT,
  x_label TEXT, x_unit TEXT, x_scale TEXT,
  residual REAL,                    -- how far the y ticks stray from the scale fitted to them, of its range
  ticks TEXT,                       -- JSON: the y tick values the scale was fitted to
  categories TEXT,                  -- JSON: the category labels under a bar chart, in order
  plot_box TEXT,                    -- JSON [x0, y0, x1, y1]: the frame in the figure, in points from its top left
  reader TEXT NOT NULL,
  at TEXT NOT NULL,
  PRIMARY KEY(paper, figure, plot)
);
CREATE TABLE IF NOT EXISTS chart_values (
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  figure TEXT NOT NULL,
  plot INTEGER NOT NULL,
  series INTEGER NOT NULL,          -- its series, in the order they were met
  name TEXT,                        -- the series' name from the legend, when there is one
  colour TEXT,                      -- its fill, #rrggbb
  ordinal INTEGER NOT NULL,         -- the value's place in its series
  category TEXT,                    -- a bar's category
  x REAL,                           -- a point's x
  y REAL NOT NULL,
  err_lo REAL, err_hi REAL,         -- the error bar's reach below and above y; NULL when it does not show
  PRIMARY KEY(paper, figure, plot, series, ordinal)
);
CREATE TABLE IF NOT EXISTS figure_reads (
  paper TEXT PRIMARY KEY REFERENCES papers(key) ON DELETE CASCADE,
  reader TEXT NOT NULL, figures INTEGER NOT NULL, plots INTEGER NOT NULL, read INTEGER NOT NULL, values_ INTEGER NOT NULL,
  seconds REAL, at TEXT NOT NULL,
  file TEXT,                        -- the PDF read: the paper's own, or an XML paper's figures_file
  unmatched INTEGER,                -- plots in that PDF pinned to no figure of the paper
  pages INTEGER,                    -- the pages of that PDF; NULL when not counted
  pages_read INTEGER                -- an XML paper's: the pages printing a caption of one of its floats, the only ones looked at
);
CREATE TABLE IF NOT EXISTS figure_outcomes (
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  figure TEXT NOT NULL,             -- one row per figure the last read looked for: a PDF paper's picture, an XML paper's float by its key
  figure_label TEXT,                -- an XML paper's key looked for, "2" or "Scheme 1"; NULL for a PDF paper's picture
  outcome TEXT NOT NULL,            -- plots | no-plot | no-caption | skipped (how many plots, and read: `charts`)
  page INTEGER,                     -- a picture's page; for an XML paper's float, the PDF's page its plots are on, or its caption
  bbox TEXT,                        -- a picture's [left, top, right, bottom] in points from the top
  detail TEXT,                      -- why it was skipped
  PRIMARY KEY(paper, figure)
);
"""
    )
    have = {r[1] for r in conn.execute("PRAGMA table_info(figure_reads)")}
    for col in ("pages", "pages_read"):
        if col not in have:  # a store made before the pages were counted
            conn.execute(f"ALTER TABLE figure_reads ADD COLUMN {col} INTEGER")
    conn.commit()


def _hex(c: list[int] | tuple[int, ...]) -> str:
    return "#" + "".join(f"{int(v):02x}" for v in c[:3])


def pictures(conn: sqlite3.Connection, key: str) -> list[sqlite3.Row]:
    """The paper's pictures with a place on a page, large enough to hold a chart."""
    rows = conn.execute(
        "SELECT node_id, page, bbox_l, bbox_t, bbox_r, bbox_b FROM nodes WHERE paper = ? AND type IN ('picture', 'chart') AND page IS NOT NULL AND bbox_l IS NOT NULL ORDER BY rowid",
        (key,),
    ).fetchall()
    return [r for r in rows if r["bbox_r"] - r["bbox_l"] >= MIN_SIDE_PT and abs(r["bbox_b"] - r["bbox_t"]) >= MIN_SIDE_PT]


_CHART_COLS = ("paper", "figure", "figure_label", "plot", "page", "bbox", "panel", "title", "kind", "status", "reason", "source", "y_label", "y_unit", "y_scale",
               "x_label", "x_unit", "x_scale", "residual", "ticks", "categories", "plot_box", "reader", "at")


def _rows(key: str, figure: str, label: str | None, j: int, page: int, bbox: list[float], p: dict[str, Any], source: str, scale: float, stamp: str) -> tuple[dict[str, Any], list[tuple[Any, ...]]]:
    """One plot as its `charts` row and its `chart_values` rows."""
    y, x = p.get("y") or {}, p.get("x") or {}
    if p.get("words") == "ocr":
        source = "raster"  # read from an image inside the figure, whatever drew the rest
    row = {"paper": key, "figure": figure, "figure_label": label, "plot": j, "page": page, "bbox": json.dumps([round(v, 2) for v in bbox]),
           "panel": p.get("panel"), "title": p.get("title") or None, "kind": p.get("kind"), "status": p["status"], "reason": p.get("reason") or None,
           "source": source, "y_label": y.get("label") or None, "y_unit": y.get("unit"), "y_scale": y.get("scale"),
           "x_label": x.get("label") or None, "x_unit": x.get("unit"), "x_scale": x.get("scale"), "residual": y.get("residual"),
           "ticks": json.dumps(y.get("ticks") or []), "categories": json.dumps(p.get("categories") or []),
           "plot_box": json.dumps([round(v / scale, 2) for v in p["bbox"]]), "reader": READER, "at": stamp}
    values = []
    if p["status"] == "read":
        for si, s_ in enumerate(p["series"]):
            for vi, v in enumerate(s_["values"]):
                values.append((key, figure, j, si, s_.get("name"), _hex(s_.get("colour") or [0, 0, 0]), vi, v.get("category"), v.get("x"), v["y"], v.get("err_lo"), v.get("err_hi")))
    return row, values


def _store(conn: sqlite3.Connection, key: str, rows: list[dict[str, Any]], values: list[tuple[Any, ...]], figures_n: int, seconds: float, stamp: str, file: str, unmatched: int = 0,
           outcomes: list[tuple[Any, ...]] | None = None, pages: int | None = None, pages_read: int | None = None) -> dict[str, Any]:
    """A paper's chart rows in place of the ones it had, and what each figure looked for came to
    (`outcomes`: figure, figure_label, outcome, page, bbox, detail)."""
    n_read = sum(r["status"] == "read" for r in rows)
    with conn:
        conn.execute("DELETE FROM chart_values WHERE paper = ?", (key,))
        conn.execute("DELETE FROM charts WHERE paper = ?", (key,))
        conn.execute("DELETE FROM figure_outcomes WHERE paper = ?", (key,))
        conn.executemany(f"INSERT INTO charts({', '.join(_CHART_COLS)}) VALUES ({', '.join('?' * len(_CHART_COLS))})", [tuple(r[c] for c in _CHART_COLS) for r in rows])
        conn.executemany(f"INSERT INTO chart_values VALUES ({', '.join('?' * 12)})", values)
        conn.executemany("INSERT INTO figure_outcomes(paper, figure, figure_label, outcome, page, bbox, detail) VALUES (?,?,?,?,?,?,?)", [(key, *o) for o in outcomes or []])
        conn.execute("INSERT OR REPLACE INTO figure_reads(paper, reader, figures, plots, read, values_, seconds, at, file, unmatched, pages, pages_read) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (key, READER, figures_n, len(rows), n_read, len(values), seconds, stamp, file, unmatched, pages, pages_read))
    return {"figures": figures_n, "plots": len(rows), "read": n_read, "values": len(values), "seconds": seconds, "unmatched": unmatched, "pages": pages, "pages_read": pages_read}


def _box(f: sqlite3.Row) -> tuple[float, float, float, float]:
    """A picture node's box, (left, top, right, bottom) from the top of the page whichever way its rows give it."""
    return (f["bbox_l"], min(f["bbox_t"], f["bbox_b"]), f["bbox_r"], max(f["bbox_t"], f["bbox_b"]))


def _place(box: tuple[float, ...] | list[float]) -> str:
    return json.dumps([round(v, 2) for v in box])


def read_paper(conn: sqlite3.Connection, key: str, pdf_path: Path, *, ocr: charts.Ocr | None = None, on_figure: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Every figure of a PDF paper read, its rows replacing the ones it had: `{figures, plots,
    read, values, seconds}`; and each picture's outcome, plots or none — or skipped, and why, a
    picture on a page the PDF has not."""
    import pypdfium2 as pdfium

    from .library import now_iso

    ensure_schema(conn)
    t0 = time.time()
    figs = pictures(conn, key)
    stamp = now_iso()
    rows: list[dict[str, Any]] = []
    values: list[tuple[Any, ...]] = []
    outcomes: list[tuple[Any, ...]] = []
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        n_pages = len(pdf)
        for i, f in enumerate(figs):
            if on_figure:
                on_figure(i, len(figs))
            bbox = _box(f)
            if f["page"] > n_pages:
                outcomes.append((f["node_id"], None, "skipped", f["page"], _place(bbox), f"page {f['page']} is past the end of {pdf_path.name}"))
                continue
            img, words, source, scale = crop(pdf, f["page"], bbox)
            plots = charts.read(img, words, ocr=ocr or charts.ocr_words)
            for j, p in enumerate(plots):
                row, vals = _rows(key, f["node_id"], None, j, f["page"], list(bbox), p, source, scale, stamp)
                rows.append(row)
                values += vals
            outcomes.append((f["node_id"], None, "plots" if plots else "no-plot", f["page"], _place(bbox), None))
    finally:
        pdf.close()
    return _store(conn, key, rows, values, len(figs), round(time.time() - t0, 2), stamp, pdf_path.name, outcomes=outcomes, pages=n_pages)


# -- an XML paper's figures, from a PDF of it ------------------------------------------------------------------

PAGE_DPI = 300
_KINDS = {"s": "Scheme ", "c": "Chart "}  # what a key puts before the number, by the first letter of its caption's first word; a figure's, nothing
_LABEL = re.compile(r"^\s*(fig(?:ure)?s?|scheme|chart)\s*\.?\s*(S?\d+)(?![\d.]*\d)", re.I)


def figure_key(kind: str, number: str) -> str:
    """The key a caption gives its float: a figure its number alone, "2" or "S2"; a scheme or a
    chart its kind and number, "Scheme 2", "Chart 2", so that neither takes a figure's number. A
    figure key is one with no kind word. The kind is told by its first letter, as edges.py tells
    it, so every word the patterns take has a key ("Fıgure" and "ſcheme" too, which re.I matches).
    (edges.py keys the same scheme "scheme 2", and no chart.)"""
    return _KINDS.get(kind[:1].casefold(), "") + number.upper()


def _figure_rows(conn: sqlite3.Connection, keys: list[str] | None = None) -> list[sqlite3.Row]:
    """The picture and chart nodes of the papers (`keys`, or every paper), in reading order, each
    with its captions' text and its place."""
    where = f"AND p.paper IN ({', '.join('?' * len(keys))})" if keys is not None else ""
    return conn.execute(
        "SELECT p.paper, p.node_id, p.text, p.page, p.bbox_l, p.bbox_t, p.bbox_r, p.bbox_b, "
        "(SELECT group_concat(c.text, ' ') FROM nodes c WHERE c.parent = p.node_id AND c.type = 'caption') AS cap "
        f"FROM nodes p WHERE p.type IN ('picture', 'chart') {where} ORDER BY p.rowid",
        list(keys or []),
    ).fetchall()


def _key_of(r: sqlite3.Row) -> str | None:
    m = _LABEL.match(r["cap"] or r["text"] or "")
    return figure_key(m.group(1), m.group(2)) if m else None


def _labelled(rows: list[sqlite3.Row]) -> dict[str, tuple[str | None, bool]]:
    out: dict[str, tuple[str | None, bool]] = {}
    seen: set[str] = set()
    for r in rows:
        k = _key_of(r)
        out[r["node_id"]] = (k, k is not None and k not in seen)  # the first to give a key keeps it
        if k is not None:
            seen.add(k)
    return out


def figure_labels(conn: sqlite3.Connection, key: str) -> dict[str, tuple[str | None, bool]]:
    """Each picture or chart node of the paper by the key its caption gives it (`figure_key`) —
    None when it gives no number — and whether it is the first to give that key: one that repeats
    an earlier caption's kind and number is no float a PDF's captions can be matched to, the
    earlier one is."""
    return _labelled(_figure_rows(conn, [key]))


def is_figure_key(k: str | None) -> bool:
    """A figure's key, "2" or "S2": not a scheme's or a chart's."""
    return k is not None and " " not in k


def figure_numbers(conn: sqlite3.Connection, key: str) -> dict[str, str]:
    """The keys looked for in a PDF of the paper: its figures, schemes and charts by the key their
    captions give them (`figure_key`), {"2": picture node, "Scheme 1": picture node}."""
    return {k: node for node, (k, first) in figure_labels(conn, key).items() if first and k is not None}


_CAPTION_UPPER = re.compile(r"^\s*(FIGURE|FIG\.|SCHEME|CHART)\s*(S?\d+)(?![\d.]*\d)")  # set in capitals: a caption, punctuated or not
_CAPTION_DOTTED = re.compile(r"^\s*(fig(?:ure)?|scheme|chart)\s*\.?\s*(S?\d+)\s*[.:|](?!\d)", re.I)  # "Figure 2." "Fig. 2:" "Figure 2 |" "Scheme 1."


def captions(words: list[Word]) -> list[tuple[str, Word]]:
    """The captions on a page by their keys, ("2" or "Scheme 1", the caption's first line): a
    phrase that opens the way a caption does — "FIGURE 2", "Fig. 2.", "Figure 2:", "Scheme 1." —
    not a sentence that begins "Figure 2 shows"; its extent the whole of its line, which the text
    layer gives in pieces."""
    out = []
    for w in words:
        m = _CAPTION_UPPER.match(w.text) or _CAPTION_DOTTED.match(w.text)  # not "Figure 2 shows": a sentence
        if not m or w.w < w.h:
            continue
        num = figure_key(m.group(1), m.group(2))
        x1 = w.x1
        for o in sorted((o for o in words if o is not w and abs(o.cy - w.cy) <= 0.5 * w.h and o.x0 >= w.x0), key=lambda o: o.x0):
            if o.x0 - x1 > 3 * w.h:
                break
            x1 = max(x1, o.x1)
        out.append((num, Word(w.x0, w.y0, x1, w.y1, w.text)))
    return out


def owner(box: list[float], caps: list[tuple[str, Word]]) -> str | None:
    """The caption a plot belongs to: the nearest below it, else beside it (a caption set in the
    margin), else above it — in that order of preference."""
    x0, y0, x1, y1 = box
    ranked: list[tuple[int, float, str]] = []
    for num, w in caps:
        across = min(x1, w.x1) - max(x0, w.x0) > 0
        if w.y0 >= y1 - 5:  # below: the usual place
            ranked.append((0 if across else 3, w.y0 - y1, num))
        elif w.y0 < y1 and w.y1 > y0:  # beside: a caption set in the margin
            ranked.append((1, min(abs(w.x0 - x1), abs(x0 - w.x1)), num))
        elif across:  # above
            ranked.append((2, y0 - w.y1, num))
    return min(ranked)[2] if ranked else None


def claimed(box: list[float], caps: list[tuple[str, Word]], numbers: dict[str, str]) -> str | None:
    """The key a plot is pinned to: that of its caption, which `owner` picks from every caption on
    the page, when the paper has a float by it; else None, and the plot is counted, not kept. A
    caption the paper has no float for (one the XML lost) claims its plots all the same, and never
    hands them on to the next caption that is the paper's."""
    num = owner(box, caps)
    return num if num is not None and num in numbers else None


def _images(page: Any, w: float, h: float) -> list[tuple[Any, tuple[float, float, float, float]]]:
    """The images on a page large enough to be a figure: (object, its box from the top left, in points)."""
    import pypdfium2.raw as raw

    out = []
    for o in page.get_objects(max_depth=4):
        if o.type != raw.FPDF_PAGEOBJ_IMAGE:
            continue
        l, b, r, t = o.get_bounds()
        if r - l >= MIN_SIDE_PT and t - b >= MIN_SIDE_PT:
            out.append((o, (l, h - t, r, h - b)))
    return out


def caption_pages(pdf: Any, numbers: dict[str, str], on_page: Callable[[int, int], None] | None = None) -> Iterator[tuple[int, Any, list[Word], list[tuple[str, Word]]]]:
    """The pages of an open PDF that print the caption of one of the paper's floats (`numbers`),
    as (page index, the page, its words in points from the top left, every caption on it — the
    paper's or not: each claims what it sits under). Its text layer alone decides: no page is
    rendered. A page printing none is closed and passed by; the caller closes each page given."""
    for pno in range(len(pdf)):
        if on_page:
            on_page(pno, len(pdf))
        page = pdf[pno]
        w, h = page.get_size()
        words = text_words(page, (0, 0, w, h), 1.0)
        caps = captions(words)
        if not any(n in numbers for n, _ in caps):
            page.close()
            continue
        yield pno, page, words, caps


def _xml_outcomes(numbers: dict[str, str], plotted: dict[str, int], found: dict[str, int]) -> list[tuple[Any, ...]]:
    """An XML paper's outcome rows, one per key looked for: plots pinned to it (on the first page
    they are), its caption found and nothing pinned (on the page it is), or no caption printed."""
    return [(node, k, "plots", plotted[k], None, None) if k in plotted else (node, k, "no-plot", found[k], None, None) if k in found else (node, k, "no-caption", None, None, None)
            for k, node in numbers.items()]


def read_pages(conn: sqlite3.Connection, key: str, pdf_path: Path, *, ocr: charts.Ocr | None = None, on_page: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """An XML paper's figures read from a PDF of it, on each page that prints the caption of one
    of its figures, schemes or charts (`caption_pages`): an image there large enough to be a
    figure is read whole, at its own resolution, with OCR, and pinned with all its plots to the
    caption it sits over ("Figure 2", so to the XML's figure 2; "Scheme 1" to its scheme 1); the
    rest of the page, the images blanked, is read at PAGE_DPI with its own text layer, and each
    plot drawn there pinned the same way. A plot under no caption, or under one the paper has no
    float for, is counted, not kept (`claimed`). Each key looked for comes to plots, no plot under
    a caption found, or no caption printed; with none to look for the PDF is opened only to count
    its pages."""
    import pypdfium2 as pdfium

    from .library import now_iso

    ensure_schema(conn)
    t0 = time.time()
    numbers = figure_numbers(conn, key)
    stamp = now_iso()
    rows: list[dict[str, Any]] = []
    values: list[tuple[Any, ...]] = []
    unmatched = 0
    count: dict[str, int] = {}
    found: dict[str, int] = {}
    pages_read = 0
    ocr = ocr or charts.ocr_words

    def keep(num: str, plots: list[tuple[dict[str, Any], list[float], str, float]], pno: int) -> None:
        charts.share_legend([p for p, _, _, _ in plots])  # one figure's panels share its legend
        for p, box, source, scale in plots:
            j = count.get(num, 0)
            count[num] = j + 1
            row, vals = _rows(key, numbers[num], num, j, pno + 1, box, p, source, scale, stamp)
            rows.append(row)
            values.extend(vals)

    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        n_pages = len(pdf)
        for pno, page, words_pt, caps in (caption_pages(pdf, numbers, on_page) if numbers else ()):
            pages_read += 1
            for n, _ in caps:
                if n in numbers:
                    found.setdefault(n, pno + 1)
            w, h = page.get_size()
            # the images: each a figure of its own, read whole
            images = _images(page, w, h)
            for _, box in images:
                num = claimed(list(box), caps, numbers)
                img, _, _, scale = crop(pdf, pno + 1, box)
                plots = charts.read(img, None, ocr=ocr)
                if num is None:
                    unmatched += len(plots)
                    continue
                keep(num, [(p, [box[0] + v / scale if i % 2 == 0 else box[1] + v / scale for i, v in enumerate(p["bbox"])], "raster", scale) for p in plots], pno)
            # the rest of the page: what is drawn, read with the page's own words
            scale = PAGE_DPI / 72
            img = np.array(page.render(scale=scale).to_pil().convert("RGB"))
            page.close()
            for _, (l, t, r, b) in images:
                img[int(t * scale) : int(b * scale) + 1, int(l * scale) : int(r * scale) + 1] = 255
            words_px = [Word(x.x0 * scale, x.y0 * scale, x.x1 * scale, x.y1 * scale, x.text) for x in words_pt]
            by_fig: dict[str, list[tuple[dict[str, Any], list[float], str, float]]] = {}
            for p in charts.read(img, words_px, ocr=None):
                box = [v / scale for v in p["bbox"]]
                num = claimed(box, caps, numbers)
                if num is None:
                    unmatched += 1
                else:
                    by_fig.setdefault(num, []).append((p, box, "vector", scale))
            for num, plots in by_fig.items():
                keep(num, plots, pno)
    finally:
        pdf.close()
    plotted: dict[str, int] = {}
    for r in rows:
        plotted[r["figure_label"]] = min(plotted.get(r["figure_label"], r["page"]), r["page"])
    return _store(conn, key, rows, values, len(numbers), round(time.time() - t0, 2), stamp, pdf_path.name, unmatched,
                  outcomes=_xml_outcomes(numbers, plotted, found), pages=n_pages, pages_read=pages_read)


def read_for(conn: sqlite3.Connection, key: str, papers_dir: Path, **kw: Any) -> dict[str, Any] | None:
    """A paper's figures read from whatever draws them: its own PDF, or the PDF kept beside its
    XML; None when it has neither."""
    row = conn.execute("SELECT format, file, figures_file FROM papers WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    if row["format"] == "pdf" and row["file"] and (papers_dir / row["file"]).exists():
        return read_paper(conn, key, papers_dir / row["file"], **{k: v for k, v in kw.items() if k in ("ocr", "on_figure")})
    if row["figures_file"] and (papers_dir / row["figures_file"]).exists():
        return read_pages(conn, key, papers_dir / row["figures_file"], **{k: v for k, v in kw.items() if k in ("ocr", "on_page")})
    return None


def caption_numbers(pdf_path: Path) -> set[str]:
    """The keys of the captions a PDF prints, every page's ("2", "S2", "Scheme 1"): its text layer
    read as `read_pages` reads it for `captions`, and nothing more — no page rendered, no OCR, a
    hundredth of a second a page. A PDF pdfium will not open prints none."""
    from .recover import open_pdf

    out: set[str] = set()
    try:
        with open_pdf(pdf_path) as pdf:
            for pno in range(len(pdf)):
                page = pdf[pno]
                w, h = page.get_size()
                out.update(num for num, _ in captions(text_words(page, (0, 0, w, h), 1.0)))
    except Exception:  # noqa: BLE001 — a PDF that cannot be read prints what was read of it
        pass
    return out


def _set_aside(papers_dir: Path, safe: str, path: Path) -> str:
    """A PDF moved out of the way, never deleted: to the first free `<safe>.figures.<n>.pdf`."""
    import os

    n = 1
    while (papers_dir / f"{safe}.figures.{n}.pdf").exists():
        n += 1
    os.replace(path, papers_dir / f"{safe}.figures.{n}.pdf")
    return f"{safe}.figures.{n}.pdf"


def keep_beside(conn: sqlite3.Connection, papers_dir: Path, key: str, src: Path, now: str, *, came: str | None = None) -> dict[str, Any]:
    """A PDF that arrived for an XML paper's figures, kept beside it as `<safe>.figures.pdf` — or
    not, and why: `{kept, same, file, aside, why, captions, figures}`. The one gate every such PDF
    passes, however it came:

    1. The paper must be one read from XML.
    2. The PDF kept already, on disk and in the same bytes, is kept (`same`): nothing is copied,
       and nothing is read again. Only a stub of those bytes goes, as in step 5: kept here, then
       filed again as a paper of its own, since a kept file's hash is not where filing looks.
    3. Its first pages must name the paper (acquire.names_the_paper): its DOI, or its whole title
       not under a supplement's name. Unassignable beats misassigned.
    4. A PDF kept already gives way only to one whose pages print the captions of strictly more of
       the paper's figures (`captions` of its `figures`, figure keys only), and is set aside as
       the first free `<safe>.figures.<n>.pdf` (`aside`), never deleted. A kept file no longer on
       disk counts as none.
    5. The PDF is copied in through a temporary file, so no reader meets half of it, and the
       paper's `figures_file` set; PMC's answer that it had none to give (`figures_note`) goes,
       since a PDF draws the figures now. The last read of the paper's figures goes when it names
       the file it read: that was another file, whatever it was named, and the figures op reads
       this one (a read that names none, a merge's copy, is left to its caller). The same bytes
       filed before as a paper of their own, which only their hash could meet (no DOI, no PMID),
       were a stub of this paper, and go with their rows, as filing drops one (store.file_paper).
    6. A `filed` row says so — the file's name, or how it `came` when it has no name of its own
       (worker._ask_pmc: "from the PMC Cloud Service") — with how many of the paper's figures its
       pages caption, or that there is no reading to count them in yet: a merge keeps a PDF for a
       paper its rebuild has still to read (projects.merge).

    A refusal writes nothing, not even a row: the caller says it, as the arrival it was. The off
    switch is not looked at: keeping a file is not reading it. (Two PDFs are kept without passing
    here. An XML reread over a paper read from its PDF keeps that PDF, worker._format_changed: it
    is the paper's own file, filed under the paper's own identifier. A merge copies a paper new to
    its target with the PDF its source keeps beside it, and that PDF's reads, projects.merge: a
    copy of what the source admitted, not an arrival. A merge's PDF for a paper its target holds
    already does pass here.)"""
    import os
    import shutil

    from .acquire import first_pages, names_the_paper
    from .library import safe_key
    from .store import log_event, sha256_of

    out: dict[str, Any] = {"kept": False, "same": False, "file": None, "aside": None, "why": None, "captions": None, "figures": None}
    row = conn.execute("SELECT title, doi, format, figures_file FROM papers WHERE key = ?", (key,)).fetchone()
    if row is None:
        return {**out, "why": "no such paper"}
    if row["format"] != "jats":
        return {**out, "why": "not a paper read from XML"}
    held = papers_dir / row["figures_file"] if row["figures_file"] else None
    if held is not None and not held.is_file():
        held = None  # kept once, gone since: none, and nothing to set aside
    if held is not None and sha256_of(held) == sha256_of(src):
        stub = _drop_stub(conn, key, held)
        if stub is not None:
            log_event(conn, key, now, "filed", f"the PDF kept for its figures, {held.name}, again: {src.name}; {stub}, the same bytes filed before for want of an identifier, goes with its rows")
        return {**out, "kept": True, "same": True, "file": held.name}
    why = names_the_paper(first_pages(src), {"title": row["title"], "doi": row["doi"]})
    if why:
        return {**out, "why": why}
    numbers = figure_numbers(conn, key)
    wanted = {k for k in numbers if " " not in k}  # "2" and "S2", not "Scheme 1"
    printed = len(caption_numbers(src) & wanted)
    out.update(captions=printed, figures=len(wanted))
    if held is not None:
        before = len(caption_numbers(held) & wanted)
        if printed <= before:
            return {**out, "why": f"prints the captions of {printed} of its {len(wanted)} figure{'s' if len(wanted) != 1 else ''}; the PDF kept, {held.name}, prints {before}"}
    safe = safe_key(key)
    dest = papers_dir / f"{safe}.figures.pdf"
    tmp = dest.with_name(dest.name + ".part")
    shutil.copy2(src, tmp)
    moved: list[tuple[str, str]] = []
    if held is not None:
        moved.append((held.name, _set_aside(papers_dir, safe, held)))
    if dest.exists() and dest.resolve() != src.resolve():
        moved.append((dest.name, _set_aside(papers_dir, safe, dest)))  # a file of that name the paper no longer keeps: aside too, never overwritten
    os.replace(tmp, dest)
    ensure_schema(conn)  # before the writes: it commits what is pending
    conn.execute("UPDATE papers SET figures_file = ?, figures_note = NULL WHERE key = ?", (dest.name, key))
    conn.execute("DELETE FROM figure_reads WHERE paper = ? AND file IS NOT NULL", (key,))  # a name is not the bytes: what was read is not what is kept now
    stub = _drop_stub(conn, key, dest)
    conn.commit()
    if numbers:
        clause = f"its pages print the captions of {printed} of its {len(wanted)} figure{'s' if len(wanted) != 1 else ''}"
    elif conn.execute("SELECT 1 FROM nodes WHERE paper = ? LIMIT 1", (key,)).fetchone() is not None:
        clause = "its XML numbers no figures"
    else:
        clause = "its XML not yet read into rows"  # a merge's paper, its rebuild to come: its figures are not counted, not none
    log_event(conn, key, now, "filed", f"a PDF of the XML, kept for its figures: {came or src.name} ({clause})" + "".join(f"; {old} set aside as {new}" for old, new in moved)
              + (f"; {stub}, the same bytes filed before for want of an identifier, goes with its rows" if stub is not None else ""))
    return {**out, "kept": True, "file": dest.name, "aside": moved[0][1] if moved else None}


def _drop_stub(conn: sqlite3.Connection, key: str, kept: Path) -> str | None:
    """The paper the bytes kept for `key`'s figures were filed as before, which only their hash
    could meet (no DOI, no PMID): a stub of `key`, deleted with its rows, as filing drops one
    (store.file_paper). Its key, or None. Not committed."""
    from .store import sha256_of

    stub = conn.execute("SELECT key FROM papers WHERE sha256 = ? AND key != ? AND doi IS NULL AND pmid IS NULL", (sha256_of(kept), key)).fetchone()
    if stub is None:
        return None
    conn.execute("DELETE FROM papers WHERE key = ?", (stub["key"],))
    return str(stub["key"])


def figures_wanted(conn: sqlite3.Connection, papers_dir: Path | None = None) -> list[dict[str, Any]]:
    """The XML papers whose figures no PDF draws yet: read, with figures their captions number,
    and no figures_file — or, given the folder the papers are in, none on disk there: a PDF kept
    once and gone since draws nothing. What the collect window can fetch for them, most figures
    first, with PMC's settled answer when it was asked for the PDF and had none (`figures_note`),
    and the open copy OpenAlex named for the candidate the paper was filed from (`oa_url`). Only
    figure keys count: a PDF kept for a paper is read for its schemes and charts too, but a paper
    whose captions number only schemes and charts is not listed. The off switch is not looked at
    (`states` is asked as if the figures were on): the `wanted` op's own check empties its answer."""
    told = states(conn, papers_dir, True)
    rows = {r["key"]: r for r in conn.execute("SELECT key, title, doi, pmid, pmcid, figures_note FROM papers WHERE format = 'jats' AND status = 'parsed'")}
    out = [{**{c: r[c] for c in ("key", "title", "doi", "pmid", "pmcid", "figures_note")}, "figures": told[k]["numbered"], "oa_url": None}
           for k, r in rows.items() if told[k]["kind"] in ("no-pdf", "missing-file") and told[k]["numbered"]]
    if out and conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'candidates'").fetchone() \
            and "oa_url" in {r[1] for r in conn.execute("PRAGMA table_info(candidates)")}:
        urls: dict[str, str] = {}
        for r in conn.execute("SELECT paper_key, oa_url FROM candidates WHERE paper_key IS NOT NULL AND oa_url IS NOT NULL ORDER BY cand_id"):
            urls.setdefault(r["paper_key"], r["oa_url"])
        for w in out:
            w["oa_url"] = urls.get(w["key"])
    out.sort(key=lambda d: (-d["figures"], d["key"]))
    return out


def _read_now(fr: Any, fmt: str | None, own: str | None, figures_file: str | None, looked: set[str], stored: set[str]) -> bool:
    """Read by this reader, from the file that draws the figures now (a read naming none, a
    merge's copy, stands for it), looking for as many figures as there are to look for now — and,
    where it kept what each came to, for the same ones."""
    if fr is None or fr["reader"] != READER:
        return False
    source = own if fmt == "pdf" else figures_file
    if fr["file"] is not None and fr["file"] != source:
        return False
    if fr["figures"] != len(looked):
        return False
    return not stored or stored == looked


def _looked(fmt: str | None, rows: list[sqlite3.Row], labels: dict[str, tuple[str | None, bool]]) -> set[str]:
    """The figures a read looks for now: an XML paper's floats by their keys (`figure_numbers`),
    a PDF paper's pictures large enough and placed (`pictures`)."""
    if fmt == "pdf":
        return {r["node_id"] for r in rows if _placed(r) and not _small(r)}
    return {node for node, (k, first) in labels.items() if first}


def _placed(r: sqlite3.Row) -> bool:
    return r["page"] is not None and r["bbox_l"] is not None


def _small(r: sqlite3.Row) -> bool:
    return not (r["bbox_r"] - r["bbox_l"] >= MIN_SIDE_PT and abs(r["bbox_b"] - r["bbox_t"]) >= MIN_SIDE_PT)


def is_read(conn: sqlite3.Connection, key: str) -> bool:
    """Whether the paper's figures were read by this reader from the file that draws them now,
    looking for the figures there are now: as many (`figure_reads.figures`), and, where what each
    came to was kept, the same ones. A figure the reading gained since, or a caption that numbers
    one now, is a figure not read."""
    ensure_schema(conn)
    row = conn.execute("SELECT f.reader, f.file, f.figures, p.format, p.file AS own, p.figures_file FROM figure_reads f JOIN papers p ON p.key = f.paper WHERE f.paper = ?", (key,)).fetchone()
    if row is None:
        return False
    rows = _figure_rows(conn, [key])
    stored = {r[0] for r in conn.execute("SELECT figure FROM figure_outcomes WHERE paper = ?", (key,))}
    return _read_now(row, row["format"], row["own"], row["figures_file"], _looked(row["format"], rows, _labelled(rows)), stored)


def _outcome(fmt: str | None, drawn: bool, read_now: bool, node: str, r: sqlite3.Row, label: tuple[str | None, bool], stored: dict[str, Any], plotted: set[str]) -> str:
    """What one figure came to: its row, kept by the read the paper's figures stand on, or what is
    known without one. A read made before rows were kept tells what `backfill` would record from
    SQL: a figure with plots has plots, a PDF's picture with none has none; only an XML figure with
    none, whose caption the PDF may not print, waits for its row (unrecorded)."""
    k, first = label
    if fmt == "jats":
        if k is None:
            return "unnumbered"  # no number to match a PDF's captions by
        if not first:
            return "same-number"  # an earlier caption's kind and number: a PDF's plots go to that one
        if not drawn:
            return "no-pdf"
    elif not _placed(r):
        return "unplaced"
    elif _small(r):
        return "small"
    if not read_now:
        return "unread"
    if node in stored:
        return str(stored[node]["outcome"])
    if node in plotted:
        return "plots"
    return "no-plot" if fmt != "jats" else "unrecorded"  # read before outcomes were kept: `backfill`


_TALLY = ("figures", "numbered", "pinned", "no_plot", "no_caption", "unnumbered", "same_number", "small", "unplaced", "unread")
_COUNTED = {"plots": "pinned", "no-plot": "no_plot", "no-caption": "no_caption", "unnumbered": "unnumbered", "same-number": "same_number", "small": "small",
            "unplaced": "unplaced", "unread": "unread", "unrecorded": "unread", "skipped": "unread"}


def _paper_outcomes(fmt: str | None, figures_file: str | None, fr: Any, own: str | None, rows: list[sqlite3.Row], stored: dict[str, Any],
                    plotted: set[str]) -> tuple[bool, dict[str, tuple[str, str | None]], dict[str, int]]:
    """A paper's figures, each by what it came to (`plotted`: the nodes with `charts` rows): (read
    now, {node: (outcome, its key)}, the counts the window shows). An XML paper's are counted over
    its figures — not its schemes or charts, whose rows tag their own nodes — as `figures_wanted`
    counts them; a PDF paper's over the pictures a read looks for, its small and unplaced ones
    counted apart. The one count the read message and the papers op both give."""
    labels = _labelled(rows)
    read_now = _read_now(fr, fmt, own, figures_file, _looked(fmt, rows, labels), set(stored))
    drawn = fmt != "jats" or bool(figures_file)
    each: dict[str, tuple[str, str | None]] = {}
    c = dict.fromkeys(_TALLY, 0)
    for r in rows:
        node = r["node_id"]
        o = _outcome(fmt, drawn, read_now, node, r, labels[node], stored, plotted)
        k, first = labels[node]
        each[node] = (o, k)
        if fmt == "jats" and k is not None and not is_figure_key(k):
            continue  # "Scheme 1": looked for, and not counted
        if o in ("small", "unplaced"):
            c[_COUNTED[o]] += 1  # a PDF's picture no read looks for: not one of its figures
            continue
        c["figures"] += 1
        c["numbered"] += is_figure_key(k) and first
        if o in _COUNTED:
            c[_COUNTED[o]] += 1
    return read_now, each, c


def states(conn: sqlite3.Connection, papers_dir: Path | None, enabled: bool, keys: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """What each paper's figures came to (`keys`, or every paper's), from a few grouped queries —
    SQL only, no PDF opened: `{kind, figures, numbered, pinned, no_plot, no_caption, unnumbered,
    same_number, small, unplaced, unread, plots, read, values, unmatched, pages, pages_read, file,
    at, note, asked}`. The kind:

    - none: no figure — for a PDF paper, no picture a read looks for (`small` and `unplaced`
      count the rest).
    - Then the file decides: an XML paper with no PDF kept beside it is no-pdf; a file that draws
      the figures not on disk in `papers_dir` (none given: taken as there) is missing-file —
      whatever was read of it.
    - read, when the read stands (`is_read`), else unread.
    - off in place of no-pdf, missing-file and unread when `enabled` is false — the switch, which
      the caller reads, stops the work, not the answers: a read paper is read either way.

    `plots`, `read` and `values` count `charts` rows, a scheme's too; `unmatched`, `pages` and
    `pages_read` are the last read's; `note` is PMC's settled answer (`figures_note`); `asked`
    the last word on asking PMC for its PDF (a `figures-pdf` row: an ask, never a refused drop)."""
    ensure_schema(conn)
    where = f"IN ({', '.join('?' * len(keys))})" if keys is not None else "IS NOT NULL"
    args = list(keys or [])
    papers = conn.execute(f"SELECT key, format, file, figures_file, figures_note FROM papers WHERE key {where}", args).fetchall()
    figs: dict[str, list[sqlite3.Row]] = {}
    for r in _figure_rows(conn, keys):
        figs.setdefault(r["paper"], []).append(r)
    reads = {r["paper"]: r for r in conn.execute(f"SELECT * FROM figure_reads WHERE paper {where}", args)}
    stored: dict[str, dict[str, Any]] = {}
    for r in conn.execute(f"SELECT paper, figure, outcome FROM figure_outcomes WHERE paper {where}", args):
        stored.setdefault(r["paper"], {})[r["figure"]] = r
    plots = {r["paper"]: r for r in conn.execute(f"SELECT paper, COUNT(*) AS plots, SUM(status = 'read') AS read FROM charts WHERE paper {where} GROUP BY paper", args)}
    plotted: dict[str, set[str]] = {}
    for r in conn.execute(f"SELECT DISTINCT paper, figure FROM charts WHERE paper {where}", args):
        plotted.setdefault(r[0], set()).add(r[1])
    values = {r[0]: r[1] for r in conn.execute(f"SELECT paper, COUNT(*) FROM chart_values WHERE paper {where} GROUP BY paper", args)}
    asked = {r[0]: r[1] for r in conn.execute(f"SELECT paper, detail FROM events WHERE id IN (SELECT MAX(id) FROM events WHERE stage = 'figures-pdf' AND paper {where} GROUP BY paper)", args)}
    out: dict[str, dict[str, Any]] = {}
    for p in papers:
        key, fmt = p["key"], p["format"]
        fr = reads.get(key)
        read_now, _, c = _paper_outcomes(fmt, p["figures_file"], fr, p["file"], figs.get(key, []), stored.get(key, {}), plotted.get(key, set()))
        file = p["file"] if fmt == "pdf" else p["figures_file"]
        if not c["figures"]:
            kind = "none"
        elif fmt == "jats" and not file:
            kind = "no-pdf"
        elif not file or (papers_dir is not None and not (papers_dir / file).is_file()):
            kind = "missing-file"
        else:
            kind = "read" if read_now else "unread"
        if not enabled and kind in ("no-pdf", "missing-file", "unread"):
            kind = "off"
        pl = plots.get(key)
        out[key] = {"kind": kind, **c, "plots": pl["plots"] if pl else 0, "read": (pl["read"] or 0) if pl else 0, "values": values.get(key, 0),
                    "unmatched": fr["unmatched"] if fr else None, "pages": fr["pages"] if fr else None, "pages_read": fr["pages_read"] if fr else None,
                    "file": file, "at": fr["at"] if fr else None, "note": p["figures_note"], "asked": asked.get(key)}
    return out


def outcomes(conn: sqlite3.Connection, key: str) -> dict[str, dict[str, Any]]:
    """Each picture or chart node of the paper by what it came to: `{outcome, label, plots, read,
    page, bbox, detail}` — the row its read kept when the paper's read stands, else what is known
    without one (no-pdf, unnumbered, same-number, small, unplaced, unrecorded, unread; for a read
    made before rows were kept, plots and a PDF picture's no-plot, as `backfill` would record them). `label` is
    the key its caption gives it ("2", "Scheme 1"); `plots` and `read` count its `charts` rows;
    `page` is a picture's, or the PDF page an XML figure's plots or caption are on. SQL only."""
    ensure_schema(conn)
    p = conn.execute("SELECT format, file, figures_file FROM papers WHERE key = ?", (key,)).fetchone()
    if p is None:
        return {}
    rows = _figure_rows(conn, [key])
    stored = {r["figure"]: r for r in conn.execute("SELECT * FROM figure_outcomes WHERE paper = ?", (key,))}
    fr = conn.execute("SELECT * FROM figure_reads WHERE paper = ?", (key,)).fetchone()
    counts = {r["figure"]: r for r in conn.execute("SELECT figure, COUNT(*) AS plots, SUM(status = 'read') AS read, MIN(page) AS page FROM charts WHERE paper = ? GROUP BY figure", (key,))}
    _, each, _ = _paper_outcomes(p["format"], p["figures_file"], fr, p["file"], rows, stored, set(counts))
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        node = r["node_id"]
        o, k = each[node]
        s = stored[node] if node in stored and stored[node]["outcome"] == o else None  # the row it came to, when the read it was kept by stands
        c = counts.get(node)
        page, bbox = (s["page"], json.loads(s["bbox"]) if s["bbox"] else None) if s else (r["page"], None)
        if s is None and o in ("plots", "no-plot"):  # a read made before rows were kept: told where `backfill` would place it
            page, bbox = (c["page"], None) if p["format"] == "jats" else (r["page"], json.loads(_place(_box(r))))
        out[node] = {"outcome": o, "label": k, "plots": c["plots"] if c else 0, "read": (c["read"] or 0) if c else 0,
                     "page": page, "bbox": bbox, "detail": s["detail"] if s else None}
    return out


def outcome_of(conn: sqlite3.Connection, figure: str) -> dict[str, Any] | None:
    """What one figure came to (`outcomes`); None for a node that is no picture or chart."""
    row = conn.execute("SELECT paper FROM nodes WHERE node_id = ?", (figure,)).fetchone()
    return outcomes(conn, row[0]).get(figure) if row is not None else None


def backfill(conn: sqlite3.Connection, key: str, papers_dir: Path) -> bool:
    """What each figure came to, for a paper whose figures were read before it was kept: a PDF
    paper's from its rows alone (a picture with plots, or with none), an XML paper's by finding
    again the page of each caption in the kept PDF's text layer (`caption_pages`): no page
    rendered, no chart read. Only for a read that stands (`is_read`, whose guards say it looked
    for the figures there are now) and that kept no outcome yet; True when rows were written.
    pdfium runs here, so only the ingest thread calls it (the figures op, a rebuild)."""
    import pypdfium2 as pdfium

    ensure_schema(conn)
    if conn.execute("SELECT 1 FROM figure_outcomes WHERE paper = ? LIMIT 1", (key,)).fetchone() is not None or not is_read(conn, key):
        return False
    p = conn.execute("SELECT format, figures_file FROM papers WHERE key = ?", (key,)).fetchone()
    pages = pages_read = None
    if p["format"] == "pdf":
        figs = pictures(conn, key)
        plotted = {r[0] for r in conn.execute("SELECT DISTINCT figure FROM charts WHERE paper = ?", (key,))}
        rows = [(f["node_id"], None, "plots" if f["node_id"] in plotted else "no-plot", f["page"], _place(_box(f)), None) for f in figs]
    else:
        numbers = figure_numbers(conn, key)
        kept = papers_dir / p["figures_file"] if p["figures_file"] else None
        if not numbers or kept is None or not kept.is_file():
            return False
        found: dict[str, int] = {}
        try:
            pdf = pdfium.PdfDocument(str(kept))
            try:
                pages, pages_read = len(pdf), 0
                for pno, page, _, caps in caption_pages(pdf, numbers):
                    page.close()
                    pages_read += 1
                    for n, _ in caps:
                        if n in numbers:
                            found.setdefault(n, pno + 1)
            finally:
                pdf.close()
        except Exception:  # noqa: BLE001 — a PDF that will not open now records nothing; the read stands
            return False
        plotted_at = {r[0]: r[1] for r in conn.execute("SELECT figure_label, MIN(page) FROM charts WHERE paper = ? AND figure_label IS NOT NULL GROUP BY figure_label", (key,))}
        rows = _xml_outcomes(numbers, plotted_at, found)
    if not rows:
        return False
    with conn:
        conn.executemany("INSERT INTO figure_outcomes(paper, figure, figure_label, outcome, page, bbox, detail) VALUES (?,?,?,?,?,?,?)", [(key, *o) for o in rows])
        if pages is not None:
            conn.execute("UPDATE figure_reads SET pages = ?, pages_read = ? WHERE paper = ?", (pages, pages_read, key))
    return True


def read_message(out: dict[str, Any], c: dict[str, Any], xml: bool) -> str | None:
    """What a read of a paper's figures came to, in the words the window uses: `out` the read's
    totals (`_store`), `c` the paper's counts (`states`), so the line and the chip count the same
    figures. None when there was nothing to look for and nothing to say of it."""
    if xml and c["figures"] and not c["numbered"]:
        n = c["figures"]
        return f"{n} figure{'s' if n != 1 else ''}, none with a number (“Figure 2”) to match the PDF’s captions by"
    if not out["figures"]:
        return None
    clauses = [(c["no_plot"], "with no chart under their caption" if xml else "with no chart in them"), (c["no_caption"] if xml else 0, "whose caption the PDF does not print"),
               (out.get("unmatched") or 0, "plots under no caption of the paper’s")]
    within = f", in {c['pinned']} of {c['figures']} figures" if c["figures"] else ""  # only schemes and charts looked for: no figure to count
    return (f"{out['read']} of {out['plots']} charts read{within}: {out['values']} values, in {out['seconds']}s"
            + "".join(f"; {n} {words}" for n, words in clauses if n))


def overlap(a: list[float] | tuple[float, ...], b: list[float] | tuple[float, ...]) -> float:
    """Two boxes (left, top, right, bottom) as alike as their shared area over their joined one:
    1 for the same box, 0 for two that do not touch."""
    shared = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    joined = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - shared
    return shared / joined if joined > 0 else 0.0


def remap(conn: sqlite3.Connection, key: str) -> dict[str, Any]:
    """After the paper's nodes were replaced (a rebuild, a reparse, a reread): each plot's figure
    found again, or the plot removed. Pinned by a caption, a plot goes to the figure with that
    number now. Else it goes to the picture on its page whose box is near the one its figure had
    (`SAME_PLACE`); where several are, to its own figure if that is one of them; else nowhere. A
    node's id alone never keeps a plot: another reading may give it to another picture. A figure
    claimed by two, or by one while it keeps plots of its own, takes none: the incoming plots are
    removed. The whole mapping is made before a row moves, so two figures that trade ids trade
    their plots. What each figure came to (`figure_outcomes`) moves and goes with it, by the same
    mapping. When any plot, or any figure's outcome, is removed the paper's read goes too, and the
    figures op reads it again (`is_read`). Returns `{moved, removed, lost}`: the plots moved and removed, and what the
    removed were plotted for ("2", "Scheme 1", "page 4")."""
    ensure_schema(conn)
    on_page: dict[int, list[tuple[str, tuple[float, ...]]]] = {}
    for r in pictures(conn, key):
        on_page.setdefault(r["page"], []).append((r["node_id"], (r["bbox_l"], min(r["bbox_t"], r["bbox_b"]), r["bbox_r"], max(r["bbox_t"], r["bbox_b"]))))
    numbers = figure_numbers(conn, key)

    def placed(old: str, page: int | None, bbox: str | None) -> str | None:
        box = json.loads(bbox) if bbox else None
        near = [node for node, b in on_page.get(page, []) if box and overlap(box, b) >= SAME_PLACE] if page is not None else []
        return near[0] if len(near) == 1 else old if old in near else None  # two alike enough to be one: its own id tells which

    to: dict[str, str | None] = {}
    plots: dict[str, int] = {}
    named: dict[str, str] = {}
    held = conn.execute("SELECT figure, figure_label, page, bbox, COUNT(*) AS n FROM charts WHERE paper = ? GROUP BY figure, figure_label, page, bbox ORDER BY MIN(rowid)", (key,)).fetchall()
    told = conn.execute("SELECT figure, figure_label, page, bbox, 0 AS n FROM figure_outcomes WHERE paper = ? ORDER BY rowid", (key,)).fetchall()  # what a figure came to goes where its plots go
    for r in [*held, *told]:
        old = r["figure"]
        now = numbers.get(r["figure_label"]) if r["figure_label"] else placed(old, r["page"], r["bbox"])
        to[old] = now if old not in to or to[old] == now else None  # a figure's plots that part ways keep none of it
        plots[old] = plots.get(old, 0) + r["n"]
        named.setdefault(old, r["figure_label"] or (f"page {r['page']}" if r["page"] is not None else "no page"))
    staying = {old for old, now in to.items() if now == old}
    claims: dict[str, list[str]] = {}
    for old, now in to.items():
        if now is not None and now != old:
            claims.setdefault(now, []).append(old)
    for now, olds in claims.items():
        if len(olds) > 1 or now in staying:
            for old in olds:
                to[old] = None
    gone = [old for old, now in to.items() if now is None]
    moving = {old: now for old, now in to.items() if now is not None and now != old}
    with conn:
        for old in gone:
            for table in ("chart_values", "charts", "figure_outcomes"):
                conn.execute(f"DELETE FROM {table} WHERE paper = ? AND figure = ?", (key, old))
        for table in ("charts", "chart_values", "figure_outcomes"):
            for old in moving:  # out of the way first, so a figure another one leaves is free when it is moved into
                conn.execute(f"UPDATE {table} SET figure = 'remap:' || figure WHERE paper = ? AND figure = ?", (key, old))
            for old, now in moving.items():
                conn.execute(f"UPDATE {table} SET figure = ? WHERE paper = ? AND figure = ?", (now, key, "remap:" + old))
        lost = [old for old in gone if plots[old]]
        if gone:  # a figure gone with no plot goes too: the read looked for what is not there now, and its last row gone would leave is_read nothing to tell it by
            conn.execute("DELETE FROM figure_reads WHERE paper = ?", (key,))
    return {"moved": sum(plots[old] for old in moving), "removed": sum(plots[old] for old in gone), "lost": list(dict.fromkeys(named[old] for old in lost))}


def figure_name(key: str) -> str:
    """A key, or a place, as a person reads it: "2" is Figure 2; "Scheme 1" and "page 4" are as
    they are."""
    return key if " " in key else f"Figure {key}"


def removed_note(out: dict[str, Any]) -> str | None:
    """What a `remap` that removed plots says, as an events row and a log line; None when it
    removed none."""
    n = out["removed"]
    if not n:
        return None
    return (f"{n} plot{'s' if n != 1 else ''} no longer {'have' if n != 1 else 'has'} a figure in this reading ({', '.join(figure_name(x) for x in out['lost'])}): "
            f"removed; the figures op reads {'them' if n != 1 else 'it'} again")


def of_figure(conn: sqlite3.Connection, figure: str) -> list[dict[str, Any]]:
    """A figure's plots as rows give them: each `{plot, panel, kind, status, reason, source, y:
    {label, unit, scale}, x: {...}, categories, series: [{name, colour, values: [{category, x,
    y, err_lo, err_hi}]}]}`."""
    ensure_schema(conn)
    out = []
    for p in conn.execute("SELECT * FROM charts WHERE figure = ? ORDER BY plot", (figure,)).fetchall():
        series: dict[int, dict[str, Any]] = {}
        for v in conn.execute("SELECT * FROM chart_values WHERE figure = ? AND plot = ? ORDER BY series, ordinal", (figure, p["plot"])):
            s = series.setdefault(v["series"], {"name": v["name"], "colour": v["colour"], "values": []})
            s["values"].append({"category": v["category"], "x": v["x"], "y": v["y"], "err_lo": v["err_lo"], "err_hi": v["err_hi"]})
        out.append({
            "plot": p["plot"], "panel": p["panel"], "title": p["title"], "kind": p["kind"], "status": p["status"], "reason": p["reason"], "source": p["source"],
            "y": {"label": p["y_label"], "unit": p["y_unit"], "scale": p["y_scale"]}, "x": {"label": p["x_label"], "unit": p["x_unit"], "scale": p["x_scale"]},
            "categories": json.loads(p["categories"] or "[]"), "series": list(series.values()),
        })
    return out


def csv_of(plot: dict[str, Any]) -> str:
    """One plot as CSV: a row per value — series, category or x, y, the error bar's ends."""
    lines = ["series,category,x,y,err_lo,err_hi"]

    def cell(v: Any) -> str:
        if v is None:
            return ""
        t = str(v)
        return '"' + t.replace('"', '""') + '"' if any(c in t for c in ',"\n') else t

    for i, s in enumerate(plot["series"]):
        for v in s["values"]:
            lines.append(",".join(cell(x) for x in (s["name"] or f"series {i + 1}", v["category"], v["x"], v["y"], v["err_lo"], v["err_hi"])))
    return "\n".join(lines) + "\n"
