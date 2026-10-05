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
import re
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
  figure_label TEXT,                -- "2" when the plot was pinned to "Figure 2" by its caption (an XML paper's PDF)
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
  unmatched INTEGER                 -- plots in that PDF pinned to no figure of the paper
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


def _store(conn: sqlite3.Connection, key: str, rows: list[dict[str, Any]], values: list[tuple[Any, ...]], figures_n: int, seconds: float, stamp: str, file: str, unmatched: int = 0) -> dict[str, Any]:
    """A paper's chart rows in place of the ones it had."""
    n_read = sum(r["status"] == "read" for r in rows)
    with conn:
        conn.execute("DELETE FROM chart_values WHERE paper = ?", (key,))
        conn.execute("DELETE FROM charts WHERE paper = ?", (key,))
        conn.executemany(f"INSERT INTO charts({', '.join(_CHART_COLS)}) VALUES ({', '.join('?' * len(_CHART_COLS))})", [tuple(r[c] for c in _CHART_COLS) for r in rows])
        conn.executemany(f"INSERT INTO chart_values VALUES ({', '.join('?' * 12)})", values)
        conn.execute("INSERT OR REPLACE INTO figure_reads(paper, reader, figures, plots, read, values_, seconds, at, file, unmatched) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (key, READER, figures_n, len(rows), n_read, len(values), seconds, stamp, file, unmatched))
    return {"figures": figures_n, "plots": len(rows), "read": n_read, "values": len(values), "seconds": seconds, "unmatched": unmatched}


def read_paper(conn: sqlite3.Connection, key: str, pdf_path: Path, *, ocr: charts.Ocr | None = None, on_figure: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Every figure of a PDF paper read, its rows replacing the ones it had: `{figures, plots,
    read, values, seconds}`."""
    import pypdfium2 as pdfium

    from .library import now_iso

    ensure_schema(conn)
    t0 = time.time()
    figs = pictures(conn, key)
    stamp = now_iso()
    rows: list[dict[str, Any]] = []
    values: list[tuple[Any, ...]] = []
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        for i, f in enumerate(figs):
            if on_figure:
                on_figure(i, len(figs))
            bbox = (f["bbox_l"], min(f["bbox_t"], f["bbox_b"]), f["bbox_r"], max(f["bbox_t"], f["bbox_b"]))
            if f["page"] > len(pdf):
                continue
            img, words, source, scale = crop(pdf, f["page"], bbox)
            for j, p in enumerate(charts.read(img, words, ocr=ocr or charts.ocr_words)):
                row, vals = _rows(key, f["node_id"], None, j, f["page"], list(bbox), p, source, scale, stamp)
                rows.append(row)
                values += vals
    finally:
        pdf.close()
    return _store(conn, key, rows, values, len(figs), round(time.time() - t0, 2), stamp, pdf_path.name)


# -- an XML paper's figures, from a PDF of it ------------------------------------------------------------------

PAGE_DPI = 300
_LABEL = re.compile(r"^\s*(?:fig(?:ure)?s?|scheme)\s*\.?\s*(S?\d+)(?![\d.]*\d)", re.I)


def figure_numbers(conn: sqlite3.Connection, key: str) -> dict[str, str]:
    """The paper's figures by the number their captions give them: {"2": picture node}."""
    out: dict[str, str] = {}
    for r in conn.execute(
        "SELECT p.node_id, p.text, (SELECT group_concat(c.text, ' ') FROM nodes c WHERE c.parent = p.node_id AND c.type = 'caption') AS cap "
        "FROM nodes p WHERE p.paper = ? AND p.type IN ('picture', 'chart') ORDER BY p.rowid",
        (key,),
    ):
        m = _LABEL.match(r["cap"] or r["text"] or "")
        if m and m.group(1).upper() not in out:
            out[m.group(1).upper()] = r["node_id"]
    return out


_CAPTION_UPPER = re.compile(r"^\s*(?:FIGURE|FIG\.|SCHEME)\s*(S?\d+)(?![\d.]*\d)")  # set in capitals: a caption, punctuated or not
_CAPTION_DOTTED = re.compile(r"^\s*(?:fig(?:ure)?|scheme)\s*\.?\s*(S?\d+)\s*[.:|](?!\d)", re.I)  # "Figure 2." "Fig. 2:" "Figure 2 |"


def captions(words: list[Word]) -> list[tuple[str, Word]]:
    """The captions on a page, ("2", the caption's first line): a phrase that opens the way a
    caption does — "FIGURE 2", "Fig. 2.", "Figure 2:" — not a sentence that begins "Figure 2
    shows"; its extent the whole of its line, which the text layer gives in pieces."""
    out = []
    for w in words:
        m = _CAPTION_UPPER.match(w.text) or _CAPTION_DOTTED.match(w.text)  # not "Figure 2 shows": a sentence
        if not m or w.w < w.h:
            continue
        num = m.group(1).upper()
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


def read_pages(conn: sqlite3.Connection, key: str, pdf_path: Path, *, ocr: charts.Ocr | None = None, on_page: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """An XML paper's figures read from a PDF of it, on each page that prints a figure's caption:
    an image there large enough to be a figure is read whole, at its own resolution, with OCR,
    and pinned with all its plots to the caption it sits over ("Figure 2", so to the XML's figure
    2); the rest of the page, the images blanked, is read at PAGE_DPI with its own text layer, and
    each plot drawn there pinned the same way. A plot no caption of the paper's claims is counted,
    not kept."""
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
        for pno in range(len(pdf)):
            if on_page:
                on_page(pno, len(pdf))
            page = pdf[pno]
            w, h = page.get_size()
            words_pt = text_words(page, (0, 0, w, h), 1.0)
            caps = [(n, c) for n, c in captions(words_pt) if n in numbers]
            if not caps:
                page.close()
                continue
            # the images: each a figure of its own, read whole
            images = _images(page, w, h)
            for _, box in images:
                num = owner(list(box), caps)
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
                num = owner(box, caps)
                if num is None:
                    unmatched += 1
                else:
                    by_fig.setdefault(num, []).append((p, box, "vector", scale))
            for num, plots in by_fig.items():
                keep(num, plots, pno)
    finally:
        pdf.close()
    return _store(conn, key, rows, values, len(numbers), round(time.time() - t0, 2), stamp, pdf_path.name, unmatched)


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


def figures_wanted(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The XML papers whose figures no PDF draws yet: read, with figures their captions number,
    and no figures_file — what the collect window can fetch for them, most figures first."""
    out = []
    for r in conn.execute("SELECT key, title, doi, pmid, pmcid FROM papers WHERE format = 'jats' AND status = 'parsed' AND (figures_file IS NULL OR figures_file = '')"):
        n = len(figure_numbers(conn, r["key"]))
        if n:
            out.append({**dict(r), "figures": n})
    out.sort(key=lambda d: (-d["figures"], d["key"]))
    return out


def is_read(conn: sqlite3.Connection, key: str) -> bool:
    """Whether the paper's figures were read by this reader from the file that draws them now."""
    ensure_schema(conn)
    row = conn.execute("SELECT f.reader, f.file, p.format, p.file AS own, p.figures_file FROM figure_reads f JOIN papers p ON p.key = f.paper WHERE f.paper = ?", (key,)).fetchone()
    if row is None or row["reader"] != READER:
        return False
    source = row["own"] if row["format"] == "pdf" else row["figures_file"]
    return row["file"] is None or row["file"] == source


def remap(conn: sqlite3.Connection, key: str) -> int:
    """After a rebuild renumbered the nodes: each plot's figure found again — by its page and its
    place on it, or, pinned by a caption, by its number. Returns how many plots moved."""
    ensure_schema(conn)
    figs = {(r["page"], tuple(round(v, 2) for v in (r["bbox_l"], min(r["bbox_t"], r["bbox_b"]), r["bbox_r"], max(r["bbox_t"], r["bbox_b"])))): r["node_id"] for r in pictures(conn, key)}
    numbers = figure_numbers(conn, key)
    moved = 0
    with conn:
        for r in conn.execute("SELECT DISTINCT figure, figure_label, page, bbox FROM charts WHERE paper = ?", (key,)).fetchall():
            now = numbers.get(r["figure_label"]) if r["figure_label"] else figs.get((r["page"], tuple(json.loads(r["bbox"]))))
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
