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
rebuild that renumbers the nodes finds the figure again (`remap`). Reading a paper again
replaces its rows; `READER` names the reader, and a paper read by this one is not read again
unless asked.

Nothing leaves the machine: the PDF is rendered here, and OCR runs on the models RapidOCR's
package ships.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import charts
from .charts import Word

READER = "charts-1"
VECTOR_DPI = 600
MAX_DPI = 600
MIN_SIDE_PT = 72  # a picture smaller than an inch each way is a logo or an icon: not read


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
    conn.executescript(
        """
CREATE TABLE IF NOT EXISTS charts (
  paper TEXT NOT NULL REFERENCES papers(key) ON DELETE CASCADE,
  figure TEXT NOT NULL,             -- the picture node it was read from
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
  seconds REAL, at TEXT NOT NULL
);
"""
    )


def _hex(c: list[int] | tuple[int, ...]) -> str:
    return "#" + "".join(f"{int(v):02x}" for v in c[:3])


def pictures(conn: sqlite3.Connection, key: str) -> list[sqlite3.Row]:
    """The paper's pictures with a place on a page, large enough to hold a chart."""
    rows = conn.execute(
        "SELECT node_id, page, bbox_l, bbox_t, bbox_r, bbox_b FROM nodes WHERE paper = ? AND type IN ('picture', 'chart') AND page IS NOT NULL AND bbox_l IS NOT NULL ORDER BY rowid",
        (key,),
    ).fetchall()
    return [r for r in rows if r["bbox_r"] - r["bbox_l"] >= MIN_SIDE_PT and abs(r["bbox_b"] - r["bbox_t"]) >= MIN_SIDE_PT]


def read_paper(conn: sqlite3.Connection, key: str, pdf_path: Path, *, ocr: charts.Ocr | None = None, on_figure: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Every figure of a PDF paper read, its rows replacing the ones it had: `{figures, plots,
    read, values, seconds}`."""
    import pypdfium2 as pdfium

    from .library import now_iso

    ensure_schema(conn)
    t0 = time.time()
    figs = pictures(conn, key)
    stamp = now_iso()
    plots_rows: list[tuple[Any, ...]] = []
    value_rows: list[tuple[Any, ...]] = []
    n_read = 0
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        for i, f in enumerate(figs):
            if on_figure:
                on_figure(i, len(figs))
            bbox = (f["bbox_l"], min(f["bbox_t"], f["bbox_b"]), f["bbox_r"], max(f["bbox_t"], f["bbox_b"]))
            if f["page"] > len(pdf):
                continue
            img, words, source, scale = crop(pdf, f["page"], bbox)
            plots = charts.read(img, words, ocr=ocr or charts.ocr_words)
            for j, p in enumerate(plots):
                y, x = p.get("y") or {}, p.get("x") or {}
                plots_rows.append((key, f["node_id"], j, f["page"], json.dumps([round(v, 2) for v in bbox]), p.get("panel"), p.get("title") or None, p.get("kind"), p["status"], p.get("reason") or None,
                                   source, y.get("label") or None, y.get("unit"), y.get("scale"), x.get("label") or None, x.get("unit"), x.get("scale"),
                                   y.get("residual"), json.dumps(y.get("ticks") or []), json.dumps(p.get("categories") or []),
                                   json.dumps([round(v / scale, 2) for v in p["bbox"]]), READER, stamp))
                if p["status"] != "read":
                    continue
                n_read += 1
                for si, s in enumerate(p["series"]):
                    for vi, v in enumerate(s["values"]):
                        value_rows.append((key, f["node_id"], j, si, s.get("name"), _hex(s.get("colour") or [0, 0, 0]), vi, v.get("category"), v.get("x"), v["y"], v.get("err_lo"), v.get("err_hi")))
    finally:
        pdf.close()
    seconds = round(time.time() - t0, 2)
    with conn:
        conn.execute("DELETE FROM chart_values WHERE paper = ?", (key,))
        conn.execute("DELETE FROM charts WHERE paper = ?", (key,))
        conn.executemany(f"INSERT INTO charts VALUES ({', '.join('?' * 23)})", plots_rows)
        conn.executemany(f"INSERT INTO chart_values VALUES ({', '.join('?' * 12)})", value_rows)
        conn.execute("INSERT OR REPLACE INTO figure_reads VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (key, READER, len(figs), len(plots_rows), n_read, len(value_rows), seconds, stamp))
    return {"figures": len(figs), "plots": len(plots_rows), "read": n_read, "values": len(value_rows), "seconds": seconds}


def is_read(conn: sqlite3.Connection, key: str) -> bool:
    ensure_schema(conn)
    row = conn.execute("SELECT reader FROM figure_reads WHERE paper = ?", (key,)).fetchone()
    return row is not None and row[0] == READER


def remap(conn: sqlite3.Connection, key: str) -> int:
    """After a rebuild renumbered the nodes: each plot's figure found again by its page and its
    place on it. Returns how many plots moved."""
    ensure_schema(conn)
    figs = {(r["page"], tuple(round(v, 2) for v in (r["bbox_l"], min(r["bbox_t"], r["bbox_b"]), r["bbox_r"], max(r["bbox_t"], r["bbox_b"])))): r["node_id"] for r in pictures(conn, key)}
    moved = 0
    with conn:
        for r in conn.execute("SELECT DISTINCT figure, page, bbox FROM charts WHERE paper = ?", (key,)).fetchall():
            now = figs.get((r["page"], tuple(json.loads(r["bbox"]))))
            if now and now != r["figure"]:
                conn.execute("UPDATE chart_values SET figure = ? WHERE paper = ? AND figure = ?", (now, key, r["figure"]))
                moved += conn.execute("UPDATE charts SET figure = ? WHERE paper = ? AND figure = ?", (now, key, r["figure"])).rowcount
    return moved


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
