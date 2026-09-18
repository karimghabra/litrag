"""What the PDF's text layer knows that the layout model missed.

Docling's layout model draws boxes and labels them; the words inside come from the
PDF's text layer. When the model draws no box around a block — the first lines of a
column after a page break, a paragraph beside a wide figure — those words reach no node,
and when it labels a body block a page footer, they are thrown away as furniture. The
text layer has every line with its position, so this pass reads it back:

- a line no box holds, or a box labelled furniture holds while it reads as prose, comes
  back as a paragraph at its place on the page, in reading order;
- a block whose text lacks lines the layer has inside its box gets its text rebuilt
  from the layer, in order, de-hyphenated; a block whose ligatures Docling split
  ("signi fi cantly") takes the layer's whole words;
- an equation the model saw but did not read (an empty `formula`) takes the layer's
  text inside its box; a table the model saw but could not structure gives its lines
  back as paragraphs;
- every prose block learns its geometry — first-line indent, last-line width — which is
  how a reader tells a paragraph that ends from one the page cut in two.

A paragraph Docling carried across a page break is one item with two provenance
entries, one per page; both pages are read, so the second page's lines are never taken
for free. A superscript the layer breaks out on its own is marked "^7", never fused into
"107". Everything here is deterministic and local: pypdfium2 reading the file already
on disk. The raw Docling document is untouched; the additions live on the document in
memory that the tree is built from, and `rebuild` derives them again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_WORD = re.compile(r"[A-Za-z]{2,}")
_LETTERS = re.compile(r"[^a-z]")
_FURNITURE_LABELS = {"page_header", "page_footer"}
_TEXT_LABELS = {"text", "paragraph", "list_item", "footnote", "caption", "section_header", "formula", "code"}
_REBUILDABLE = {"text", "paragraph", "list_item", "footnote", "caption"}
_RUNNING = re.compile(r"downloaded from|^\s*journal of\b|\bvol\.? ?\d|\bissue \d|©|\bcopyright\b|all rights reserved|wiley online library|creative commons|^\s*\d+\s*$|^[\d\s.]+$|^https?://|\bwww\.|doi:\s*10\.|^\s*page \d", re.I)
_LIGATURE = re.compile(r"\b[a-z]{2,} (?:fi|fl|ff|ffi|ffl) [a-z]{2,}\b")
_SUPERSCRIPT_RUN = re.compile(r"[\d+\u2212\u2013\-]{1,3}")


@dataclass
class Line:
    text: str
    l: float
    b: float
    r: float
    t: float

    @property
    def cx(self) -> float:
        return (self.l + self.r) / 2

    @property
    def cy(self) -> float:
        return (self.b + self.t) / 2

    @property
    def height(self) -> float:
        return self.t - self.b


def _letters(s: str) -> str:
    return _LETTERS.sub("", s.lower())


_SOFT_BREAK = "\ufffe"  # pdfium's mark where a word was broken at a line end
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean(text: str) -> str:
    return _CONTROL.sub("", text.replace(_SOFT_BREAK, "")).strip()


def pdf_lines(path: Path) -> dict[int, list[Line]]:
    """Every visual row of every page with its box, from pdfium's text in reading order and
    its character boxes. pdfium's own line breaks are one clue; a row also ends where the
    baseline moves, since pdfium runs the first lines of a paragraph together. A raised
    small number is a superscript and is marked "^7", never fused into "107"."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    out: dict[int, list[Line]] = {}
    try:
        for i in range(len(pdf)):
            tp = pdf[i].get_textpage()
            n = tp.count_chars()
            if not n:
                continue
            text = tp.get_text_range(0, n)
            lines: list[Line] = []
            start = 0
            for raw in text.split("\r\n"):
                end = start + len(raw)
                chars = [(text[j], None if text[j].isspace() else tp.get_charbox(j)) for j in range(start, min(end, n))]
                start = end + 2
                for row in _visual_rows(chars):
                    line = _line_of(row)
                    if line is None:
                        continue
                    prev = lines[-1] if lines else None
                    if prev is not None and _same_row(prev, line) and _adjacent(prev, line):
                        _fold(prev, line)
                    else:
                        lines.append(line)
            out[i + 1] = lines
    finally:
        pdf.close()
    return out


def _visual_rows(chars: list[tuple[str, tuple[float, float, float, float] | None]]) -> list[list[tuple[str, tuple[float, float, float, float] | None]]]:
    """One pdfium line cut where its baseline moves by most of a line: the next visual row."""
    rows: list[list[tuple[str, tuple[float, float, float, float] | None]]] = []
    cur: list[tuple[str, tuple[float, float, float, float] | None]] = []
    ref_cy: float | None = None
    ref_h = 0.0
    for ch, box in chars:
        if box is None or not (box[2] > box[0] and box[3] > box[1]):
            if cur:
                cur.append((ch, None))
            continue
        cy, h = (box[1] + box[3]) / 2, box[3] - box[1]
        if ref_cy is not None and abs(cy - ref_cy) > 0.7 * max(ref_h, h):
            rows.append(cur)
            cur, ref_cy = [], None
        if ref_cy is None or h > ref_h:
            ref_cy, ref_h = cy, h  # the row's largest glyph sets its baseline: a superscript never does
        cur.append((ch, box))
    if cur:
        rows.append(cur)
    return rows


def _line_of(row: list[tuple[str, tuple[float, float, float, float] | None]]) -> Line | None:
    """A visual row as a Line: its text with superscript numbers marked, its box."""
    boxes = [bx for _, bx in row if bx is not None]
    if not boxes:
        return None
    # the baseline is where most glyphs sit; the body size is theirs
    counts: dict[float, int] = {}
    for _, b, _, _ in boxes:
        counts[round(b)] = counts.get(round(b), 0) + 1
    base = max(counts, key=lambda k: (counts[k], -k))
    on_base = [(ch, t - b) for ch, bx in row if bx is not None for _, b, _, t in [bx] if abs(b - base) <= 1.0 and t - b > 0]
    caps = sorted(h for ch, h in on_base if ch.isdigit() or ch.isupper())
    body = caps[len(caps) // 2] if caps else max((h for _, h in on_base), default=0.0)  # a capital's height: what a digit on the baseline stands

    def raised(k: int) -> bool:
        ch, bx = row[k]
        if bx is None or not ch.isdigit() or body <= 0:
            return False
        _, b, _, t = bx
        h = t - b
        # a superscript is a small digit well above the baseline; a glyph pdfium gives a
        # degenerate box (a sliver at mid-height) is neither
        return 0.35 * body <= h < 0.8 * body and b - base > 0.3 * body

    flags = [raised(k) for k in range(len(row))]
    for k, (ch, bx) in enumerate(row):  # a sign belongs to the exponent after it: "cm^-1"
        if ch in "+\u2212\u2013-" and bx is not None and k + 1 < len(row) and flags[k + 1]:
            _, b, _, t = bx
            flags[k] = (t - b) < 0.85 * body and b - base > 0.15 * body
    pieces: list[str] = []
    in_sup = False
    for k, (ch, bx) in enumerate(row):
        if bx is None:
            pieces.append(ch)
            in_sup = False
            continue
        if flags[k] and not in_sup:
            if pieces and pieces[-1].isspace():
                pieces.pop()
            pieces.append("^")
        in_sup = flags[k]
        pieces.append(ch)
    text = re.sub(r"\^(?![\d+−–-])", "", clean(" ".join("".join(pieces).split())))  # a mark with nothing after it marks nothing
    if not text:
        return None
    return Line(text, min(x[0] for x in boxes), min(x[1] for x in boxes), max(x[2] for x in boxes), max(x[3] for x in boxes))


def _adjacent(a: Line, b: Line) -> bool:
    """A piece right beside the row's line — a superscript, a symbol from another font, a
    word set in italics, a table's next cell — not the other column of the page."""
    gap = max(b.l - a.r, a.l - b.r)
    h = max(a.height, b.height)
    return gap < 1.2 * h or (len(b.text.strip()) <= 4 and gap < 3 * h)


def _fold(prev: Line, line: Line) -> None:
    """`line` into `prev`, the row it sits on: a raised small number is a superscript and
    is marked "^7", not fused into "107"; a visible gap is a space; a wide gap — a table's
    next cell — is two spaces, which `_grid_of` reads and `_join_lines` closes."""
    raised = 0.3 * prev.height <= line.height < 0.85 * prev.height and line.b > prev.b + 0.25 * prev.height
    lowered = line.height < 0.85 * prev.height and line.t < prev.t - 0.25 * prev.height
    piece = line.text.strip()
    if raised and _SUPERSCRIPT_RUN.fullmatch(piece):
        piece = "^" + piece
    h = max(prev.height, line.height)
    if line.l >= prev.l:
        gap = line.l - prev.r
        after_mark = bool(re.search(r"\^[\d+\u2212\u2013\-]{1,3}$", prev.text))
        if (gap < 0.15 * h and not after_mark) or piece.startswith("^") or lowered:
            sep = ""  # glyphs touching: one word
        else:
            sep = " " if gap < 0.6 * h else "  "
        prev.text = prev.text + sep + piece
    else:
        gap = prev.l - line.r
        prev.text = piece + ("" if gap < 0.15 * h else (" " if gap < 0.6 * h else "  ")) + prev.text
    prev.l, prev.b, prev.r, prev.t = min(prev.l, line.l), min(prev.b, line.b), max(prev.r, line.r), max(prev.t, line.t)


def _cells(line: Line) -> list[str]:
    return [c for c in line.text.split("  ") if c.strip()]


def _grid_of(lines: list[Line]) -> list[list[dict[str, str]]]:
    """The lines inside a table's box as rows of cells: one row per visual row, one cell per
    piece the row's spacing sets apart."""
    rows: list[list[Line]] = []
    for ln in _rows(lines):
        if rows and _same_row(rows[-1][0], ln):
            rows[-1].append(ln)
        else:
            rows.append([ln])
    return [[{"text": c} for ln in sorted(row, key=lambda x: x.l) for c in _cells(ln)] for row in rows]


_NOTE_START = re.compile(r"^(?:Notes?\b|Abbreviations?\b|Data are\b|Values are\b|Reported values\b|Results are\b|Each value\b|Means?\b|SD\b|\*|†|‡|§|[a-d]\s|[a-d](?=[A-Z]))")


def _under_table(bb: dict[str, float], tables: list[dict[str, float]], line_h: float) -> bool:
    for tb in tables:
        overlap = min(bb["r"], tb["r"]) - max(bb["l"], tb["l"])
        if overlap > 0.5 * (bb["r"] - bb["l"]) and -2 <= tb["b"] - bb["t"] <= 2.5 * line_h:
            return True
    return False


def _unglue_sidebars(doc: dict[str, Any], texts: list[dict[str, Any]], body_children: list[dict[str, str]], items_by_page: dict[int, list[tuple[dict[str, Any], dict[str, float]]]], report: dict[str, int]) -> None:
    """The layout model glues a page's sidebar to body blocks on the pages after it, as one
    item with one box per piece. Each body piece — its own span of the text — becomes a
    block of its own at its place on its page; the sidebar keeps its own words and is
    dropped as furniture by the tree."""
    for item in list(texts):
        if not item.get("_sidebar"):
            continue
        provs = [p for p in item.get("prov") or [] if "page_no" in p and p.get("bbox")]
        text = item.get("text") or ""
        pieces = [p for p in provs if not _sidebar(p["bbox"], doc.get("_unit", 10.0)) and p.get("charspan")]
        for p in pieces:
            a, b = p["charspan"]
            words = text[a:b].strip()
            if len(_WORD.findall(words)) < 4:
                continue
            new = {"self_ref": f"#/texts/{len(texts)}", "parent": {"$ref": "#/body"}, "children": [], "label": "text", "text": words, "prov": [{**p, "charspan": [0, len(words)]}], "_recovered": True, "_unglued": True}
            texts.append(new)
            page = int(p["page_no"])
            entries = items_by_page.setdefault(page, [])
            entries[:] = [(it, bb) for it, bb in entries if it is not item]
            entries.append((new, p["bbox"]))
            body_children.insert(_insert_at(doc, body_children, items_by_page, page, p["bbox"]), {"$ref": new["self_ref"]})
            report["unglued"] = report.get("unglued", 0) + 1
        if pieces:
            first = provs[0]
            if first.get("charspan"):
                item["text"] = text[first["charspan"][0] : first["charspan"][1]]
            item["prov"] = [first]


def _split_misjoined(doc: dict[str, Any], items_by_page: dict[int, list[tuple[dict[str, Any], dict[str, float]]]], texts: list[dict[str, Any]], body_children: list[dict[str, str]], report: dict[str, int]) -> None:
    """A paragraph Docling carried over a page break onto a table's note: the note is cut off
    as the table's footnote and the paragraph is left unfinished, for its real tail to find."""
    for item in list(texts):
        provs = item.get("prov") or []
        if len(provs) < 2 or item.get("label") not in ("text", "paragraph"):
            continue
        text = item.get("text") or ""
        for k in range(1, len(provs)):
            span, prev_span = provs[k].get("charspan"), provs[k - 1].get("charspan")
            if not span or not prev_span:
                continue
            head, tail = text[: prev_span[1]].rstrip(), text[span[0] : span[1]].strip()
            if not head or not tail or head[-1] in ".!?:;\"'\u201d\u2019" or not tail[0].isupper():
                continue
            page, bb = int(provs[k]["page_no"]), provs[k].get("bbox")
            tables = [tb for it, tb in items_by_page.get(page, []) if it.get("data") is not None]
            if not bb or not tables or not _under_table(bb, tables, 1.3 * doc.get("_unit", 10.0)) or not (_NOTE_START.match(tail) or len(tail.split()) <= 40):
                continue
            new = {"self_ref": f"#/texts/{len(texts)}", "parent": {"$ref": "#/body"}, "children": [], "label": "footnote", "text": tail, "prov": [{**provs[k], "charspan": [0, len(tail)]}], "_table_note": True, "_recovered": True}
            item["text"], item["prov"] = head, provs[:k]
            texts.append(new)
            entries = items_by_page.setdefault(page, [])
            entries[:] = [(it, b) for it, b in entries if it is not item or b is not bb]
            entries.append((new, bb))
            body_children.insert(_insert_at(doc, body_children, items_by_page, page, bb), {"$ref": new["self_ref"]})
            report["notes"] = report.get("notes", 0) + 1
            break


def _table_notes(items_by_page: dict[int, list[tuple[dict[str, Any], dict[str, float]]]], report: dict[str, int], unit: float = 10.0) -> None:
    """A short block right under a table's box that reads as its note is the table's footnote,
    not a paragraph: it must not be joined to the paragraph the table interrupted."""
    for page_no, entries in items_by_page.items():
        tables = [bb for item, bb in entries if item.get("data") is not None]
        if not tables:
            continue
        for item, bb in entries:
            if item.get("label") not in ("text", "paragraph") or len(item.get("prov") or []) != 1:
                continue
            text = (item.get("text") or "").strip()
            words = len(text.split())
            lines = max(int(item.get("_lines") or 1), 1)
            if not text or words > 80:
                continue
            line_h = max((bb["t"] - bb["b"]) / lines, 0.6 * unit)
            if _under_table(bb, tables, line_h) and (_NOTE_START.match(text) or (lines <= 2 and words <= 40)):
                item["label"] = "footnote"
                item["_table_note"] = True
                report["notes"] = report.get("notes", 0) + 1


def _same_row(a: Line, b: Line) -> bool:
    """Two pdfium lines on one visual row: they overlap in height and their centres sit
    within a line of each other."""
    overlap = min(a.t, b.t) - max(a.b, b.b)
    return overlap > 0.5 * min(a.height, b.height) and abs(a.cy - b.cy) < max(a.height, b.height)


def _page_no(item: dict[str, Any]) -> int | None:
    prov = item.get("prov") or []
    return int(prov[0]["page_no"]) if prov and "page_no" in prov[0] else None


def _bbox(item: dict[str, Any]) -> dict[str, float] | None:
    prov = item.get("prov") or []
    return prov[0].get("bbox") if prov and prov[0].get("bbox") else None


def _sidebar(bb: dict[str, float], unit: float = 10.0) -> bool:
    """A strip of a box taller than it is wide by far: text set up the page's margin —
    narrower than a line and a half, taller than fifteen lines."""
    w, h = abs(float(bb["r"]) - float(bb["l"])), abs(float(bb["t"]) - float(bb["b"]))
    return w < 1.6 * unit and h > 15 * unit


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _inside(line: Line, bb: dict[str, float], tol: float = 2.0) -> bool:
    return bb["l"] - tol <= line.cx <= bb["r"] + tol and bb["b"] - tol <= line.cy <= bb["t"] + tol


def _join_lines(lines: list[Line]) -> str:
    """Lines into a paragraph: a word broken at the line end is mended."""
    out = ""
    for ln in lines:
        piece = ln.text.strip()
        if not piece:
            continue
        if not out:
            out = piece
        elif (out.endswith("-") and piece[:1].islower()) or out.endswith(_SOFT_BREAK):
            out = out.rstrip("-" + _SOFT_BREAK) + piece
        else:
            out += " " + piece
    return clean(re.sub(r" {2,}", " ", out))


def _already(line: Line, page_letters: str) -> bool:
    """Whether a line's words are already in some box's text on the page: a long line must
    be found at two of its three windows, so that a legend that repeats the paragraph's
    words is not taken for the paragraph."""
    key = _letters(line.text)
    if len(key) < 16:
        return key in page_letters if key else True
    if len(key) < 40:
        return key[:16] in page_letters or key[-16:] in page_letters
    mid = len(key) // 2
    return sum(1 for w in (key[:16], key[mid - 8 : mid + 8], key[-16:]) if w in page_letters) >= 2


def _prose(line: Line) -> bool:
    words = _WORD.findall(line.text)
    return len(words) >= 4 and sum(len(w) for w in words) >= 0.55 * len(line.text.replace(" ", "")) and not _RUNNING.search(line.text)


def _rows(lines: list[Line]) -> list[Line]:
    return sorted(lines, key=lambda ln: (-ln.t, ln.l))


def _unit_of(lines_by_page: dict[int, list[Line]]) -> float:
    """The paper's body line: the median glyph-box height of its prose rows, in points —
    about three quarters of the type size. Every threshold below is a multiple of it."""
    heights = sorted(ln.height for lines in lines_by_page.values() for ln in lines if _prose(ln) and 0 < ln.height < 60)
    return heights[len(heights) // 2] if heights else 10.0


def recover(doc: dict[str, Any], lines_by_page: dict[int, list[Line]]) -> dict[str, int]:
    """The document with the text layer's evidence folded in; what changed, by kind."""
    report = {"recovered": 0, "rebuilt": 0, "formulas": 0, "attached": 0, "ligatures": 0, "tables": 0, "notes": 0}
    unit = _unit_of(lines_by_page)
    doc["_unit"] = round(unit, 2)
    texts: list[dict[str, Any]] = doc.setdefault("texts", [])
    body_children: list[dict[str, str]] = doc.setdefault("body", {}).setdefault("children", [])
    pages_meta = doc.get("pages") or {}
    indents: list[float] = []

    # every box on every page — an item that runs over a page break has one box per page;
    # a rotated sidebar ("Downloaded from …" up the margin of a Wiley page) is furniture
    # whatever the model glued to it, and its strip of a box holds nothing
    items_by_page: dict[int, list[tuple[dict[str, Any], dict[str, float]]]] = {}
    for kind in ("texts", "pictures", "tables"):
        for item in doc.get(kind) or []:
            provs = [p for p in item.get("prov") or [] if "page_no" in p and p.get("bbox")]
            if provs and _sidebar(provs[0]["bbox"], unit) and _RUNNING.search(item.get("text") or ""):
                item["_sidebar"] = True
            for prov in provs:
                if _sidebar(prov["bbox"], unit):
                    continue
                items_by_page.setdefault(int(prov["page_no"]), []).append((item, prov["bbox"]))
    _unglue_sidebars(doc, texts, body_children, items_by_page, report)

    # a line on three or more pages is a running head, whatever it says
    seen_on: dict[str, set[int]] = {}
    for page_no, lines in lines_by_page.items():
        for ln in lines:
            norm = re.sub(r"\d+", "#", ln.text.lower())
            if len(norm) > 12:
                seen_on.setdefault(norm, set()).add(page_no)
    running = {norm for norm, pages in seen_on.items() if len(pages) >= 3}

    _split_misjoined(doc, items_by_page, texts, body_children, report)

    # the lines every text box holds, over all the pages it spans; its text and geometry first,
    # so that a line the box's text lacked is the box's, not a free line to recover
    rows_of: dict[int, dict[int, list[Line]]] = {}
    item_of: dict[int, dict[str, Any]] = {}
    boxed_by_page: dict[int, list[tuple[dict[str, Any], dict[str, float], list[Line]]]] = {}
    for page_no, lines in lines_by_page.items():
        if str(page_no) not in pages_meta and page_no not in pages_meta:
            continue
        boxed = [(item, bb, [ln for ln in lines if _inside(ln, bb)]) for item, bb in items_by_page.get(page_no, [])]
        boxed_by_page[page_no] = boxed
        for item, bb, inside in boxed:
            if item.get("label") in _TEXT_LABELS:
                rows_of.setdefault(id(item), {})[page_no] = _rows(inside)
                item_of[id(item)] = item
    for iid, by_page in rows_of.items():
        item = item_of[iid]
        pages = sorted(by_page)
        all_rows = [ln for p in pages for ln in by_page[p]]
        if not all_rows:
            continue
        _geometry(item, all_rows, by_page[pages[0]], by_page[pages[-1]], indents)
        furniture = [ln for p in pages for ln in lines_by_page.get(p, []) if _RUNNING.search(ln.text) or re.sub(r"\d+", "#", ln.text.lower()) in running]
        _strip_furniture(item, furniture, report)
        _retext(item, all_rows, report, running)
    _table_notes(items_by_page, report, unit)

    for page_no, boxed in boxed_by_page.items():
        lines = lines_by_page[page_no]
        page_letters = "".join(_letters(item.get("text") or "") for item, _, _ in boxed if item.get("label") in _TEXT_LABELS)
        held: set[int] = set()
        for item, bb, inside in boxed:
            label = item.get("label")
            if label in _FURNITURE_LABELS:
                # a body block the model called furniture is not furniture: leave its prose lines free
                prose = [ln for ln in inside if _prose(ln)]
                if len(prose) >= 2 and sum(len(ln.text) for ln in prose) > 120 and not any(_RUNNING.search(ln.text) for ln in prose):
                    continue
            own = _letters(item.get("text") or "")
            is_table = item.get("data") is not None
            cells = [c for row in ((item.get("data") or {}).get("grid") or []) for c in row]
            if is_table and not cells and inside:
                # a table the model saw but could not structure: its rows from the text layer
                grid = _grid_of(inside)
                if grid:
                    item.setdefault("data", {})["grid"] = grid
                    item["_grid_from_layer"] = True
                    report["tables"] += 1
                held.update(id(ln) for ln in inside)
                continue
            if cells:
                own += "".join(_letters(c.get("text") or "") for c in cells)
            prose_inside = [ln for ln in inside if _prose(ln)]
            for ln in inside:
                if is_table and all(_already(Line(c, 0, 0, 0, 0), own) for c in _cells(ln)):
                    held.add(id(ln))  # every cell of the row is in the table's cells
                    continue
                if label in ("picture", "chart"):
                    # a text column swallowed by a wide figure box reads as prose, two lines or more; a label does not
                    if _prose(ln) and len(prose_inside) >= 2 and sum(len(_WORD.findall(p.text)) for p in prose_inside) >= 16:
                        continue
                elif label in _TEXT_LABELS or is_table:
                    if _prose(ln) and own and not _already(ln, own):
                        continue  # a table's footnote, a line the box's text lacks: free, to be a paragraph of its own
                held.add(id(ln))

        # lines no box holds — nor any box's text, since a box can sit a hair off its line — grouped
        # into blocks by their place; a block is kept when it reads as prose: its short last line
        # ("CD45-, and CD90-.") comes with it, figure labels and running heads do not
        free = [ln for ln in lines if id(ln) not in held and not _already(ln, page_letters) and re.sub(r"\d+", "#", ln.text.lower()) not in running]
        text_boxes = [(item, bb, inside) for item, bb, inside in boxed if item.get("label") in ("text", "paragraph", "list_item") and inside]
        for block in _blocks(_rows(free), unit):
            prose = [ln for ln in block if _prose(ln)]
            if not prose or len(block) - len(prose) > 1 or (len(block) > 1 and not _prose(block[0])):
                continue
            text = _join_lines(block)
            if not text:
                continue
            if _attach(block, text, text_boxes, report):
                continue
            if len(_WORD.findall(text)) < 8:
                continue
            new_index = len(texts)
            item = {
                "self_ref": f"#/texts/{new_index}",
                "parent": {"$ref": "#/body"},
                "children": [],
                "label": "text",
                "text": text,
                "prov": [{"page_no": page_no, "bbox": {"l": min(ln.l for ln in block), "t": max(ln.t for ln in block), "r": max(ln.r for ln in block), "b": min(ln.b for ln in block), "coord_origin": "BOTTOMLEFT"}, "charspan": [0, len(text)]}],
                "_recovered": True,
            }
            _geometry(item, block, block, block, indents)
            texts.append(item)
            body_children.insert(_insert_at(doc, body_children, items_by_page, page_no, item["prov"][0]["bbox"]), {"$ref": item["self_ref"]})
            report["recovered"] += 1

    if indents:
        doc["_indents"] = round(sum(1 for x in indents if x > 0.6 * unit) / len(indents), 2)
    doc["_recovery"] = report
    return report


def _geometry(item: dict[str, Any], rows: list[Line], first_rows: list[Line], last_rows: list[Line], indents: list[float]) -> None:
    """First-line indent from the block's first page, last-line width from its last."""
    item["_lines"] = len(rows)
    if len(rows) < 2:
        return
    left = min(ln.l for ln in rows)
    width = max(ln.r for ln in rows) - left
    if len(first_rows) >= 2:
        item["_first_indent"] = round(first_rows[0].l - min(ln.l for ln in first_rows[1:]), 1)
        if item.get("label") in ("text", "paragraph") and len(first_rows) >= 3:
            indents.append(item["_first_indent"])
    last = last_rows[-1] if last_rows else rows[-1]
    item["_last_full"] = width > 0 and (last.r - last.l) / width >= 0.85


def _strip_furniture(item: dict[str, Any], furniture: list[Line], report: dict[str, int]) -> None:
    """A running head or footer the layout model folded into the block — "www.advmat.de
    rotating…", "…plays N.Y. Patrawalla et al." — taken off either end of its text."""
    if item.get("label") not in _REBUILDABLE or not furniture:
        return
    text = " ".join((item.get("text") or "").split())
    low = text.lower()
    changed = False
    for _ in range(3):
        hit = False
        for ln in furniture:
            piece = " ".join(ln.text.split()).lower()
            if len(piece) < 6:
                continue
            if low.startswith(piece) and len(low) > len(piece) + 20:
                text, low = text[len(piece):].lstrip(), low[len(piece):].lstrip()
                hit = changed = True
            elif low.endswith(piece) and len(low) > len(piece) + 20:
                text, low = text[: -len(piece)].rstrip(), low[: -len(piece)].rstrip()
                hit = changed = True
        if not hit:
            break
    if changed:
        item["text"] = text
        report["furniture"] = report.get("furniture", 0) + 1


def _retext(item: dict[str, Any], rows: list[Line], report: dict[str, int], running: set[str] | None = None) -> None:
    """The item's text from the layer when the layer has what the text lacks: missing lines,
    the words of an equation, ligatures Docling split — and without the running head the
    layout model folded into the block."""
    label = item.get("label")
    heads = [ln for ln in rows if _RUNNING.search(ln.text) or (running and re.sub(r"\d+", "#", ln.text.lower()) in running)]
    layer = _join_lines([ln for ln in rows if ln not in heads])
    have = _letters(item.get("text") or "")
    if heads and label in _REBUILDABLE and layer:
        for ln in heads:
            key = _letters(ln.text)
            if len(key) >= 8 and have.startswith(key[:8]) and _letters(layer)[:20] in have:
                item["text"] = layer
                item["_rebuilt"] = True
                report["furniture"] = report.get("furniture", 0) + 1
                return
    if label == "formula":
        if not (item.get("text") or "").strip() and layer.strip():
            item["text"] = layer
            report["formulas"] += 1
        return
    if label not in _REBUILDABLE or not layer:
        return
    layer_letters = _letters(layer)
    text = item.get("text") or ""
    if (_LIGATURE.search(text) or "^" in layer) and _alnum(layer) == _alnum(text) and not item.get("_sidebar"):
        item["text"] = layer  # the same letters and digits: the layer's words whole, its superscripts marked
        report["ligatures"] += 1
        return
    missing = [ln for ln in rows if len(_letters(ln.text)) >= 12 and _letters(ln.text)[:12] not in have and _letters(ln.text)[-12:] not in have and _prose(ln)]
    if missing and have[:20] in layer_letters and len(layer_letters) >= 0.9 * len(have):
        item["text"] = layer
        item["_rebuilt"] = True
        report["rebuilt"] += 1


def _attach(block: list[Line], text: str, text_boxes: list[tuple[dict[str, Any], dict[str, float], list[Line]]], report: dict[str, int]) -> bool:
    """A free block a line or two below a block that stops mid-sentence is that block's tail
    the box cut off; one just above a block whose first line it runs into is its head."""
    top, bottom = max(ln.t for ln in block), min(ln.b for ln in block)
    left, right = min(ln.l for ln in block), max(ln.r for ln in block)
    height = max((ln.height for ln in block), default=8)
    for item, bb, inside in text_boxes:
        same_column = min(right, bb["r"]) - max(left, bb["l"]) > 0.4 * (right - left)
        if not same_column:
            continue
        own = (item.get("text") or "").rstrip()
        if 0 <= bb["b"] - top <= 1.8 * height and own and own[-1] not in ".!?:;\"'”’" and len(block) <= 3:
            item["text"] = _join_lines([Line(own, 0, 0, 0, 0)] + block)
            item["_extended"] = True
            report["attached"] = report.get("attached", 0) + 1
            return True
        if 0 <= bottom - bb["t"] <= 1.8 * height and text and text[-1] not in ".!?:;\"'”’" and len(block) <= 3 and own[:1].islower():
            item["text"] = _join_lines(block + [Line(own, 0, 0, 0, 0)])
            item["_extended"] = True
            report["attached"] = report.get("attached", 0) + 1
            return True
    return False


def _blocks(rows: list[Line], unit: float = 10.0) -> list[list[Line]]:
    """Consecutive lines at one left edge with normal spacing are one block."""
    blocks: list[list[Line]] = []
    for ln in rows:
        if blocks:
            prev = blocks[-1][-1]
            gap = prev.b - ln.t
            same_column = abs(ln.l - prev.l) < 2.4 * unit or (ln.l < prev.r and prev.l < ln.r)
            if same_column and -0.2 * unit <= gap <= 1.8 * max(prev.height, ln.height, 0.4 * unit):
                blocks[-1].append(ln)
                continue
        blocks.append([ln])
    return blocks


def _insert_at(doc: dict[str, Any], body_children: list[dict[str, str]], items_by_page: dict[int, list[tuple[dict[str, Any], dict[str, float]]]], page_no: int, bb: dict[str, float]) -> int:
    """Where a recovered block goes in the body: after the item above it in its column — an
    item's box on this page, even when the item began on the page before — else before the
    item below it, else where the page's items begin."""
    index_of = {ref.get("$ref", ""): idx for idx, ref in enumerate(body_children)}
    positions = [(index_of[item.get("self_ref", "")], ibb) for item, ibb in items_by_page.get(page_no, []) if item.get("self_ref", "") in index_of]
    overlaps = [(idx, ibb) for idx, ibb in positions if min(bb["r"], ibb["r"]) - max(bb["l"], ibb["l"]) > 0.4 * (bb["r"] - bb["l"])]
    tol = 0.2 * doc.get("_unit", 10.0)
    above = [(idx, ibb) for idx, ibb in overlaps if ibb["b"] >= bb["t"] - tol]
    if above:
        return max(above, key=lambda x: (x[1]["b"], x[0]))[0] + 1  # the nearest item above, in the same column
    below = [(idx, ibb) for idx, ibb in overlaps if ibb["t"] <= bb["b"] + tol]
    if below:
        return min(below, key=lambda x: (-x[1]["t"], x[0]))[0]
    if positions:
        return min(idx for idx, _ in positions)
    last_before = -1
    for idx, ref in enumerate(body_children):
        item = _resolve(doc, ref.get("$ref", ""))
        p = _page_no(item) if item else None
        if p is not None and p < page_no:
            last_before = idx
    return last_before + 1


def _resolve(doc: dict[str, Any], ref: str) -> dict[str, Any] | None:
    parts = ref.lstrip("#/").split("/")
    if len(parts) != 2 or not parts[1].isdigit():
        return None
    items = doc.get(parts[0])
    if not isinstance(items, list) or int(parts[1]) >= len(items):
        return None
    return items[int(parts[1])]


def recover_from_pdf(doc: dict[str, Any], path: Path | None) -> dict[str, int]:
    """`recover` over the file's own text layer; nothing when there is no PDF."""
    if path is None or not path.exists() or path.suffix.lower() != ".pdf":
        return {}
    try:
        lines = pdf_lines(path)
    except Exception:
        return {}
    return recover(doc, lines)
