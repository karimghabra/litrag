"""Typography as a witness for headings: what the page's type says a line is.

The pairs (`pairs.py`) put the PDF reader's largest loss in its headings: 82 per cent of
the XML's headings found, and what is missed is mostly one of four things — a heading
printed run in at the start of its paragraph ("2.1. Non Surgical Approach. For small tears
…", "Materials. Human amniotic membranes …"), a heading the layout model ran into the
paragraph before or after it, a heading it dropped, and a heading whose letters it split
("T endon"). The page knows all four: a heading is set apart from the body — bold, italic,
larger, or on its own numbered row — and pdfium reads the font of every glyph. So this
module reads every line of the text layer with its runs of one style, names the body's
style (the face and size that hold most of the paper's letters — the largest size that
holds a third of them, so a long reference list in a smaller size is not the body), and
says which rows and which leading spans are set apart. Measured on 64 PDF/XML pairs before
it was wired: typography alone reaches 72 per cent of the XML's headings, and with the
reader's own 91.5 (from 82), against a ceiling of 94.5 — the rest are headings the PDF
never prints.

`restyle` folds that into the Docling document, deterministically and before the tree is
built, the way `recover.py` folds in the text layer's lines:

- a heading's letters put back as the row prints them ("1 | I NTRODUCTION" → "1 | INTRODUCTION");
- a run-in heading cut from the front of its paragraph into a heading of its own;
- a heading row the layout model fused into a paragraph cut out of it, before or after a
  sentence's end;
- a heading row no box holds recovered as a heading (bold, larger or numbered — an italic
  row alone is not enough, since emphasis and species names are italic too).

- a heading that is the journal's running head, or a line of furniture, filed as the
  furniture it is.

And `depth_by_type` gives each heading its depth as the page sets it. An oracle settled that
this was the question worth most: the PDFs read again with each heading's depth taken from
its XML twin lost 61 per cent fewer words (faithful by words 0.832 → 0.935 over 199 pairs).
In paper after paper the headings were found and nested wrong — a review's own sections as
the introduction's subsections. The paper's anchors are its core sections by their own
names (Introduction, Methods, Results, Discussion, Conclusions; a first-level number only
where none of those is set apart), and their commonest look is the top level's. A heading
the rules leave in doubt is top-level when it is set at least as prominently as that in
every way the page shows — size within a twentieth (a flat capital's height, the round and
pointed ones scaled back by the paper's own overshoot), capitals, weight, an upright face,
the same face — and nested when it is set less prominently in some way. Nothing is said
where the page cannot say it: a scan's text layer in one face, numbered subsections set
like the top level, another face that measures the same, a figure's label, a running head,
the reference list's region, a back statement after the body; and a paper whose top level
is numbered keeps every unnumbered heading below it. A number the top level does not use,
set below it, is a list's (`_list_number`). A paper with no anchor at all — an editorial
under topical headings — whose headings are all set one way has one level, each heading
top-level (`_one_level`). Measured: faithful 0.832 → 0.938, above the oracle, with the
other readings of the day.

Everything it makes is marked (`_restyled`, `_runin`, `_unfused`, `_recovered_heading`,
`_running_heading`, `_typo_level`, `_look`) and counted in the recovery report, so the
tree's repairs show it and a rebuild does it again from the raw document and the file. No
model, no network: pypdfium2 on the file.
"""

from __future__ import annotations

import ctypes
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .facets import role_of
from .headings import top_level_lane
from .recover import _RUNNING, _insert_at, _inside, clean
from .facets import normalise
from .tree import _REF_ENTRY, TOP_NAMES, numbering_depth

Style = tuple[str, int, float]  # font name, weight, size (the loose glyph box's height)

_CAPTION = re.compile(r"^(Figure|Fig\.|Table|Scheme|Supplementary|Box|Chart|Plate)\s*S?\d", re.I)
_NUMBERED = re.compile(r"^\d+(?:\.\d+)*\.?\s+\S")
_UNIT = r"(?:mL|ML|mg|MPa|kPa|GPa|Pa|nm|µm|mm|cm|km|kg|kDa|Da|mM|nM|µM|min|MHz|kHz|Hz|Gy|mol|ppm|wt|vol|fold|times|days|weeks|h|s|m|g|M|N|V|W|K|dB|°C|%)\b"
_DEEP_NUMBER = re.compile(r"^\d{1,2}(?:\.\d{1,2}){1,3}\.?\s+(?![a-z])(?!" + _UNIT + r")\S")  # "2.2.1. PU Coating…", not "1.0 mL modified collagen" nor "3.74 MPa (PCL)"
_BOLDISH = re.compile(r"bold|black|heavy|semibold|demi|\.B$|,B$|-B$|BoldMT|Bd$", re.I)
_ITALIC = re.compile(r"italic|oblique|\.I$|,I$|-I$|It$|Ital", re.I)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_SENTENCE_END = (".", "?", "!", ":", ")", "]", "”", '"')
MAX_WORDS = 16  # a heading row; a run-in heading is fourteen words at most
_FLAT = set("BDEFHIKLMNPRTXZ147")  # capitals and digits that sit flat on the line and stop flat at the cap height
_ROUND = set("ACGOSUVWY0368")  # round, pointed or open at the line: they overshoot it, top or bottom
_TALL = set("QJÀÁÂÃÄÅÇÈÉÊËÌÍÎÏÑÒÓÔÕÖØÙÚÛÜÝ")


@dataclass
class Row:
    text: str
    l: float
    b: float
    r: float
    t: float
    spans: list[tuple[Style, str]] = field(default_factory=list)  # runs of one style, letters without spaces
    caps: list[list[float]] = field(default_factory=list)  # for each run, the heights of its flat capitals and digits on the page
    rounds: list[list[float]] = field(default_factory=list)  # and of its round ones (C, G, O, S), which overshoot the line

    @property
    def cx(self) -> float:
        return (self.l + self.r) / 2

    @property
    def cy(self) -> float:
        return (self.b + self.t) / 2

    @property
    def height(self) -> float:
        return self.t - self.b

    @property
    def letters(self) -> int:
        return sum(len(t) for _, t in self.spans)


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# -- the page's type ---------------------------------------------------------------------------------

def styled_rows(path: Path) -> dict[int, list[Row]]:
    """pdfium's own lines of every page, in its reading order, each with its text, its box
    and its runs of one style. A glyph pdfium knows no font for takes its neighbour's."""
    import pypdfium2.raw as raw

    from .recover import open_pdf

    out: dict[int, list[Row]] = {}
    with open_pdf(path) as pdf:
        box = raw.FS_RECTF()
        for i in range(len(pdf)):
            tp = pdf[i].get_textpage()
            n = tp.count_chars()
            if not n:
                continue
            text = tp.get_text_range(0, n)
            cache: dict[int, Style] = {}

            def face(j: int) -> tuple[str, int]:
                if j not in cache:
                    weight = raw.FPDFText_GetFontWeight(tp.raw, j)
                    buf = ctypes.create_string_buffer(96)
                    flags = ctypes.c_int()
                    m = raw.FPDFText_GetFontInfo(tp.raw, j, buf, 96, ctypes.byref(flags))
                    name = buf.value.decode("utf-8", "replace") if m else ""
                    cache[j] = (name, int(weight))
                return cache[j]

            rows: list[Row] = []
            start = 0
            for rawline in text.split("\r\n"):
                end = start + len(rawline)
                js = [j for j in range(start, min(end, n)) if not text[j].isspace()]
                start = end + 2
                line = clean(rawline)
                if not js or not line:
                    continue
                boxed = [(j, tp.get_charbox(j)) for j in js]
                boxed = [(j, b) for j, b in boxed if b[2] > b[0] and b[3] > b[1]]
                if not boxed:
                    continue
                boxes = [b for _, b in boxed]
                # the row's size is a capital's height on the page (a digit's, else any glyph's): one size for the row
                caps = sorted(b[3] - b[1] for j, b in boxed if text[j].isupper() or text[j].isdigit())
                heights = caps or sorted(b[3] - b[1] for b in boxes)
                size = round(heights[len(heights) // 2] * 2) / 2
                faces = [face(j) for j in js]
                known = [f for f in faces if f[0]]
                if not known:
                    continue
                last = known[0]  # a glyph pdfium knows no font for takes its neighbour's: the one before, else the first
                heights_of = {j: b[3] - b[1] for j, b in boxed}
                spans: list[list[Any]] = []
                span_caps: list[list[float]] = []
                span_round: list[list[float]] = []
                for j, f in zip(js, faces):
                    f = f if f[0] else last
                    last = f
                    st: Style = (f[0], f[1], size)
                    if spans and spans[-1][0] == st:
                        spans[-1][1] += text[j]
                    else:
                        spans.append([st, text[j]])
                        span_caps.append([])
                        span_round.append([])
                    if text[j] in _FLAT and j in heights_of:
                        span_caps[-1].append(heights_of[j])  # a flat capital's height: Q and J reach below the line, an accent above it
                    elif text[j] in _ROUND and j in heights_of:
                        span_round[-1].append(heights_of[j])  # a round one overshoots the line by a few per cent: kept apart, and scaled by the paper's own overshoot
                rows.append(Row(line, min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes), [(s, t) for s, t in spans], span_caps, span_round))
            out[i + 1] = rows
    return out


def body_style(rows_by_page: dict[int, list[Row]]) -> Style | None:
    """The face that holds most of the paper's letters, at the largest size that holds a
    third of them."""
    census: Counter[Style] = Counter()
    for rows in rows_by_page.values():
        for row in rows:
            for s, t in row.spans:
                census[s] += len(t)
    if not census:
        return None
    faces: Counter[tuple[str, int]] = Counter()
    for (name, weight, size), n in census.items():
        faces[(name, weight)] += n
    face = faces.most_common(1)[0][0]
    sizes = Counter({size: n for (name, weight, size), n in census.items() if (name, weight) == face})
    total = sum(sizes.values())
    size = max((sz for sz, n in sizes.items() if n >= 0.3 * total), default=sizes.most_common(1)[0][0])
    return (face[0], face[1], size)


def apart(style: Style, body: Style) -> str | None:
    """How a style stands apart from the body's: larger, bold, italic — or not at all."""
    name, weight, size = style
    bname, bweight, bsize = body
    if size >= bsize + 1.5:
        return "larger"
    if weight > bweight and weight >= 500:
        return "bold"
    if _BOLDISH.search(name) and not _BOLDISH.search(bname):
        return "bold"
    if _ITALIC.search(name) and not _ITALIC.search(bname):
        return "italic"
    return None


def dominant(row: Row) -> Style:
    return Counter({s: len(t) for s, t in row.spans}).most_common(1)[0][0]


def lead_of(row: Row, body: Style) -> tuple[int, str | None]:
    """The letters at the row's front set apart from the body, and how."""
    n, how = 0, None
    for s, t in row.spans:
        h = apart(s, body)
        if h and s != body:
            n += len(t)
            how = how or h
        else:
            break
    return n, how


def wordy(text: str) -> bool:
    """Shaped like words, not a reference entry, not a figure's furniture."""
    letters = sum(ch.isalpha() for ch in text)
    if letters < 0.6 * max(1, len(text.replace(" ", ""))):
        return False
    if " et al" in text or len(_YEAR.findall(text)) >= 2 or _RUNNING.search(text):
        return False
    return not any(len(w) == 1 and w.isalpha() and w not in ("a", "A", "I") for w in text.split()[:3])


def heading_kind(row: Row, body: Style, full: float = 0.0) -> str | None:
    """A whole row set apart from the body, shaped like a heading: "bold", "italic", "larger"
    — or "numbered" for a row in the body's style under a deep numbering ("2.2.1. PU Coating
    onto the Bare Polyester Fabric"), the one heading a journal sets like its text. `full` is
    the page's widest row: a heading is shorter than the column unless it is numbered."""
    text = row.text.strip()
    if not text or _CAPTION.match(text) or not wordy(text):
        return None
    nwords = len(text.split())
    if not 1 <= nwords <= MAX_WORDS or text.endswith((":", ";", ",")):
        return None
    if text.endswith(".") and not _NUMBERED.match(text):
        return None
    lead, how = lead_of(row, body)
    if how and lead >= 0.9 * row.letters:
        if full and row.r - row.l >= 0.92 * full and not _NUMBERED.match(text) and not row.text.count(" ") == 0:
            return None
        return how
    if _DEEP_NUMBER.match(text) and nwords <= 14 and not text.endswith(".") and not (full and row.r - row.l >= 0.92 * full):
        return "numbered"
    return None


def run_in_of(row: Row, body: Style) -> tuple[str, str] | None:
    """The heading a row opens with, printed run in: its front set apart from the body and
    closed by a full stop or a colon within fourteen words — (heading, how)."""
    lead, how = lead_of(row, body)
    if not lead or not how or lead >= 0.9 * row.letters:
        return None
    count, cut = 0, None
    for i, ch in enumerate(row.text):
        if not ch.isspace():
            count += 1
        if count == lead:
            cut = i + 1
            break
    if cut is None:
        return None
    head, after = row.text[:cut].strip(), row.text[cut:].lstrip()
    if not (head.endswith((".", ":")) or after[:1] in ".:"):
        return None
    head = head.rstrip(".:").strip()
    if not head or not 1 <= len(head.split()) <= 14 or not wordy(head) or _CAPTION.match(head):
        return None
    return head, how


def merge_wrapped(rows: list[Row], body: Style) -> list[Row]:
    """A heading wrapped over two rows — both set apart in one style, close, the first not
    closed by punctuation — read as one row."""
    out: list[Row] = []
    skip = False
    for k, row in enumerate(rows):
        if skip:
            skip = False
            continue
        if k + 1 < len(rows):
            nxt = rows[k + 1]
            dom, dom2 = dominant(row), dominant(nxt)
            if dom == dom2 and apart(dom, body) and not row.text.rstrip().endswith((".", ";")) and not _NUMBERED.match(nxt.text) and nxt.r - nxt.l <= row.r - row.l + 2 and 0 <= row.b - nxt.t <= 1.2 * row.height and abs(nxt.l - row.l) < 2 * row.height and len(row.text.split()) + len(nxt.text.split()) <= MAX_WORDS:
                out.append(Row(row.text.rstrip() + " " + nxt.text.strip(), min(row.l, nxt.l), nxt.b, max(row.r, nxt.r), row.t, row.spans + nxt.spans, row.caps + nxt.caps, row.rounds + nxt.rounds))
                skip = True
                continue
        out.append(row)
    return out


# -- the document, restyled ------------------------------------------------------------------------

_TEXTLIKE = {"text", "paragraph", "list_item"}
_BODY_LANES = {"introduction", "methods", "results", "results-discussion", "discussion"}


def _known(head: str) -> bool:
    return bool(top_level_lane(head)) or role_of(head, meaning=False) != "other"


def _back_word(head: str) -> bool:
    """A heading that names back matter: "Funding", "Note", "Abbreviations", "Acknowledgments"."""
    return top_level_lane(head) in ("back", "references") or role_of(head, meaning=False) in ("back", "references")


def _body_lane(head: str) -> bool:
    return top_level_lane(head) in _BODY_LANES or role_of(head, meaning=False) in _BODY_LANES


def last_body_heading(pages: list[tuple[int, list[Row]]], body: Style) -> tuple[int, int]:
    """Where the body's last heading stands — a numbered row, or a whole row that names a body
    lane — as (page, row index). A back-matter word before it is a table's note or a figure's
    legend, not the paper's back matter."""
    last = (0, -1)
    for page_no, rows in pages:
        widths = sorted(r.r - r.l for r in rows if len(r.text.split()) >= 8)
        full = widths[len(widths) // 2] if widths else 0.0
        for k, row in enumerate(rows):
            kind = heading_kind(row, body, full)
            if kind in ("bold", "larger", "numbered") and (_NUMBERED.match(row.text) or _body_lane(row.text)):
                last = (page_no, k)
    return last


def _cut_letters(text: str, letters: int) -> int | None:
    """The index in `text` after its first `letters` alphanumerics."""
    n = 0
    for i, ch in enumerate(text):
        if ch.isalnum():
            n += 1
            if n == letters:
                return i + 1
    return None


def _header(doc: dict[str, Any], text: str, page_no: int, bb: dict[str, float], mark: str) -> dict[str, Any]:
    texts = doc.setdefault("texts", [])
    item = {
        "self_ref": f"#/texts/{len(texts)}",
        "parent": {"$ref": "#/body"},
        "children": [],
        "label": "section_header",
        "level": 1,
        "text": text,
        "prov": [{"page_no": page_no, "bbox": dict(bb), "charspan": [0, len(text)]}],
        mark: True,
    }
    texts.append(item)
    return item


#: a page's folio, however the journal writes it: "7 of 9", "12/48", "Page 3". No length floor and
#: no recurrence — one is enough, and a folio is never a heading. `tree.py` keeps the same shape for
#: the blocks the layout model hands over.
_FOLIO = re.compile(r"^\s*(?:page\s*)?\d{1,4}\s*(?:of|/|\||\u2013|\u2014|-)\s*\d{1,4}\s*$|^\s*page\s+\d{1,4}\s*$", re.I)


def _caps_headings(pages: list[tuple[int, list[Row]]], body: Style, items_by_page: dict[int, list[tuple[dict[str, Any], dict[str, float]]]]) -> bool:
    """Whether the paper sets its headings in capitals in the body's own face, marked by nothing
    else: three or more of the headings the layout model did find are such lines (ASTMH). In such
    a paper a line of capitals the model read as text is a heading it missed; anywhere else
    capitals say nothing — an author list, a notice and a table's head are set in them too."""
    n = 0
    for page_no, rows in pages:
        boxed = [(item, bb) for item, bb in items_by_page.get(page_no, []) if item.get("label") == "section_header" and item.get("text")]
        for row in rows:
            text = row.text.strip()
            if not text.isupper() or len(text.split()) < 2 or heading_kind(row, body, 0.0) is not None:
                continue
            if any(_inside(row, bb) for _, bb in boxed):
                n += 1
    return n >= 3


def restyle(doc: dict[str, Any], rows_by_page: dict[int, list[Row]]) -> dict[str, int]:
    """The typography's word folded into the document; what changed, by kind."""
    report = {"retexted_headings": 0, "run_in_headings": 0, "unfused_headings": 0, "recovered_headings": 0, "running_headings": 0, "caps_headings": 0, "folio_headings": 0}
    body = body_style(rows_by_page)
    if body is None:
        return report
    body_children: list[dict[str, str]] = doc.setdefault("body", {}).setdefault("children", [])
    items_by_page: dict[int, list[tuple[dict[str, Any], dict[str, float]]]] = {}
    for kind in ("texts", "pictures", "tables"):
        for item in doc.get(kind) or []:
            for prov in item.get("prov") or []:
                if "page_no" in prov and prov.get("bbox"):
                    items_by_page.setdefault(int(prov["page_no"]), []).append((item, prov["bbox"]))
    seen_on: dict[str, set[int]] = {}
    for page_no, rows in rows_by_page.items():
        for row in rows:
            norm = re.sub(r"\d+", "#", row.text.lower())
            if len(norm) > 12:
                seen_on.setdefault(norm, set()).add(page_no)
    running = {norm for norm, pages in seen_on.items() if len(pages) >= 3}
    # a heading that is the journal's running head ("Journal of Hand Surgery Global Online" on every
    # page) or a line of furniture is no heading: furniture, as the layout model files a page header
    for item in doc.get("texts") or []:
        text = (item.get("text") or "").strip()
        if item.get("label") != "section_header" or not text:
            continue
        first_page = any(int(pv.get("page_no") or 0) == 1 for pv in item.get("prov") or [])
        title_like = len(text.split()) >= 4  # a title is longer than a banner ("Chemical Science", "RESEARCH ARTICLE")
        folio = bool(_FOLIO.match(text))
        if _RUNNING.search(text) or folio or (re.sub(r"\d+", "#", text.lower()) in running and not _known(text) and not (first_page and title_like)):
            # on the first page a heading the running heads repeat is the title — but only when it reads
            # like one: three words or fewer there is the journal's banner, which owns no prose
            item["label"] = "page_header"
            item["_running_heading"] = True
            report["folio_headings" if folio else "running_headings"] += 1

    def index_of(item: dict[str, Any]) -> int | None:
        ref = item.get("self_ref", "")
        for idx, child in enumerate(body_children):
            if child.get("$ref") == ref:
                return idx
        return None

    run_ins_by_item: dict[str, tuple[str, dict[str, float], int]] = {}
    body_started = False  # the abstract's labels ("Background:", "Methods:") and the keywords are set like run-in headings and are none
    refs_started = False
    pages = [(page_no, merge_wrapped(rows, body)) for page_no, rows in sorted(rows_by_page.items())]
    last_body = last_body_heading(pages, body)
    caps_convention = _caps_headings(pages, body, items_by_page)
    for page_no, rows in pages:
        widths = sorted(r.r - r.l for r in rows if len(r.text.split()) >= 8)
        full = widths[len(widths) // 2] if widths else 0.0  # the column's width: what a prose row spans
        body_started = body_started or page_no >= 2
        boxed = items_by_page.get(page_no, [])
        text_items = [(item, bb) for item, bb in boxed if item.get("label") in _TEXTLIKE | {"section_header"} and item.get("text")]
        page_letters = "".join(_alnum(item.get("text") or "") for item, _ in boxed if item.get("text"))
        held: set[int] = set()
        for k, row in enumerate(rows):
            norm = re.sub(r"\d+", "#", row.text.lower())
            if norm in running:
                held.add(id(row))
                continue
            key = _alnum(row.text)
            if len(key) < 3:
                continue
            kind = heading_kind(row, body, full)
            if kind is None and caps_convention and row.text.strip().isupper() and 2 <= len(row.text.split()) <= MAX_WORDS and wordy(row.text) and not _CAPTION.match(row.text.strip()) and not row.text.strip().endswith((".", ":", ";", ",")) and (not full or row.r - row.l < 0.92 * full):
                kind = "caps"  # this paper's own convention, and the layout model read this one as text
            if kind in ("bold", "larger", "numbered") and not body_started and (_NUMBERED.match(row.text) or _alnum(row.text) == "introduction"):
                body_started = True
            if kind in ("bold", "larger") and not refs_started and (top_level_lane(row.text) == "references" or role_of(row.text, meaning=False) == "references"):
                refs_started = True  # the list, and after it nothing is cut: an entry's italics, "How to cite this article"
                held.add(id(row))
                continue
            run_in = run_in_of(row, body) if body_started and not refs_started else None
            if not body_started or refs_started:
                kind = None
            # a back-matter word inside the body — "Note:", "Abbreviations:" under a table — is not the paper's back matter
            in_body = (page_no, k) < last_body
            if in_body and run_in is not None and _back_word(run_in[0]):
                run_in = None
            if in_body and kind is not None and _back_word(row.text):
                kind = None
            for item, bb in text_items:
                if not _inside(row, bb):
                    continue
                held.add(id(row))
                own = _alnum(item["text"])
                if item.get("label") == "section_header":
                    # the row prints the heading's letters whole: take its spelling
                    if key == own and row.text.strip() != item["text"].strip():
                        item["text"] = row.text.strip()
                        item["_restyled"] = True
                        report["retexted_headings"] += 1
                    break
                if run_in is not None and own.startswith(_alnum(run_in[0])) and len(_alnum(run_in[0])) < len(own) and item.get("self_ref") not in run_ins_by_item:
                    run_ins_by_item[item["self_ref"]] = (run_in[0], {"l": row.l, "b": row.b, "r": row.r, "t": row.t}, page_no)
                    break
                if kind == "caps":
                    # capitals alone relabel a block whose whole text is the heading — wrapped over two
                    # rows, centred, as a journal sets them — and never cut a block in two
                    whole = item["text"].strip()
                    if item.get("label") not in ("section_header", "list_item") and whole.isupper() and 2 <= len(whole.split()) <= MAX_WORDS and own.startswith(key) and not _REF_ENTRY.match(whole) and not _CAPTION.match(whole):
                        item["label"] = "section_header"
                        item["level"] = 1
                        item["_restyled"] = True
                        report["caps_headings"] = report.get("caps_headings", 0) + 1
                    break
                if kind is not None:
                    if item.get("label") == "list_item" or _REF_ENTRY.match(item["text"]) or len(_YEAR.findall(item["text"])) >= 3:
                        break  # a reference entry, or a list: nothing to cut out
                    if not (row.text[:1].isupper() or row.text[:1].isdigit()) or (len(row.text.split()) < 2 and not _known(row.text)) or row.text.isupper() and len(row.text.split()) < 2:
                        break  # a heading begins with a capital and is more than a name or an acronym ("GERAD", "Demgene": a consortium's members in bold)
                    pos = own.find(key)
                    if pos < 0:
                        break
                    if pos == 0 and key == own:
                        if item.get("label") != "section_header" and kind != "italic":
                            item["label"] = "section_header"
                            item["level"] = 1
                            item["_restyled"] = True
                            item["text"] = row.text.strip()
                            report["unfused_headings"] += 1
                        break
                    cut_from = _cut_letters(item["text"], pos) if pos else 0
                    cut_to = _cut_letters(item["text"], pos + len(key))
                    if cut_from is None or cut_to is None:
                        break
                    while cut_from < len(item["text"]) and not item["text"][cut_from].isalnum():
                        cut_from += 1  # the heading begins at its first letter; the sentence's full stop stays with the sentence
                    before, after = item["text"][:cut_from].rstrip(), item["text"][cut_to:].lstrip(" .:;")
                    if before and not before.endswith(_SENTENCE_END):
                        break  # a heading inside a paragraph follows a sentence's end; an emphasised phrase does not
                    if after and not (after[0].isupper() or after[0].isdigit() or after[0] in "\"\u201c(["):
                        break  # and a sentence begins after it; a row cut short by the layout is no heading
                    if not after and not before:
                        break
                    idx = index_of(item)
                    if idx is None:
                        break
                    bb_row = {"l": row.l, "b": row.b, "r": row.r, "t": row.t}
                    header = _header(doc, row.text.strip(), page_no, bb_row, "_unfused")
                    if before and after:
                        item["text"] = before
                        tail = {k: v for k, v in item.items() if k in ("parent", "label")}
                        tail.update({"self_ref": f"#/texts/{len(doc['texts'])}", "children": [], "text": after, "prov": [{"page_no": page_no, "bbox": {"l": bb["l"], "b": bb["b"], "r": bb["r"], "t": row.b}, "charspan": [0, len(after)]}], "_unfused_tail": True})
                        doc["texts"].append(tail)
                        body_children.insert(idx + 1, {"$ref": header["self_ref"]})
                        body_children.insert(idx + 2, {"$ref": tail["self_ref"]})
                        text_items.append((tail, tail["prov"][0]["bbox"]))
                    elif after:
                        item["text"] = after
                        body_children.insert(idx, {"$ref": header["self_ref"]})
                    else:
                        item["text"] = before
                        body_children.insert(idx + 1, {"$ref": header["self_ref"]})
                    report["unfused_headings"] += 1
                    break
            else:
                if kind in ("bold", "larger", "numbered") and id(row) not in held and key not in page_letters and (len(row.text.split()) >= 2 or _known(row.text)) and not row.text.isupper() and not any(_inside(row, bb) for item, bb in boxed if item.get("label") in ("picture", "table", "chart")):
                    bb_row = {"l": row.l, "b": row.b, "r": row.r, "t": row.t}
                    header = _header(doc, row.text.strip(), page_no, bb_row, "_recovered_heading")
                    body_children.insert(_insert_at(doc, body_children, items_by_page, page_no, bb_row), {"$ref": header["self_ref"]})
                    items_by_page.setdefault(page_no, []).append((header, bb_row))
                    report["recovered_headings"] += 1
    # run-in headings: cut from the front of their paragraphs. A series of three or more in a
    # row — an author list, a glossary, a figure's labels — keeps only the ones the vocabulary
    # knows (MDPI's "Funding:", "Data Availability Statement:"), never a name
    refs = [child.get("$ref", "") for child in body_children]
    order = [ref for ref in refs if ref in run_ins_by_item]
    keep: set[str] = set()
    k = 0
    while k < len(order):
        # a series: consecutive body children each with a run-in
        j = k
        while j + 1 < len(order) and refs.index(order[j + 1]) == refs.index(order[j]) + 1:
            j += 1
        series = order[k : j + 1]
        for ref in series:
            head = run_ins_by_item[ref][0]
            if len(series) < 3 or _known(head) or _NUMBERED.match(head):
                keep.add(ref)
        k = j + 1
    for ref in order:
        if ref not in keep:
            continue
        head, bb_row, page_no = run_ins_by_item[ref]
        item = next((it for it in doc.get("texts") or [] if it.get("self_ref") == ref), None)
        idx = index_of(item) if item else None
        if item is None or idx is None:
            continue
        cut = _cut_letters(item["text"], len(_alnum(head)))
        if cut is None:
            continue
        while cut < len(item["text"]) and not item["text"][cut].isalnum() and not item["text"][cut].isspace():
            cut += 1  # the heading's closing bracket or full stop goes with it
        rest = item["text"][cut:].lstrip(" .:;—–-")
        if not rest:
            continue
        item["text"] = rest
        header = _header(doc, head, page_no, bb_row, "_runin")
        body_children.insert(idx, {"$ref": header["self_ref"]})
        report["run_in_headings"] += 1
    return report


# -- a heading's depth from its type ------------------------------------------------------------------

@dataclass(frozen=True)
class Look:
    """How a heading is set: its face, weight, capital height, and whether it is in
    capitals, bold, italic."""

    face: str
    weight: int
    size: float
    caps: bool
    bold: bool
    italic: bool


def overshoot(rows_by_page: dict[int, list[Row]]) -> float:
    """How much taller the paper's round capitals measure than its flat ones, from the runs
    that have both: the factor a heading with only round capitals ("Cells", "Scaffold") is
    brought back to a flat capital's height by."""
    ratios = []
    for rows in rows_by_page.values():
        for row in rows:
            for flat, rnd in zip(row.caps, row.rounds):
                if flat and rnd:
                    f, r = sorted(flat)[len(flat) // 2], sorted(rnd)[len(rnd) // 2]
                    if f > 0:
                        ratios.append(r / f)
    ratios.sort()
    return ratios[len(ratios) // 2] if len(ratios) >= 5 else 1.03


def look_of(item: dict[str, Any], rows_by_page: dict[int, list[Row]], over: float = 1.03) -> Look | None:
    """The look of a heading item: the runs of the first row that holds it on its page — by
    its box, else by its letters — that carry its letters, a bullet or a numbering set in
    another face left aside."""
    prov = (item.get("prov") or [{}])[0]
    page_no, bb = prov.get("page_no"), prov.get("bbox")
    rows = rows_by_page.get(int(page_no), []) if page_no is not None else []
    key = _alnum(item.get("text") or "")
    if not rows or len(key) < 2:
        return None
    row = None
    if bb:
        inside = [r for r in rows if _inside(r, bb) and _alnum(r.text)[:4] and (key.startswith(_alnum(r.text)[:6]) or _alnum(r.text).startswith(key[:6]))]
        row = max(inside, key=lambda r: r.t) if inside else None
    if row is None:
        start = key[: min(len(key), 24)]
        row = next((r for r in rows if _alnum(r.text).startswith(start)), None)
    if row is None:
        return None
    want = len(key)
    picked: list[tuple[Style, str, list[float]]] = []
    rounds: list[float] = []
    got = 0
    for (st, t), hs, rs in zip(row.spans, row.caps or [[] for _ in row.spans], row.rounds or [[] for _ in row.spans]):
        if not picked and not any(ch.isalpha() for ch in t):
            continue  # a bullet, or a numbering in another face
        picked.append((st, t, hs))
        rounds.extend(rs)
        got += sum(ch.isalnum() for ch in t)
        if got >= want:
            break
    if not picked:
        return None
    by: Counter[Style] = Counter()
    for st, t, _ in picked:
        by[st] += sum(ch.isalpha() for ch in t)
    face, weight, _ = by.most_common(1)[0][0]
    heights = sorted(h for st, t, hs in picked for h in hs) or sorted(h / over for h in rounds)
    if not heights:
        return None
    size = round(heights[len(heights) // 2], 2)
    letters = [ch for _, t, _ in picked for ch in t if ch.isalpha()]
    caps = len(letters) >= 3 and sum(ch.isupper() for ch in letters) >= 0.9 * len(letters)
    return Look(face, weight, size, caps, weight >= 600 or bool(_BOLDISH.search(face)), bool(_ITALIC.search(face)))


def _name(text: str) -> str:
    """A heading's words for the core names: its numbering, case and invisible marks aside."""
    return normalise(re.sub("[\u200b-\u200d\u2060\ufeff]", "", text))


def _family(face: str) -> str:
    """"MGWKXF+Corbel-Bold" → "corbel-bold": the face, its subset's tag aside."""
    return face.split("+", 1)[-1].lower()


def promotes(a: Look, b: Look) -> bool:
    """Whether `a` is set at least as prominently as `b` in every way the page shows: its size
    within a twentieth or larger, capitals where `b` has them, bold where `b` is, upright where
    `b` is, and the same face — a heading in another face that measures the same is no
    evidence of anything."""
    if a.size < b.size * 0.95 or (b.caps and not a.caps) or (b.bold and not a.bold) or (a.italic and not b.italic):
        return False
    if a.weight and b.weight and a.weight < b.weight - 50:
        return False
    return _family(a.face) == _family(b.face) or a.size >= b.size * 1.1


def demotes(a: Look, b: Look) -> bool:
    """Whether `a` is set less prominently than `b` in some way the page shows: smaller by a
    twentieth, no capitals where `b` has them, not bold where `b` is, italic where `b` is
    upright, or lighter by a weight."""
    if a.size <= b.size * 0.95:
        return True
    if (b.caps and not a.caps) or (b.bold and not a.bold) or (a.italic and not b.italic):
        return a.size < b.size * 1.1  # unless it is plainly larger
    return bool(a.weight and b.weight and a.weight <= b.weight - 100)


def prominence(a: Look, b: Look) -> int:
    """+1 when `a` is set more prominently than `b`, -1 when less, 0 when alike: a larger
    capital by a twentieth first, then capitals, then weight, then an upright face."""
    if a.size >= b.size * 1.05:
        return 1
    if a.size <= b.size * 0.95:
        return -1
    if a.caps != b.caps:
        return 1 if a.caps else -1
    if a.weight and b.weight and abs(a.weight - b.weight) >= 100:
        return 1 if a.weight > b.weight else -1  # Bold over Semibold, where the file writes the weight
    for x, y in ((a.bold, b.bold), (not a.italic, not b.italic)):
        if x != y:
            return 1 if x else -1
    return 0


def same_look(a: Look, b: Look) -> bool:
    return prominence(a, b) == 0 and prominence(b, a) == 0


def depth_by_type(doc: dict[str, Any], rows_by_page: dict[int, list[Row]], report: dict[str, int]) -> None:
    """Each heading's depth as the page sets it: the paper's anchors are the headings whose
    level is not in doubt — a core section's own name, a first-level number — and their look
    is the look of the top level. A heading the rules leave in doubt (no number, no core
    name) set as prominently as the anchors is top-level, one set less prominently is nested:
    `_typo_level`, which `infer_level` takes over its own guess. Where the paper has no
    anchor, headings all set one way are one level (`_one_level`); nothing is said where the
    paper's headings take two looks and none is an anchor, or where its numbered subsections
    look like its top level (the type does not separate the levels there)."""
    families: Counter[str] = Counter()
    for rows in rows_by_page.values():
        for row in rows:
            for (face, _, _), t in row.spans:
                families[_family(face)] += len(t)
    if families and families.most_common(1)[0][1] >= 0.97 * sum(families.values()):
        report["depth_by_type_one_face"] = report.get("depth_by_type_one_face", 0) + 1
        return  # one face for everything — a scan's text layer, set in whatever font the recogniser chose: its sizes and faces say nothing of the page
    refs = {t.get("self_ref"): t for t in doc.get("texts") or []}
    heads = [refs[c.get("$ref")] for c in (doc.get("body") or {}).get("children") or [] if refs.get(c.get("$ref"), {}).get("label") == "section_header"]
    over = overshoot(rows_by_page)
    looks = {id(h): look_of(h, rows_by_page, over) for h in heads if not h.get("_built")}
    # the anchors: the core sections by their own names; a first-level number only where no core name is set
    # apart (a numbered list inside the methods — "1. Atomic-level features" — is no top level)
    named = [h for h in heads if looks.get(id(h)) is not None and _name(h.get("text") or "") in TOP_NAMES]
    numbered = [h for h in heads if looks.get(id(h)) is not None and numbering_depth(h.get("text") or "") == 1]
    anchors = [looks[id(h)] for h in (named or numbered)]
    if not anchors:
        _one_level(heads, looks, report)
        return
    tops_numbered = any(numbering_depth(h.get("text") or "") == 1 for h in named) if named else len(numbered) >= 2
    groups: list[list[Look]] = []
    for lk in anchors:
        for g in groups:
            if same_look(g[0], lk):
                g.append(lk)
                break
        else:
            groups.append([lk])
    top = max(groups, key=lambda g: (len(g), -g[0].size))[0]  # the commonest look of the core sections; between two as common, the plainer (Nature sets its methods larger than the main text's sections)
    deeper = [looks[id(h)] for h in heads if looks.get(id(h)) is not None and (numbering_depth(h.get("text") or "") or 0) >= 2]
    if len(deeper) >= 2 and sum(1 for lk in deeper if prominence(lk, top) >= 0) >= 0.5 * len(deeper):
        report["depth_by_type_unsure"] = report.get("depth_by_type_unsure", 0) + 1
        return  # the numbered subsections are set like the top level: the type does not tell the levels apart
    core = [k for k, h in enumerate(heads) if _name(h.get("text") or "") in TOP_NAMES]
    last_core = core[-1] if core else -1
    seen_on: dict[str, set[int]] = {}
    for page_no, rows in rows_by_page.items():
        for row in rows:
            norm = re.sub(r"\d+", "#", row.text.lower().strip())
            if len(norm) > 12:
                seen_on.setdefault(norm, set()).add(page_no)
    running = {norm for norm, pages in seen_on.items() if len(pages) >= 3}
    in_refs = False
    for k, h in enumerate(heads):
        text = h.get("text") or ""
        if _name(text) in TOP_NAMES:
            in_refs = False
        elif top_level_lane(text) == "references" or role_of(text, meaning=False) == "references":
            in_refs = True  # after the list, until a core section begins again (Nature's methods), nothing is set by its look: member lists, affiliations
        lk = looks.get(id(h))
        if lk is None:
            continue
        h["_look"] = [lk.face, lk.weight, lk.size, lk.caps, lk.bold, lk.italic]
        if in_refs:
            continue
        if _CAPTION.match(text) or re.sub(r"\d+", "#", text.lower().strip()) in running:
            continue  # "Figure 1" read as a heading is a figure's label, "Original research" on every page a running head: no level from their look
        if k > last_core and _back_word(text):
            continue  # a statement after the body is back matter at the top level, whatever its look; inside the body ("Ethical approval" under the methods) it follows the type
        level = 2 if demotes(lk, top) else 1 if promotes(lk, top) else None
        if level is None:
            continue  # another face that measures the same: the look says nothing, the rules decide
        number = numbering_depth(text)
        if number is not None:
            if number == 1 and level == 2 and not tops_numbered:
                h["_typo_level"], h["_list_number"] = 2, True  # a number the top level does not use, set below it: a list's, not a section's
                report["depth_by_type"] = report.get("depth_by_type", 0) + 1
            continue  # otherwise the number says the depth
        if level == 1 and tops_numbered:
            continue  # where the top level is numbered, an unnumbered heading is no top-level section, however it is set ("The Bottom Line", a box)
        h["_typo_level"] = level
        report["depth_by_type"] = report.get("depth_by_type", 0) + 1


def _one_level(heads: list[dict[str, Any]], looks: dict[int, Look | None], report: dict[str, int]) -> None:
    """A paper with no anchor — an editorial, a commentary, a perspective under topical headings
    alone — whose headings are all set one way has one level: each is top-level (a JATS file
    keeps such sections flat). Only the first page may hold a heading set larger than the rest,
    the title or the journal's banner read as one; a second look anywhere else, a number, or
    fewer than three headings in all, and nothing is said."""
    body = [h for h in heads if looks.get(id(h)) is not None and not _CAPTION.match(h.get("text") or "")]
    if len(body) < 3 or any(numbering_depth(h.get("text") or "") is not None for h in body):
        return
    groups: list[list[dict[str, Any]]] = []
    for h in body:
        for g in groups:
            if same_look(looks[id(g[0])], looks[id(h)]):
                g.append(h)
                break
        else:
            groups.append([h])
    common = max(groups, key=len)
    base = looks[id(common[0])]
    for h in body:
        if h in common:
            continue
        page = ((h.get("prov") or [{}])[0] or {}).get("page_no")
        if page != 1 or looks[id(h)].size < 1.2 * base.size:
            return  # a second look in the body: the type may separate levels the rules cannot name
    if len(common) < 2:
        return
    for h in common:
        h["_typo_level"] = 1
    report["depth_by_type_one_level"] = report.get("depth_by_type_one_level", 0) + len(common)


def restyle_from_pdf(doc: dict[str, Any], path: Path | None) -> dict[str, int]:
    """`restyle` over the file's type, then each heading's depth from its look; nothing when
    there is no PDF."""
    if path is None or not path.exists() or path.suffix.lower() != ".pdf":
        return {}
    try:
        rows = styled_rows(path)
    except Exception:
        return {}
    if not rows:
        return {}
    report = restyle(doc, rows)
    depth_by_type(doc, rows, report)
    return report
