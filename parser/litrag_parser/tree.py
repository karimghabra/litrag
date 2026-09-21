"""A DoclingDocument into litrag's node tree.

Docling gives a body of items — headers, paragraphs, tables, pictures,
captions, lists — each with a label and provenance. Section headers carry a
level but sit beside the paragraphs they head, so the hierarchy is rebuilt
here: a stack by header level, every item filed under the nearest header
above it. The role of a node is the role of the *top-level* section it is
under, inherited all the way down, so a paragraph three levels beneath
"2. Materials and Methods" is methods whatever its own subheading says.

Nothing here talks to Docling's models; it reads a finished document, so it
runs the same over a saved JSON as over a fresh conversion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

from .facets import normalise as normalise_heading, role_of, unspace
from .headings import agreed, canonical_of, top_level_lane
from .glyphs import ligature_vocabulary, repair_glyphs
from .structure import CANONICAL, build_headings, lane_sections

# Docling labels that are structure, not prose; kept as nodes with their own type.
_TABLE = {"table", "document_index"}
_PICTURE = {"picture", "chart"}
_CAPTION = {"caption"}
_HEADER = {"section_header", "title"}
_LIST = {"list_item"}
_SKIP = {"page_header", "page_footer"}  # running heads: noise for retrieval, kept out of the tree
_PROSE = {"text", "paragraph", "footnote", "formula", "code", "reference", "checkbox_selected", "checkbox_unselected", "key_value_region", "form"}


@dataclass
class Node:
    node_id: str
    parent: str | None
    ordinal: int
    depth: int
    type: str  # document | section | paragraph | table | picture | caption | list_item | formula | code | footnote
    label: str  # Docling's label, verbatim
    level: int | None  # header level for sections
    role: str
    heading: str | None  # a section's own heading
    ancestry: list[str]  # headings from the top down, this section's own excluded
    text: str
    page: int | None
    bbox: list[float] | None  # [l, t, r, b] with the page's origin top-left, in PDF points
    self_ref: str | None  # Docling's ref, for going back to the raw document
    charspan: list[int] | None = None
    table: dict[str, Any] | None = None  # {"rows": int, "cols": int, "cells": [[text,...],...]} for tables
    children: list["Node"] = field(default_factory=list)
    #: every page a node's text came from, when a join took it across a page break; `page` is the first
    pages: list[int] | None = None
    #: the catalogue's name for a section (headings.py): "Materials and methods" for "2. Experimental"; None when it has none
    canonical: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class Page:
    page_no: int
    width: float
    height: float


@dataclass
class Tree:
    title: str
    pages: list[Page]
    root: Node
    #: how many nodes landed in each role — the parse's own report card
    roles: dict[str, int]
    #: papers with no methods section are legitimate (reviews) or a parse failure; the count says which to look at
    has_methods: bool
    #: items left out on purpose, by kind — a journal's logo filed as a picture on every page — so the omission is visible
    dropped: dict[str, int] = field(default_factory=dict)
    #: the same omissions, one record each with its words: `{kind, page, text}`. The counts say
    #: how much was left out and this says what, which is what an accounting of the text layer
    #: needs — a count cannot tell a logo from a paragraph.
    dropped_items: list[dict[str, Any]] = field(default_factory=list)
    #: items put back together, by kind — a paragraph split at a page break, a run cut loose — so the mending is visible
    repairs: dict[str, int] = field(default_factory=dict)
    #: what the reader noticed and did not act on — a section whose paragraphs read as another
    #: lane than its heading names — each `{kind, node_id, page, message}`, for the audit
    notes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "pages": [asdict(p) for p in self.pages],
            "roles": self.roles,
            "has_methods": self.has_methods,
            "dropped": self.dropped,
            "dropped_items": self.dropped_items,
            "repairs": self.repairs,
            "notes": self.notes,
            "root": self.root.to_dict(),
        }

    def walk(self) -> Iterable[Node]:
        stack = [self.root]
        while stack:
            n = stack.pop()
            yield n
            stack.extend(reversed(n.children))



def _drop(dropped: dict[str, int], items: list[dict[str, Any]], kind: str, text: str = "", page: Any = None) -> None:
    """Count an omission and keep what it said, so the words can be accounted for later."""
    dropped[kind] = dropped.get(kind, 0) + 1
    items.append({"kind": kind, "page": page, "text": (text or "").strip()})


def _bbox_top_left(prov: dict[str, Any], page: Page | None) -> list[float] | None:
    b = prov.get("bbox")
    if not b:
        return None
    l, t, r, btm = float(b["l"]), float(b["t"]), float(b["r"]), float(b["b"])
    origin = b.get("coord_origin", "BOTTOMLEFT")
    if origin == "BOTTOMLEFT" and page is not None:
        # Docling's default: y grows upward from the page's bottom edge.
        t, btm = page.height - t, page.height - btm
    return [round(l, 2), round(min(t, btm), 2), round(r, 2), round(max(t, btm), 2)]


def _first_prov(item: dict[str, Any]) -> dict[str, Any] | None:
    prov = item.get("prov") or []
    return prov[0] if prov else None


def _pages_of(item: dict[str, Any]) -> list[int] | None:
    """Every page an item's text came from: Docling gives a paragraph that runs over a page
    break one provenance entry per page, and a join here adds the pages of the parts."""
    pages = {int(p["page_no"]) for p in (item.get("prov") or []) if "page_no" in p}
    pages.update(int(p) for p in (item.get("_pages") or []))
    return sorted(pages) if len(pages) > 1 else None


def _box_key(prov: dict[str, Any]) -> tuple[int, ...]:
    b = prov["bbox"]
    return tuple(round(float(b[k]) / 4) for k in ("l", "t", "r", "b"))


def _decorative_pictures(doc: dict[str, Any]) -> set[str]:
    """Refs of pictures that are decoration, not figures: uncaptioned and either tiny — a
    journal's logo, a rule — or recurring at one spot on three or more pages."""
    at_spot: dict[tuple[int, ...], set[int]] = {}
    candidates: list[tuple[str, dict[str, Any]]] = []
    for pic in doc.get("pictures") or []:
        prov = _first_prov(pic)
        if pic.get("captions") or not prov or not prov.get("bbox"):
            continue
        at_spot.setdefault(_box_key(prov), set()).add(int(prov.get("page_no", 0)))
        candidates.append((pic.get("self_ref", ""), prov))
    out: set[str] = set()
    for ref, prov in candidates:
        b = prov["bbox"]
        w, h = abs(float(b["r"]) - float(b["l"])), abs(float(b["t"]) - float(b["b"]))
        if (w < 60 and h < 60) or len(at_spot[_box_key(prov)]) >= 3:
            out.add(ref)
    return out


def _recurring_furniture(doc: dict[str, Any]) -> set[str]:
    """Refs of short text items that recur on three or more pages at the same height — a
    publisher's mark ("1 3"), a running head with the page number in it ("Micromachines 2024,
    15, 851 7 of 14"), a page number the layout model did not call a footer. A running head
    stands where the page always puts it; the same words at three different heights are the
    paper's own, and only the occurrences that stand together are dropped: Diabetes Care prints
    "RESULTS" in its visual abstract, again in its structured abstract, and over the section
    itself, whose heading is no running head."""
    spots: dict[str, list[tuple[int, float, str]]] = {}
    for t in doc.get("texts") or []:
        if t.get("label") not in ("text", "paragraph", "list_item", "section_header"):
            continue
        key = re.sub(r"\d+", "#", re.sub(r"\s+", " ", (t.get("text") or "")).strip().lower())
        prov = _first_prov(t)
        if not key or len(key) > 80 or not prov:
            continue
        box = prov.get("bbox") or {}
        y = (float(box["t"]) + float(box["b"])) / 2 if "t" in box and "b" in box else -1e9  # a file without boxes: the words alone, as before
        spots.setdefault(key, []).append((int(prov.get("page_no", 0)), y, t.get("self_ref", "")))
    out: set[str] = set()
    for places in spots.values():
        for _, y, _ in places:
            together = [p for p in places if abs(p[1] - y) <= 4.0]
            if len({p[0] for p in together}) >= 3:
                out.update(p[2] for p in together)
    return out


def _table_cells(item: dict[str, Any]) -> dict[str, Any] | None:
    data = item.get("data") or {}
    grid = data.get("grid")
    if not grid:
        return None
    cells = [[(c.get("text") or "").strip() for c in row] for row in grid]
    return {"rows": len(cells), "cols": max((len(r) for r in cells), default=0), "cells": cells}


def _resolve(doc: dict[str, Any], ref: str) -> dict[str, Any] | None:
    # "#/texts/12" → doc["texts"][12]
    parts = ref.lstrip("#/").split("/")
    cur: Any = doc
    for p in parts:
        if isinstance(cur, list):
            try:
                cur = cur[int(p)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return None
    return cur if isinstance(cur, dict) else None


# Docling's JATS backend keeps `<italic>` and `<sup>` by cutting a paragraph into runs, each
# stripped of its whitespace, wrapped in an `inline` group: "solution (", "w", "/", "v",
# ") prepared". The runs are one paragraph; these are the boundaries that take no space back.
_TIGHT_LEFT = "([{/"
_TIGHT_RIGHT = ")]},.;:/%?!"


def _join_inline(fragments: list[dict[str, Any]]) -> str:
    text = ""
    for frag in fragments:
        piece = str(frag.get("text") or "")
        if not piece:
            continue
        if not text:
            text = piece
            continue
        script = (frag.get("formatting") or {}).get("script")
        tight = text[-1] in _TIGHT_LEFT or text[-1].isspace() or piece[0] in _TIGHT_RIGHT or script in ("super", "sub")
        text += ("" if tight else " ") + piece
    return text


def _inline_runs(doc: dict[str, Any], refs: list[str]) -> list[dict[str, Any]] | None:
    """The text runs under inline groups and plain text items, in order — or None if
    anything beneath is structure (a list, a table, a heading) rather than runs."""
    runs: list[dict[str, Any]] = []
    for ref in refs:
        item = _resolve(doc, ref)
        if item is None:
            continue
        kids = [c["$ref"] for c in item.get("children", []) if "$ref" in c]
        if item.get("self_ref", ref).startswith("#/groups"):
            if item.get("label") != "inline":
                return None
            sub = _inline_runs(doc, kids)
            if sub is None:
                return None
            runs.extend(sub)
        elif item.get("label") in ("text", "paragraph", "formula", "footnote") and not kids:
            runs.append(item)
        else:
            return None
    return runs


_TEXTLIKE = {"text", "paragraph"}
_SYMBOL_START = re.compile(r"[=<>/)\],;:%+|&]")


def _is_fragment(text: str) -> bool:
    """One or two characters of a word — "p", "n", "w", "/": a run the layout model cut loose."""
    return len(text) <= 4 and len(re.sub(r"\W", "", text)) <= 2


def _same_page(a: dict[str, Any], b: dict[str, Any]) -> bool:
    pa, pb = _first_prov(a), _first_prov(b)
    return (pa or {}).get("page_no") == (pb or {}).get("page_no")


_FINISHED = ".!?:;\"'\u201d\u2019"
_TAIL_WORDS = {"and", "or", "the", "of", "in", "to", "a", "an", "with", "by", "for", "that", "which", "as", "at", "on", "from", "but", "than", "et", "al", "see", "e.g", "i.e", "cf"}


def _page_of(item: dict[str, Any]) -> int | None:
    prov = _first_prov(item)
    return int(prov["page_no"]) if prov and "page_no" in prov else None


def _vertical_gap(a: dict[str, Any], b: dict[str, Any]) -> float:
    """From the bottom of box `a` down to the top of box `b`, in points, whichever way the
    page's y axis runs; negative when `b` starts above `a`'s bottom."""
    if a.get("coord_origin", "BOTTOMLEFT") == "TOPLEFT":
        return float(b["t"]) - float(a["b"])
    return float(a["b"]) - float(b["t"])


def _same_column(prev: dict[str, Any], it: dict[str, Any]) -> bool:
    """Whether two blocks stand in one column of the page: they share a third of the narrower's
    width. Their pages may differ — a paragraph carries on in the same column of the next page."""
    pa, pb = (prev.get("prov") or [None])[-1], (it.get("prov") or [None])[0]
    if not pa or not pb or not pa.get("bbox") or not pb.get("bbox"):
        return False
    a, b = pa["bbox"], pb["bbox"]
    return min(float(a["r"]), float(b["r"])) - max(float(a["l"]), float(b["l"])) > 0.3 * (float(b["r"]) - float(b["l"]))


def _stacked_apart(prev: dict[str, Any], it: dict[str, Any]) -> bool:
    """Two boxes in one column of one page with more than two lines of space between them,
    or overlapping: a figure, a heading or a paragraph break stood there, and the page's
    geometry has no word on whether the second continues the first."""
    pa, pb = (prev.get("prov") or [None])[-1], (it.get("prov") or [None])[0]
    if not pa or not pb or not pa.get("bbox") or not pb.get("bbox") or pa.get("page_no") != pb.get("page_no"):
        return False
    a, b = pa["bbox"], pb["bbox"]
    if min(float(a["r"]), float(b["r"])) - max(float(a["l"]), float(b["l"])) <= 0.3 * (float(b["r"]) - float(b["l"])):
        return False  # other columns
    line = max(abs(float(a["t"]) - float(a["b"])) / max(int(prev.get("_lines") or 1), 1), 6.0)
    gap = _vertical_gap(a, b)
    return gap > 2.2 * line or gap < -0.5 * line


def _box_of(item: dict[str, Any], pages: dict[int, "Page"]) -> list[float] | None:
    """A block's box on its page, origin top-left — None when the file gives none, or when the
    box is too small to hold the block's own text (a rotated sidebar reaches Docling as a sliver,
    and nothing about the page can be read from it)."""
    prov = _first_prov(item)
    if not prov or "page_no" not in prov:
        return None
    box = _bbox_top_left(prov, pages.get(int(prov["page_no"])))
    if not box:
        return None
    n = len((item.get("text") or "").split())
    if n >= 20 and (box[2] - box[0]) * (box[3] - box[1]) < 40 * n:
        return None  # a word of the smallest type still takes some hundred square points: the box is not this block's
    return box


def _one_column(a: list[float], b: list[float]) -> bool:
    """Whether two boxes stand in one column: they share more than half the narrower's width."""
    over = min(a[2], b[2]) - max(a[0], b[0])
    return over > 0.5 * min(a[2] - a[0], b[2] - b[0])


def _columns(blocks: list[tuple[list[float], int]]) -> list[list[float]]:
    """The page's columns as x-intervals, from the blocks that carry a paragraph of text: a
    heading, a page number or a one-line statement says nothing about how the page is set, and a
    block that spans two columns joins them, which is what a full-width figure does. A page of
    one band is set in one column."""
    bands = [[box[0], 0.0, box[2], 0.0] for box, n in sorted(blocks, key=lambda t: t[0][0]) if n >= 25]
    merged = True
    while merged:
        merged = False
        for i in range(len(bands)):
            for j in range(i + 1, len(bands)):
                if _one_column(bands[i], bands[j]):
                    bands[i] = [min(bands[i][0], bands[j][0]), 0.0, max(bands[i][2], bands[j][2]), 0.0]
                    bands.pop(j)
                    merged = True
                    break
            if merged:
                break
    return bands


def _reading_order(items: list[dict[str, Any]], pages: dict[int, "Page"], repairs: dict[str, int]) -> list[dict[str, Any]]:
    """A page read out of order, put back. Where the layout model jumps back up one column of a
    page — MDPI sets its reference list at the foot of the page and the model reads it before the
    text above it, so a conclusion files under "References" — the blocks above are read first.
    Only within one column of one page, only where a heading stands in what was read early (that
    is where the order costs a section its text), and on a page of two columns only where both
    runs stand in the same one: there the column, not the height, is the order. Pages the file
    gives no boxes for are left exactly as they came."""
    out = list(items)
    by_page: dict[int | None, list[tuple[list[float], int]]] = {}
    for it in out:
        box = _box_of(it, pages)
        if box:
            by_page.setdefault(_page_of(it), []).append((box, len((it.get("text") or "").split())))
    bands = {pg: _columns(blocks) for pg, blocks in by_page.items() if pg is not None}

    def band_of(box: list[float], page: int | None) -> int | None:
        for k, band in enumerate(bands.get(page) or []):
            if _one_column(band, box):
                return k
        return None

    i, moved = 1, 0
    while i < len(out):
        prev, it = out[i - 1], out[i]
        a, b = _box_of(prev, pages), _box_of(it, pages)
        page = _page_of(it)
        if not a or not b or _page_of(prev) != page or not _one_column(a, b) or b[3] >= a[1]:
            i += 1
            continue
        end = i
        while end + 1 < len(out):
            nxt = _box_of(out[end + 1], pages)
            if _page_of(out[end + 1]) != page or not nxt or nxt[3] >= a[1]:
                break
            end += 1
        above = out[i : end + 1]
        low = max(_box_of(x, pages)[3] for x in above)  # the lowest the blocks above reach
        start = i
        while start - 1 >= 0:
            box = _box_of(out[start - 1], pages)
            if _page_of(out[start - 1]) != page or not box or box[1] < low:
                break
            start -= 1
        early = out[start:i]
        column = len(bands.get(page) or []) < 2 or len({band_of(_box_of(x, pages), page) for x in early + above}) == 1 and band_of(b, page) is not None
        if start == i or not column or not any(x.get("label") == "section_header" for x in early) or not any(len((x.get("text") or "").split()) >= 10 for x in above):
            i = end + 1
            continue
        out[start : end + 1] = above + early
        moved += len(early)
        i = start + len(above)
    if moved:
        repairs["reordered_page"] = repairs.get("reordered_page", 0) + moved
    return out


#: How much of a paper must indent its paragraphs before an unindented first line means anything.
#: Measured 2026-09-19: at 0.0 — the full last line deciding alone, in papers that indent nothing —
#: paragraphs cut fell a little (0.041 → 0.039 of them on 127 novel pairs) and paragraphs silently
#: merged rose three and a half times (0.015 → 0.054). A merge leaves no mark in the tree; a split
#: does. The threshold stays where the measurement put it.
INDENTS_ENOUGH = 0.35


def _geometry_says(prev: dict[str, Any], it: dict[str, Any], indents: float | None, unit: float = 10.0, bridged: bool = False) -> bool | None:
    """What the page itself says about two blocks, from the text layer (recover.py):
    a block whose last line stops short ended its paragraph; in a paper that indents,
    a block whose first line is flush left continues the one before — when the two sit
    where a continuation can: at a column or page break, or one right under the other.
    None: no word.

    `bridged` — a figure, a table or a caption stands between the two — is measured and not used:
    taking the gap as the figure's and asking only that the column agree joined a paragraph's tail
    to the next paragraph's head often enough to cost `intact` on all four corpora (2026-09-19). A
    caption read between the halves is two fifths of every paragraph that arrives cut, and a fuller
    last line is not enough to recover it."""
    last_full, indent = prev.get("_last_full"), it.get("_first_indent")
    if last_full is False:
        return False
    if last_full and indent is not None and indents is not None and indents >= INDENTS_ENOUGH:
        if not _stacked_apart(prev, it):
            return indent < 0.6 * unit  # flush left: within a bit over half a line of the block's edge
        return None
    return None


def _oracle() -> Any:
    """The process-wide oracle of meaning (lanes.py), or None when none is configured."""
    from .lanes import active

    return active()


def _label_of(text: str) -> str | None:
    """The label a block opens with, if it opens with one: "Keywords", "Data availability",
    "Clinical Relevance:" — the text before a colon within six words, else the two or three
    capitalised words before a new sentence starts."""
    t = text.lstrip()
    m = re.match(r"^([^:\n]{1,60}):", t)
    if m and len(m.group(1).split()) <= 6:
        return m.group(1).strip()
    words = t.split()
    if len(words) < 3 or not words[0][0].isupper():
        return None
    for k in (2, 3):
        if len(words) > k and words[k][0].isupper() and not re.search(r"[.!?;,]$", " ".join(words[:k])) and all(w[0].isalpha() for w in words[:k]):
            return " ".join(words[:k])
    return None


def _means_label(text: str) -> bool:
    """Whether a heading-sized text means a section label ("Associated data", "Level of
    evidence") rather than a start of prose — the embedder's word, `False` without one."""
    o = _oracle()
    if o is None or not text or len(text.split()) > 6:
        return False
    return o.nearest("label", text).name == "label"


_RUN_IN = re.compile(r"^(?:Keywords?|Acknowledg\w+|Author\s+contributions?|Authors\W+contributions?|Funding|Data\s+availability|Availability\s+of\s+data|Declarations?|Conflicts?\s+of\s+interest|Competing\s+interests?|Ethics\w*|Ethical\s+\w+|Supplementary\s+\w+|Correspondence|Open\s+Access|Publisher'?s\s+Note|Abbreviations|Consent\s+\w+|Received|Accepted|Citation|Highlights|Graphical\s+abstract)\b|^(?:Conclusions?|Clinical\s+Relevance|Background|Methods|Materials\s+and\s+Methods|Results|Purpose|Objectives?|Significance|Hypothesis|Study\s+Design|Design|Setting|Interpretation|Findings|Implications|Limitations|Level\s+of\s+Evidence)\s*:", re.I)


def _continues(prev_text: str, text: str, prev_page: int | None, page: int | None, geometry: bool | None = None, repairs: dict[str, int] | None = None) -> bool:
    """Whether `text` reads as the rest of `prev_text`'s last sentence — a paragraph the
    layout model split at a page or column break, often inside a citation."""
    a, b = prev_text.rstrip(), text.lstrip()
    if not a or not b:
        return False
    if page is not None and prev_page is not None and page not in (prev_page, prev_page + 1):
        return False
    if a[-1] == "." and b[0] == "," and re.search(r"\b[A-Z][a-z]{0,6}\.$", a):
        return True  # "…Adv. Mater." || ", 2400084." — a journal's abbreviation, not a sentence's end
    if a[-1] in ".!?" and (b[0].islower() or b[0] in ")],;"):
        return False  # a sentence that ended does not continue in lowercase: that tail belongs elsewhere
    if geometry is not None and a[-1] in ".!?":
        # a full stop at the line's end: only the page can tell — unless a label ("Keywords",
        # "Funding", "Conclusion:") opens the next block. The list of labels is a first pass;
        # a label it does not know is asked of the embedder, the one place meaning vetoes a
        # measurement, because the failure is a visible split, never a silent merge.
        if not geometry or _RUN_IN.match(b):
            return False
        label = _label_of(b)
        if label is not None and _means_label(label):
            if repairs is not None:
                repairs["label_veto"] = repairs.get("label_veto", 0) + 1
            return False
        return True
    if a[-1] in _FINISHED:
        return False
    # from here the words say the sentence is unfinished; a short last line does not overrule
    # them (a figure may have cut the column short), it only keeps the judge quiet
    if a.count("(") > a.count(")") or a.count("[") > a.count("]"):
        closes = [i for i in (b.find(")"), b.find("]")) if i >= 0]
        opens = [i for i in (b.find("("), b.find("[")) if i >= 0]
        if closes and min(closes) < min(opens, default=len(b)):
            return True  # "…(Fields et al., 2022, Friston et al.," || "2023, Fields, 2024)-to optimize…"
    if b[0].islower() or b[0] in ")],;":
        return True  # a digit is not enough: "…Zheng 1, *" || "1 Beijing Institute…" are two lines of front matter
    last = re.sub(r"[^\w.]", "", a.split()[-1].lower()).rstrip(".")
    if a[-1] in ",-" or last in _TAIL_WORDS:
        return True
    return b[0] == "(" and a[-1].isalpha()  # "…of amoeboid chemotaxis" || "( K ≈ 2 ) (Sect. 5) and…"


_BRIDGEABLE = {"picture", "table", "caption", "footnote", "formula"}  # what may sit between the two halves of a paragraph: a figure at the top of a page


_CITATION_TAIL = re.compile(r"(?:\s*\[[\d,\s–\-\[\]]+\]|\s+\d{1,3}(?:[,–-]\d{1,3})*|\s*[*†‡])+$")


def _judge_candidate(a: str, b: str) -> bool:
    """A pair the rules leave alone but a reader might join: A stops mid-sentence, B starts
    with a capital, a digit or a bracket, both are prose, neither is a running line or a
    heading the layout model called text. A citation after the full stop — "…function.[8]",
    "…medicine. 29,30" — is a finished sentence, not a candidate."""
    a, b = a.rstrip(" \t\n​‌‍﻿"), b.lstrip()  # a zero-width space after the full stop is not a word
    if not a or not b:
        return False
    core = _CITATION_TAIL.sub("", a).rstrip()
    if not core or core[-1] in _FINISHED or core[-1] in ",-":
        return False
    if len(a.split()) < 12 or len(b.split()) < 8:
        return False  # a short block with no full stop is a heading the layout model called text
    if _FURNITURE.search(a) or _FURNITURE.search(b) or _looks_like_authors(b):
        return False
    return b[0].isupper() or b[0].isdigit() or b[0] == "("


def _merged(a: dict[str, Any], b: dict[str, Any], text: str) -> dict[str, Any]:
    """`a` carrying `b`'s text, and the pages of both."""
    pages = list(a.get("_pages") or ([_page_of(a)] if _page_of(a) else []))
    pb = _page_of(b)
    if pb is not None and pb not in pages:
        pages.append(pb)
    return {**a, "text": text, "children": [], "_pages": pages}


def _tail_like(text: str) -> bool:
    """A block that reads as the rest of a sentence: lowercase, or a closing bracket, or a
    comma first, with some words to it."""
    t = text.lstrip()
    return bool(t) and (t[0].islower() or t[0] in ")],;") and len(t.split()) >= 3


def _choose_head(text: str, heads: list[int], texts: list[str], repairs: dict[str, int] | None) -> int:
    """Which of several unfinished paragraphs a tail belongs to: the nearest by meaning when
    the embedder is sure, else the nearest on the page (the first offered)."""
    if len(heads) > 1:
        o = _oracle()
        if o is not None:
            v = o.which(text[:400], [t[-400:] for t in texts])
            if v.sure:
                if repairs is not None and heads[int(v.name)] != heads[0]:
                    repairs["rejoined_meaning"] = repairs.get("rejoined_meaning", 0) + 1
                return heads[int(v.name)]
    return heads[0]


def _head_like(text: str) -> bool:
    """A block a displaced tail can be the rest of: anything but a front-matter line
    ("Published", "KEYWORDS …", an author line, a licence line) that happens to end
    without a full stop — a tail glued to one of those takes its citations out of the
    body. A short cut line of prose is still a head: requiring a paragraph's worth of
    words left tails standing alone as fragments."""
    t = text.strip()
    words = t.split()
    if not words or _KEYWORDS.match(t) or _looks_like_authors(t) or _name_list(t) or _FURNITURE.search(t) or _LICENCE.search(t):
        return False
    if (_DATE_LINE.search(t) and len(words) <= 3) or (_DATE_LINE.match(t) and len(words) <= 30):
        return False  # "Received 22nd August 2026 Accepted …", "Published on 01 September 2026": a dates line, wherever it stands
    if re.search(rf"{_EMAIL}|\borcid\b", t, re.I) and len(words) <= 60:
        return False  # a correspondence line
    if _affiliation_like(t):
        return False  # an affiliation, at any length: "a Institute of Tropical Durability, …, Hanoi 100000, Vietnam"; four of them numbered
    return True


def _displaced_head(out: list[dict[str, Any]], anchor: int, it: dict[str, Any], text: str, repairs: dict[str, int] | None = None) -> int | None:
    """The page put a figure, a caption or another paragraph between a sentence's head and
    its tail: the tail's head is the nearest unfinished paragraph a few items back — and
    when more than one is unfinished, the one the tail is about."""
    if not _tail_like(text):
        return None
    if out and out[-1].get("label") == "formula":
        return None  # "where α, h, B, Eg and n represent …": the formula's own sentence goes on, from a head that ended in a colon
    page = _page_of(it)
    heads: list[int] = []
    texts: list[str] = []
    counted = 0  # eleven blocks of prose back, within twenty items: the front-matter lines between a head and its tail
    for k in range(len(out) - 1, -1, -1):  # (RSC's affiliation footnotes under the introduction's first lines) do not close the window
        if len(out) - 1 - k > 20 or counted >= 11:
            break
        if k == anchor or out[k].get("label") not in _TEXTLIKE:
            continue
        cand = out[k]
        cand_text = (cand.get("text") or "").rstrip()
        cand_page = _page_of(cand)
        if not cand_text or not _head_like(cand_text):
            continue
        counted += 1
        if cand_text[-1] in _FINISHED:
            continue
        if page is not None and cand_page is not None and page not in (cand_page, cand_page + 1):
            continue
        if _continues(cand_text, text, cand_page, page):
            heads.append(k)
            texts.append(cand_text)
    if not heads:
        return None
    return _choose_head(text, heads, texts, repairs)


def _caption_tails(doc: dict[str, Any], items: list[dict[str, Any]], repairs: dict[str, int]) -> list[dict[str, Any]]:
    """A caption the layout model cut short — its box ended a line early — followed, after
    its figure, by the rest of its sentence in lowercase: the tail goes back to the caption,
    not to the paragraph the figure interrupted."""
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(items):
        it = items[i]
        out.append(it)
        nxt = items[i + 1] if i + 1 < len(items) else None
        if nxt is not None and nxt.get("label") in _TEXTLIKE and it.get("label") in _TABLE | _PICTURE | _CAPTION | {"footnote"}:
            tail = (nxt.get("text") or "").strip()
            if _tail_like(tail):
                cut_caps: list[dict[str, Any]] = []
                for k in range(len(out) - 1, max(-1, len(out) - 5), -1):
                    cap = _caption_of(doc, out[k])
                    cap_text = (cap.get("text") or "").rstrip() if cap else ""
                    if not cap_text or any(c is cap for c in cut_caps):
                        continue
                    cut = cap_text[-1] not in _FINISHED and (cap.get("_last_full") is True or cap_text[-1] in ",-(" or cap_text.split()[-1].lower() in _TAIL_WORDS)
                    if cut and _continues(cap_text, tail, _page_of(cap), _page_of(nxt)):
                        cut_caps.append(cap)
                if cut_caps:
                    pick = _choose_head(tail, list(range(len(cut_caps))), [c["text"] for c in cut_caps], repairs)
                    cap = cut_caps[pick]
                    cap["text"] = _join_inline([cap, nxt])
                    cap["_pages"] = _merged(cap, nxt, cap["text"])["_pages"]
                    repairs["caption_tail"] = repairs.get("caption_tail", 0) + 1
                    i += 1
        i += 1
    return out


def _caption_of(doc: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
    """The item when it is a caption; a figure's or table's last caption; else nothing."""
    if item.get("label") in _CAPTION:
        return item
    if item.get("label") in _TABLE | _PICTURE:
        caps = [_resolve(doc, c["$ref"]) for c in item.get("captions", []) if "$ref" in c]
        return caps[-1] if caps and caps[-1] is not None else None
    return None


def _stitch_fragments(items: list[dict[str, Any]], repairs: dict[str, int], judge: Any = None, indents: float | None = None, unit: float = 10.0) -> list[dict[str, Any]]:
    """What the layout model cut in two is put back together.

    A fragment — an italic "p" or "n" read as its own paragraph: "…higher (", "p", "< 0.001)
    compared…" — goes back into the unfinished sentence before it, and so does the
    sentence's tail when it starts with a symbol or lowercase. A paragraph that stops
    mid-sentence at a page or column break takes the paragraph that continues it, even
    across a figure, a table or a footnote that the page put between them. The same
    paragraph twice is one. Where the rules are silent and a `judge` is given — a local
    model, see judge.py — the pair is read and its verdict followed.
    """
    out: list[dict[str, Any]] = []
    anchor = -1  # where in `out` the last paragraph sits, while only bridgeable items have followed it
    i = 0
    while i < len(items):
        it = items[i]
        label = it.get("label")
        text = (it.get("text") or "").strip()
        if label in _TEXTLIKE and text and anchor >= 0:
            prev = out[anchor]
            prev_text = (prev.get("text") or "").rstrip()
            adjacent = anchor == len(out) - 1
            if adjacent and _is_fragment(text) and _same_page(prev, it) and prev_text and prev_text[-1] not in ".!?":
                merged = _merged(prev, it, _join_inline([prev, it]))
                if i + 1 < len(items) and items[i + 1].get("label") in _TEXTLIKE and _same_page(it, items[i + 1]):
                    tail = (items[i + 1].get("text") or "").lstrip()
                    if tail and (tail[0].islower() or _SYMBOL_START.match(tail)):
                        merged = _merged(merged, items[i + 1], _join_inline([merged, items[i + 1]]))
                        i += 1
                out[anchor] = merged
                repairs["stitched"] = repairs.get("stitched", 0) + 1
                i += 1
                continue
            if _is_fragment(text) and (not adjacent or not prev_text or prev_text[-1] in ".!?") and _fragment_forward(it, items, i, out, repairs):
                i += 1
                continue
            geometry = _geometry_says(prev, it, indents, unit)
            if _continues(prev_text, text, _page_of(prev), _page_of(it), geometry, repairs):
                out[anchor] = _merged(prev, it, _join_inline([prev, it]))
                repairs["joined"] = repairs.get("joined", 0) + 1
                i += 1
                continue
            if adjacent and len(text) > 10 and (re.sub(r"\W+", " ", text).strip().lower() == re.sub(r"\W+", " ", prev_text).strip().lower() or (len(text) >= 24 and re.sub(r"\W+", "", text).lower() in re.sub(r"\W+", "", prev_text).lower())):
                repairs["deduplicated"] = repairs.get("deduplicated", 0) + 1  # the same paragraph twice, or the end of it again: a column read twice
                i += 1
                continue
            if judge is not None and geometry is None and _judge_candidate(prev_text, text) and judge(prev_text, text, {"prev_page": _page_of(prev), "page": _page_of(it)}):
                out[anchor] = _merged(prev, it, _join_inline([prev, it]))
                repairs["judged"] = repairs.get("judged", 0) + 1
                i += 1
                continue
            head = _displaced_head(out, anchor, it, text, repairs)
            if head is not None:
                out[head] = _merged(out[head], it, _join_inline([out[head], it]))
                repairs["rejoined"] = repairs.get("rejoined", 0) + 1
                i += 1
                continue
        if label in _TEXTLIKE and text and anchor < 0 and _is_fragment(text) and _fragment_forward(it, items, i, out, repairs):
            i += 1
            continue
        if label in _TEXTLIKE and text.startswith("=") and out and out[-1].get("label") == "formula":
            it = {**it, "label": "formula"}  # an equation the layout model read as text, after the one it read as a formula
            label = "formula"
            repairs["formula_text"] = repairs.get("formula_text", 0) + 1
        if label in _TEXTLIKE and text.startswith("=") and i + 1 < len(items) and items[i + 1].get("label") in _TEXTLIKE and len(text) >= 20 and re.sub(r"\W+", "", text).lower() in re.sub(r"\W+", "", items[i + 1].get("text") or "").lower():
            repairs["deduplicated"] = repairs.get("deduplicated", 0) + 1  # the same definitions again, cut in front
            i += 1
            continue
        out.append(it)
        if label in _TEXTLIKE and text:
            anchor = len(out) - 1
        elif label not in _BRIDGEABLE:
            anchor = -1  # a heading or a list item closes the paragraph before it
        i += 1
    return out


def _fragment_forward(it: dict[str, Any], items: list[dict[str, Any]], i: int, out: list[dict[str, Any]], repairs: dict[str, int]) -> bool:
    """A fragment that cannot go back — "Δ" before "EI difference" — goes onto the front of
    the block after it on the same page; one with no block on either side ("-5" beside a
    figure) is an axis label, dropped. Whether the fragment was dealt with."""
    text = (it.get("text") or "").strip()
    nxt = items[i + 1] if i + 1 < len(items) else None
    if len(text) == 1 and not text.isascii() and nxt is not None and nxt.get("label") in _TEXTLIKE and (nxt.get("text") or "").strip() and _same_page(it, nxt):
        nxt["text"] = text + (nxt.get("text") or "").lstrip()  # a Greek letter or a symbol cut from the word after it
        repairs["stitched"] = repairs.get("stitched", 0) + 1
        return True
    if nxt is None or nxt.get("label") in _PICTURE | _TABLE | _CAPTION:
        repairs["junk"] = repairs.get("junk", 0) + 1
        return True
    return False


_REF_ENTRY = re.compile(r"^(?:\[\d{1,3}\]|\d{1,3}\.)\s+\S|^[A-Z][A-Za-z'\u2019\-]+(?:,\s*|\s+)(?:[A-Z]\.?\s?){1,3}[,;.]|^[A-Z][A-Za-z'\u2019\-]+\s+[A-Z]{1,3}[,.]\s|^[A-Z][A-Za-z'\u2019\-]+,\s+[A-Z][a-z]+|^(?:[A-Z]\.\s?){1,3}[A-Z][A-Za-z'\u2019\-]+,\s")  # "[12] …", "12. …", "Smith, J. A.;", "Smith JA,", "Smith, John", "J. A. Smith," (Wiley)
_A_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def _entries_follow(items: list[dict[str, Any]], index: int, within: int = 40) -> bool:
    """Whether a reference entry stands among the items after the heading at `index`, before
    any heading that opens a body section: the heading was read between the entries of a list
    that goes on — "Generative AI statement" and "Publisher's note" set in the left column
    under the start of a Frontiers PDF's reference list; MDPI's whole block of statements,
    "Funding" to "Conflicts of Interest", set between the entries — and belongs inside it,
    whatever lane its name has."""
    for it in items[index + 1 : index + 1 + within]:
        text = (it.get("text") or "").strip()
        if it.get("label") == "section_header":
            if top_number(text) is not None or role_of(text, meaning=False) not in ("other", "back"):
                return False  # a body section begins: the list is over
            continue
        if it.get("label") in ("list_item", "text", "paragraph") and len(text) < 700 and _REF_ENTRY.match(text) and _A_YEAR.search(text):
            return True
    return False


def _infer_references(items: list[dict[str, Any]], repairs: dict[str, int]) -> list[dict[str, Any]]:
    """Some Wiley PDFs reach Docling with no "References" heading: the entries file under
    the last section and no citation can be linked. A run of eight or more entries —
    numbered, or an author's name and initials, each with a year — in the back part of
    the paper is the reference list, and a heading is put before it."""
    if any(it.get("label") == "section_header" and role_of(it.get("text") or "") == "references" for it in items):
        return items
    n = len(items)
    flags = []
    for it in items:
        text = (it.get("text") or "").strip()
        flags.append(it.get("label") in ("list_item", "text", "paragraph") and len(text) < 700 and bool(_REF_ENTRY.match(text)) and bool(_A_YEAR.search(text)))
    by_rule = list(flags)  # a run opens at an entry a pattern knows, or at a verdict the next item agrees with; a lone verdict never opens
    # an entry shaped like none of the patterns — the embedder says what it resembles, for
    # the back part of the paper, in one batch
    o = _oracle()
    if o is not None:
        maybe = [i for i, it in enumerate(items) if i >= 0.5 * n and not flags[i] and it.get("label") in ("list_item", "text", "paragraph") and 20 < len((it.get("text") or "").strip()) < 700 and _A_YEAR.search(it.get("text") or "")]
        if maybe:
            for i, v in zip(maybe, o.nearest_many("refentry", [(items[i].get("text") or "").strip() for i in maybe])):
                if v.name == "entry":
                    flags[i] = True
    best: tuple[int, int, int] | None = None
    i = 0
    while i < n:
        if not flags[i] or not (by_rule[i] or (i + 1 < n and flags[i + 1])):
            i += 1
            continue
        last = i
        j = i + 1
        while j < n:
            if flags[j]:
                last = j
                j += 1
            elif j - last <= 3 and any(flags[j : j + 4]):
                j += 1  # an entry the pattern misses, in the middle of the list
            else:
                break
        count = sum(1 for k in range(i, last + 1) if flags[k])
        if count >= 8 and count >= 0.6 * (last - i + 1) and (best is None or count > best[2]):
            best = (i, last, count)
        i = last + 1
    if best is None:
        return items
    header = {"self_ref": "#/texts/references~inferred", "parent": {"$ref": "#/body"}, "children": [], "label": "section_header", "text": "References", "level": 1, "prov": list(items[best[0]].get("prov") or []), "_inferred": True}
    repairs["inferred_references"] = best[2]
    return items[: best[0]] + [header] + items[best[0] :]


_BULLET = re.compile(r"^[■▪●•◆▶‣◼█▉\s]+")


_CAPTION_START = re.compile(r"^(?:fig(?:ure)?|scheme|table|chart|plate|graph)\.?\s*S?\d", re.I)


def _adopt_captions(doc: dict[str, Any], repairs: dict[str, int] | None = None) -> None:
    """A figure's legend the layout model nested under the figure without naming it a
    caption — Frontiers sets the legend under the image and Docling files it as the
    picture's child, which the walk never enters — becomes the picture's caption.

    Which of a figure's text children is its legend is a question of what the text
    resembles: the embedder answers it for every such child in one batch (`figtext`:
    caption, prose, junk), and a child it is sure is a caption is adopted whatever its
    length. It never takes a legend away: a text of six words or more is adopted as
    before, because a child not adopted is text the tree never reaches, and that is
    nothing to lose on a verdict."""
    pending: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for pic in doc.get("pictures") or []:
        if pic.get("captions"):
            continue
        for c in pic.get("children", []):
            child = _resolve(doc, c.get("$ref", "")) if "$ref" in c else None
            if child is not None and (child.get("text") or "").strip() and child.get("label") in ("caption", "text", "paragraph"):
                pending.append((pic, child))
    if not pending:
        return
    o = _oracle()
    asked = [child for _, child in pending if child.get("label") != "caption"]
    verdicts = {id(child): v for child, v in zip(asked, o.nearest_many("figtext", [child["text"] for child in asked]))} if o is not None and asked else {}
    for pic, child in pending:
        text = (child.get("text") or "").strip()
        by_rule = child.get("label") == "caption" or len(text.split()) >= 6
        v = verdicts.get(id(child))
        adopt = by_rule or (v is not None and v.sure and v.name == "caption")
        if adopt and not by_rule and repairs is not None:
            repairs["captions_meaning"] = repairs.get("captions_meaning", 0) + 1
        if adopt:
            pic.setdefault("captions", []).append({"$ref": child["self_ref"]})
    for pic, _ in pending:
        if pic.get("captions") == []:
            del pic["captions"]


def _descendants(node: Node) -> Iterable[Node]:
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def _unspaced_heading(text: str) -> str:
    """"a b s t r a c t" → "Abstract", "■ REFERENCES" → "REFERENCES"; anything else as it came."""
    text = _BULLET.sub("", text)
    u = unspace(text)
    return u.capitalize() if u != text else text


def _body_items(doc: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """The body in reading order, each item once: list groups flattened, inline groups
    joined back into the one paragraph they are."""
    seen: set[str] = set()

    def visit(ref: str) -> Iterable[dict[str, Any]]:
        item = _resolve(doc, ref)
        if item is None:
            return
        self_ref = item.get("self_ref", ref)
        if self_ref in seen:
            return
        seen.add(self_ref)
        children = [c["$ref"] for c in item.get("children", []) if "$ref" in c]
        if self_ref.startswith("#/groups") and item.get("label") == "inline":
            runs = [r for r in (_resolve(doc, c) for c in children) if r is not None and r.get("self_ref", "") not in seen]
            seen.update(r.get("self_ref", "") for r in runs)
            if len(runs) == 1:
                yield runs[0]
            elif runs:
                yield {**runs[0], "text": _join_inline(runs), "children": [], "self_ref": self_ref}
            return
        if self_ref.startswith("#/groups") or self_ref == "#/body":
            for c in children:
                yield from visit(c)
            return
        if not (item.get("text") or "").strip() and children and item.get("label") in _PROSE | _LIST:
            # A list item or footnote whose text Docling put in an inline group beneath it
            # (the `<p>` had formatting): the runs are the item's own text.
            runs = _inline_runs(doc, children)
            if runs is not None:
                seen.update(r.get("self_ref", "") for r in runs)
                yield {**item, "text": _join_inline(runs), "children": []}
                return
        yield item
        # Tables and pictures own their captions; a caption is filed under them below,
        # so their children are not walked as body items — but a table's footnotes are
        # its children too, and they are prose the paper means to keep. A picture's
        # children are its labels ("F.", "0", "H"): never prose.
        if item.get("label") in _TABLE:
            for c in children:
                child = _resolve(doc, c)
                if child is not None and child.get("label") in ("footnote", "text", "paragraph", "list_item") and child.get("self_ref", c) not in seen and len(re.findall(r"[A-Za-z]{2,}", child.get("text") or "")) >= 3:
                    seen.add(child.get("self_ref", c))
                    yield child
            return
        if item.get("label") in _PICTURE:
            return
        for c in children:
            yield from visit(c)

    yield from visit("#/body")

_NUMBERED = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+\S")


def numbering_depth(heading: str) -> int | None:
    """"2.1.3 Cell culture" → 3; "Results" → None."""
    m = _NUMBERED.match(heading)
    return m.group(1).count(".") + 1 if m else None


def undouble(text: str) -> str:
    """"3.4. Simulation Results 3.4. Simulation Results" → "3.4. Simulation Results"."""
    t = text.strip()
    if len(t) % 2 == 1:
        half = t[: len(t) // 2]
        if half and t == f"{half} {half}":
            return half
    return t


_WORDISH = re.compile(r"[^0-9a-z]+")


def unrepeat(text: str) -> str:
    """A block that carries its own text twice: a copy cut short, then the paragraph from its
    first word again — the layout model kept both its own reading of the cell and the page's
    text layer ("SEM imaging was performed … resulted in higher packing SEM imaging was
    performed … of collagen fibers."). Where the text begins again with its first eight words
    and everything before that point is said again after it, the first copy goes. Found by
    comparing a PDF's reading with its XML's (pairs.py): the words were there twice, the XML
    held them once."""
    ws = text.split()
    if len(ws) < 30:
        return text
    norm = [_WORDISH.sub("", w.lower()) for w in ws]
    head = norm[:8]
    for p in range(12, len(ws) - 11):
        if norm[p : p + 8] != head:
            continue
        first, rest = norm[:p], norm[p:]
        if len(rest) >= p and rest[:p] == first:
            return " ".join(ws[p:])  # a copy cut short, then the whole paragraph
        if len(rest) < p and first[: len(rest)] == rest:
            return " ".join(ws[:p])  # the whole paragraph, then its beginning again
    return text


#: The lanes a numbered heading never takes by meaning alone: no numbered heading in the
#: corpora is an abstract, a reference list or a back-matter statement (the vocabulary's own
#: word, "7. References" in a preprint, still counts).
_NOT_NUMBERED = frozenset({"abstract", "references", "back"})


def top_number(heading: str) -> str | None:
    """"3.2 Effect of…" → "3"; unnumbered → None."""
    m = _NUMBERED.match(heading)
    return m.group(1).split(".")[0] if m else None


#: A core section's own name: its level is not in doubt, and its look is the look of the
#: paper's top level (`typography.depth_by_type`).
TOP_NAMES = frozenset({
    "introduction", "background", "methods", "method", "materials and methods", "material and methods", "methods and materials",
    "materials and experimental methods", "experimental", "experimental section", "experimental procedures", "results", "result",
    "results and discussion", "discussion", "conclusion", "conclusions", "concluding remarks", "discussion and conclusions",
})


def infer_level(heading: str, docling_level: int, open_top: bool, stated: bool = False, typo_level: int | None = None, list_number: bool = False, open_top_role: str | None = None) -> int:
    """A header's depth when the layout model gives every header the same level.

    Numbering wins when present. A heading that names a lane (Methods, Results,
    Discussion…) is top-level whatever it looked like on the page. Anything else
    beneath an open top-level section is that section's child, one level down,
    unless the layout model already placed it deeper — but only where the level is
    the layout model's guess. With `stated`, the file says how deep its sections lie
    (a JATS file: <sec> inside <sec>) and its word stands: a review's topical
    sections are the paper's top level, not children of whichever section stood
    open. Measured on 513 XML papers: 453 headings in 145 of them had been nested
    under "Introduction" or "Conclusions" against the file, and whole review bodies
    read as `introduction`.
    """
    if list_number and typo_level is not None and not stated:
        return typo_level  # a number the paper's top level does not use, set below it: a list's number, not a section's
    depth = numbering_depth(heading)
    if depth is not None:
        return depth
    if typo_level is not None and not stated and normalise_heading(heading) not in TOP_NAMES and role_of(heading, meaning=False) not in ("references", "abstract") and not (typo_level > 1 and open_top_role == "references"):
        return typo_level  # the page's type: set like the paper's core sections, or less prominently (`typography.depth_by_type`; a back statement only inside the body; never a subsection of the reference list)
    if role_of(heading, meaning=False) != "other" or top_level_lane(heading, promote=True) is not None:
        return 1  # the vocabulary's word, or the catalogue's exact spelling (two words or more) of a top-level section
    if docling_level <= 1 and role_of(heading) != "other":
        return 1  # a lane found by meaning keeps the depth the page gave it: top when the page set it top ("Methods Coral core collection"), never promoted from deeper
    if open_top and not stated:
        return max(docling_level, 2)
    return max(docling_level, 1)


_MERGED = re.compile(r"^(?P<head>(?:\d+\.\s+)?[A-Za-z][^.]{2,60}?)\s+(?P<num>\d+)\.(?P<sub>\d+)\.?\s+(?P<rest>\S.*)$")


_FUSED = re.compile(r"^(?P<lane>Introduction|Background|Methods|Materials and [Mm]ethods|Results|Results and [Dd]iscussion|Discussion|Conclusions?)\s+(?P<rest>[A-Z][a-z]\S*(?:\s+\S+)+)$")
_NOT_A_SECOND_HEADING = {"And", "Of", "For", "In", "To", "On", "With", "From", "Section", "Summary", "Overview"}


def split_fused_heading(text: str) -> tuple[str, str] | None:
    """"Results and discussion Contrasting glacier mass balance responses during 2021–22" → the
    lane's heading and the subheading under it, which the layout model read as one line (an
    unnumbered sibling of `rescue_merged_heading`). Only a lane's bare name, then a phrase that
    starts like a heading of its own: "Results of the logistic regression", "Conclusion: the
    2024 report" and "INTRODUCTION TO THE CONCEPT" stay whole. Found by comparing a PDF's
    reading with its XML's: three quarters of that paper's body had stayed under its methods."""
    m = _FUSED.match(text.strip())
    if not m or text.isupper():
        return None
    rest = m.group("rest")
    if rest.split()[0] in _NOT_A_SECOND_HEADING or len(rest.split()) < 2 or len(text) > 160:
        return None
    return m.group("lane"), rest


def rescue_merged_heading(item: dict[str, Any]) -> list[dict[str, Any]] | None:
    """"Experimental Results 3.1. Quantification of …" → two headers, "3. Experimental Results" and "3.1. Quantification of …".

    A failure mode of the layout model: a top-level heading and the subheading
    under it read as one line, labelled a list item or a paragraph. Only a head
    that names a lane is rescued; anything else stays what Docling said it was.
    """
    if item.get("label") not in ("list_item", "text", "paragraph"):
        return None
    text = (item.get("text") or "").strip()
    if len(text) > 240:
        return None
    m = _MERGED.match(text)
    if not m:
        return None
    head = re.sub(r"^\d+\.\s+", "", m.group("head")).strip()
    if role_of(head) == "other":
        return None
    num, sub, rest = m.group("num"), m.group("sub"), m.group("rest").strip()
    # the subheading may itself be echoed: cut at a second "N.M." if one appears
    echo = re.search(rf"\s+{num}\.{sub}\.?\s", rest)
    if echo:
        rest = rest[: echo.start()].strip()
    base = {k: v for k, v in item.items() if k not in ("text", "label", "children")}
    top = {**base, "label": "section_header", "level": 1, "text": f"{num}. {head}", "self_ref": f"{item.get('self_ref', '')}~top"}
    below = {**base, "label": "section_header", "level": 2, "text": f"{num}.{sub}. {rest}", "self_ref": f"{item.get('self_ref', '')}~sub"}
    if "_typo_level" in base:
        top["_typo_level"], below["_typo_level"] = 1, 2  # the row's look is the first heading's
    return [top, below]


def _same_heading(a: str, b: str) -> bool:
    return re.sub(r"\W+", " ", a).strip().lower() == re.sub(r"\W+", " ", b).strip().lower()


# What sits above a paper's title on its first page, and never is the title.
_GENERIC_LABELS = {"original research", "original article", "research article", "article", "review", "reviews", "review article", "abstract", "abstracts", "introduction", "letter", "letters", "communication", "communications", "full paper", "full length article", "short communication", "editorial", "case report", "brief report", "untitled", "research", "research paper", "report", "paper", "papers", "original paper", "major review", "topical review", "hhs public access", "author manuscript", "supporting information", "supporting information for", "regular article", "open access", "perspective", "commentary", "mini review", "minireview", "technical note", "rapid communication", "note", "notes", "feature article", "critical review", "systematic review", "meta-analysis", "clinical study", "clinical trial", "concise review"}
_FURNITURE = re.compile(r"you may also like|related content|recent citations|this content was downloaded|sciencedirect|journal homepage|journal home page|to cite this article|view the article online|author manuscript|available in pmc|^\s*doi\b|^\s*https?://|\bwww\.|received:|accepted:|revised:|©|\(c\) \d{4}|copyright|all rights reserved|contents lists|published (in|by|online)|^\s*issn|open access|creative commons|licensee|licen[cs]ed under|\bcc[- ]by\b|cite this|^\s*citation:|downloaded from|^\s*e-?mail|correspond(?:ing author|ence)|manuscript received|article history|keywords?:|^\s*key ?words", re.I)
_FUNCTION_WORDS = {"of", "for", "in", "on", "with", "by", "the", "a", "an", "to", "from", "via", "using", "between", "into", "during", "through", "its", "their", "at", "as", "under", "toward", "towards", "without", "within", "versus", "vs", "over", "after", "before", "against"}
_DATE_LINE = re.compile(r"\b(received|accepted|published|revised|first published|epub)\b|\bavailable online\b.*\b(?:19|20)\d{2}\b", re.I)  # "available online" is a date only with a year after it: IOP's "Supplementary material … is available online" is a notice
_AFFILIATION = re.compile(r"\b(universit|department|dept\.|institut|hospital|school of|laborator|centre|center|college|faculty|clinics?\b|academy|ministry|foundation|company|ltd|inc\b|gmbh|corporation|research (group|unit)|division of|program in|graduate|medical|engineering,)", re.I)
_EMAIL = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}"
_CORRESPONDENCE = re.compile(rf"correspond(?:ing author|ence)|e-?mail|{_EMAIL}|\btel\b|\bfax\b|to whom|\*\s*author|orcid", re.I)
_FUNDING = re.compile(r"^\s*(?:the authors? (?:have|has|declares?|reports?) no|conflicts? of interest|competing interests?|disclosures?\b|funding\b|acknowledg\w+\b|this (?:work|study|research) was (?:supported|funded))", re.I)
_INSTITUTION = re.compile(r"\b(?:universit\w*|institut\w*|department|school|faculty|hospital|college|laborator\w*|cent(?:er|re)|academy)\b", re.I)
_VERBS = re.compile(r"\b(?:was|were|is|are|has|have|had|be|been|which|that|we|our)\b", re.I)


def _affiliation_like(text: str) -> bool:
    """An institution's name, not one verb of prose, and most words capitalised: "1, Foot & Ankle
    Surgery, Department of Orthopaedics, Shanghai Sixth People's Hospital …, China. 2 Department
    of …" — an affiliation block, whatever its length, and its numbering is not a citation.
    "Associations between wildfire smoke and cardiorespiratory emergency department visits varied
    by exposure product" names a department and is a sentence."""
    return bool(_INSTITUTION.search(text)) and not _VERBS.search(text) and _caps_dense(text)


def _caps_dense(text: str) -> bool:
    """Most words capitalised, as an address is: "Department of Orthopaedic Surgery, Daejeon Eulji
    Medical Center, Eulji University School of Medicine, Daejeon, Korea"."""
    words = [w for w in re.findall(r"[A-Za-z][\w'\u2019-]*", text) if len(w) > 1]
    return bool(words) and sum(1 for w in words if w[0].isupper()) >= 0.45 * len(words)


_SELF_CITATION = re.compile(r"(?:\bdoi:\s*|doi\.org/)10\.\d{4,}", re.I)
_CITATION_SHAPE = re.compile(r"^(?:(?:[A-Z][\w'’-]+ )+[A-Z]{1,3},? (?:and )?){2,}\(\d{4}\)|^(?:[A-Z][\w'’-]+, (?:[A-Z]\.\s?)+,? (?:& |and )?){2,}(?:et al\.,? )?\(\d{4}\)")


def _citation_line(text: str) -> bool:
    """The paper's own citation as the publisher prints it: "Sablan, O., Ford, B., … et al. (2026).
    Wildfire smoke … GeoHealth, 10, e2025GH001492. https://doi.org/10.1029/2025GH001492" — a
    DOI with a year in brackets or an "et al.", or the reference's own shape, two or more
    authors then the year in brackets ("Stamov S, Chobanov T, … and Reich D (2026) Paleogenomic
    evidence …") when the DOI stands on the next line."""
    return (bool(_SELF_CITATION.search(text)) and bool(re.search(r"\(\d{4}\)|\bet al\b", text))) or bool(_CITATION_SHAPE.match(text.strip()))


def _prose_like(text: str) -> bool:
    """A paragraph the introduction could open with: not furniture, not "Abstract: …", not an
    affiliation block, not an author line with its degrees."""
    return not _FURNITURE.search(text) and not _LICENCE.search(text) and not _citation_line(text) and not _ABSTRACT_LEAD.match(text) and not _affiliation_like(text) and len(_DEGREES.findall(text)) < 2
_KEYWORDS = re.compile(r"^\s*(key ?words?|index terms)\b|^\s*(?:categories|subject areas)\s*[:\u2014]", re.I)
#: the publisher's own lines, by their opening: an editor, a licensee. A date line is
#: `_dates_only`'s, which asks that the whole line be dates — this one only reads its opening, and
#: a line that opens "Received:" may still carry a sentence of the body away with it
_EDITOR_LINE = re.compile(r"^\s*(?:academic|handling|guest|associate|section)?\s*editors?\b|^\s*(?:edited|reviewed)\s*(?:by\b|:)|^\s*licensee\b", re.I)


_DATE_WORDS = {"received", "revised", "accepted", "published", "online", "first", "epub", "available", "in", "revised", "form",
               "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
               "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}


def _dates_only(text: str) -> bool:
    """Whether a line is nothing but dates and the words a journal prints beside them. A date line
    that carried a sentence away with it is not one: the reader joins a lowercase fragment onto the
    block before it, and that block may be this one."""
    words = re.findall(r"[A-Za-z]+", text)
    return bool(words) and all(w.lower() in _DATE_WORDS for w in words)


def _late_front(text: str) -> bool:
    """Whether a line is unmistakably the publisher's, wherever the layout model read it: a date
    line, an editor's name, the licence, the citation line, the imprint, the keywords, an author
    list, an institution's address, a correspondence line. MDPI sets these in the left column of
    its first page and the model
    reads them between the introduction's paragraphs, where they became chunks of the body that no
    paper holds (79 of them over 57 papers). None of these is a sentence of a paper, which is why
    the shape may decide after the body has begun — the kinds `_front_kind` reads by meaning may
    not. Three of the shapes run past the short-line cap and carry their own: a licence sentence
    and a citation line are the publisher's at any length, and neither ever cites a reference,
    which is what separates them from a paper whose subject is licensing."""
    t = text.strip()
    if re.search(r"[.!?]\s+[a-z]", t):
        return False  # a sentence carries on inside it: the block holds the body's words too
    words = len(t.split())
    if _CITE_LINE.match(t) or _citation_line(t) or _LICENCE.search(t):
        return words <= 120 and not _CITES.search(t)
    if _COPYRIGHT_LINE.match(t):
        return words <= 40
    if words > 25:
        return False  # any other shape this long is holding a paragraph of the paper as well
    if _BARE_DATE.match(t) or _dates_only(t):
        return True
    if _KEYWORDS.match(t):
        return True
    if _EDITOR_LINE.match(t) and words <= 14:
        return True
    if _looks_like_authors(t) or _name_list(t) or _affiliation_like(t):
        return True  # a name list or an institution's address, which `_front_kind` then names
    return bool(_CORRESPONDENCE.search(t) and words <= 14)


#: the imprint, which opens a line and never a sentence of a paper: "© The Author(s) 2026.
#: Published by Oxford University Press on behalf of …", "Published by Oxford University Press."
_COPYRIGHT_LINE = re.compile(r"^\s*(?:\u00a9|\(c\)\s*\d{4}|copyright\b|published (?:by|in|online)\b)", re.I)
_BARE_DATE = re.compile(r"^\s*(?:\d{1,2}\s*[A-Z][a-z]+,?\s+(?:19|20)\d{2}|[A-Z][a-z]+\s+\d{1,2},?\s+(?:19|20)\d{2}|(?:19|20)\d{2})\s*$")
_CITE_LINE = re.compile(r"^\s*(?:to cite this article|citation|cite this|to link to this article)\b", re.I)  # a publisher's citation line runs past forty words
_ABSTRACT_LEAD = re.compile(r"^\s*(abstract|summary)\b[\s:.—–-]*", re.I)


def _title_like(text: str) -> bool:
    words = text.split()
    letters = sum(ch.isalpha() for ch in text)
    return 4 <= len(words) <= 45 and letters >= 0.6 * len(text.replace(" ", "")) and not _FURNITURE.search(text) and text.lower().strip(" .:") not in _GENERIC_LABELS and not _looks_like_authors(text)


def _looks_like_authors(text: str) -> bool:
    """"Anowarul Islam a , Thomas Mbimba a , Mousa Younesi" — names, markers, no function words."""
    tokens = [t.strip(",;·*†‡") for t in text.split()]
    words = [t for t in tokens if t]
    if not words or _INSTITUTION.search(text):
        return False  # "d Le Quy Don Specialized High School, Dong Hai, Khanh Hoa 650000, Vietnam": an affiliation, whatever its markers
    marks = [t for t in words if re.fullmatch(r"[a-z](?:,[a-z])*|\d{1,2}(?:,\d{1,2})*", t)]
    if marks.count("a") == len(marks) == 1:
        marks = []  # a lone "a" with no other marker is the article ("Designing a Better, Stronger, Cheaper Scaffold")
    names = [t for t in words if t not in set(marks)]  # "Do Dinh Trung, a Pham Van Duong, b …": the markers are not the names
    function = sum(1 for t in names if t.lower() in _FUNCTION_WORDS)
    caps = sum(1 for t in names if t[0].isupper())
    markers = len(re.findall(r"(?:^|[\s,])[a-z](?:,[a-z])*(?=[\s,]|$)|(?<!\d)\d{1,2}(?:,\d{1,2})*(?=[\s,*]|$)|[·*†‡]", text))
    # markers — superscript digits and letters (one or two: a postal code is not a marker), "·", asterisks — are what tells a list of names from a
    # title with commas in it ("Extraction, Gelation, and Applications")
    return function == 0 and bool(names) and caps >= 0.5 * len(names) and (markers >= 2 or (markers >= 1 and text.count(",") >= 2) or "·" in text)


_ABSTRACT_BLOCK = re.compile(r"^\s*a\s*b\s*s\s*t\s*r\s*a\s*c\s*t\s*[:.\-\u2014]?\s*", re.I)
_WRAPPER_HEADINGS = re.compile(r"^(?:associated data|supplementary materials?|supplementary information|supporting information|declarations?|peer review|electronic supplementary material|additional information|notes|footnotes)\s*$", re.I)
_NAME_PIECE = re.compile(r"^(?:(?:[A-Z][\w'\u2019\-]*\.?|de|da|del|della|di|dos|das|du|la|le|van|von|der|den|ter|ten|bin|ibn|al|el)\s?){2,5}$")  # "Chamini Kanatiwela de Silva": a particle is part of the name


def _name_list(text: str) -> bool:
    """"Anowarul Islam, Thomas Mbimba, Mousa Younesi and Ozan Akkus" — the authors of a JATS
    file, names with no markers: two or more pieces of two to four capitalised words, no
    function words, no digits."""
    t = text.strip().rstrip(".")
    if len(t.split()) > 60 or re.search(r"\d|@|\(|:", t):
        return False
    pieces = [p.strip() for p in re.split(r",|;|\band\b|&", t) if p.strip()]
    if len(pieces) < 2:
        return False
    return all(_NAME_PIECE.match(p) and not any(w.lower() in _FUNCTION_WORDS for w in p.split()) for p in pieces)


_FRONT_LABEL = re.compile(r"^\W*(?:edited by|reviewed by|correspondence|citation|copyright|received|accepted|published|open access|keywords?|key words|abbreviations|highlights|research highlights|article info(?:rmation)?|graphical abstract|abstract|summary|significance(?: statement)?|author summary|impact statement|research in context|key points|key findings|what is already known|what this study adds|how this study might affect|editor'?s? summary|in brief|at a glance|lay summary|lay abstract|plain language summary|full-length text|research article|original article|original research|review article|short communication|brief report|case report|letter to the editor|perspective|commentary|editorial|mini-?review)\b", re.I)


#: a page's folio: "7 of 9", "12/48", "Page 3" — the same shape `typography._FOLIO` reads in the rows
_FOLIO = re.compile(r"^\s*(?:page\s*)?\d{1,4}\s*(?:of|/|\||\u2013|\u2014|-)\s*\d{1,4}\s*$|^\s*page\s+\d{1,4}\s*$", re.I)


def _journal_name(text: str, journal: str | None) -> bool:
    """Whether a line is the journal's own name, which owns no prose: the record's title, or the
    same words abbreviated in order, either way round — a record holds "Chem Sci" where the page
    prints "Chemical Science", and "J Xenobiot" where it prints "Journal of Xenobiotics"."""
    if not journal or len(text.split()) > 8:
        return False
    a, b = _norm_letters(text), _norm_letters(journal)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    skip = {"of", "the", "and", "for", "in", "on", "de", "la"}
    ws = [w for w in re.findall(r"[a-z]+", text.lower()) if w not in skip]
    js = [w for w in re.findall(r"[a-z]+", journal.lower()) if w not in skip]

    def abbreviates(short: list[str], long_: list[str]) -> bool:
        return len(short) >= 2 and len(short) <= len(long_) and all(long_[k].startswith(w) for k, w in enumerate(short))

    return abbreviates(ws, js) or abbreviates(js, ws)  # either side may be the abbreviated one


def _repeated_short(doc: dict[str, Any]) -> set[str]:
    """The short lines the pages repeat, whatever their height — a journal's banner — by their
    letters alone, so "Chem. Sci." and "Chem Sci" are one. The record's journal is better evidence;
    this is what stands in when a library has none."""
    pages_of: dict[str, set[int]] = {}
    for t in doc.get("texts") or []:
        text = (t.get("text") or "").strip()
        prov = _first_prov(t)
        if not text or len(text.split()) > 3 or not prov or "page_no" not in prov:
            continue
        pages_of.setdefault(_norm_letters(text), set()).add(int(prov["page_no"]))
    return {k for k, pages in pages_of.items() if k and len(pages) >= 3}


def _refuse_front_furniture(items: list[dict[str, Any]], title_ref: str | None, doc: dict[str, Any], journal: str | None, has_abstract_heading: bool, repairs: dict[str, int], notes: list[dict[str, Any]]) -> None:
    """A line of the paper's furniture the layout model read as a heading — the journal's banner, an
    affiliation or correspondence line, a date line, a page's folio — is no section, and the prose
    under it belongs to the body, not to it. This is the one place where a heading with prose under
    it may still be refused, so only the *shape* of the line may do it: `_front_kind` with the
    vocabulary alone, never the embedder, whose wrong verdict would file a section's prose under the
    section before. Marked here (`_furniture_head`), filed as a notice by the main loop; a folio at
    any page or place, everything else only in the front region, and never a heading another rule
    already claims."""
    # where the body begins, by the vocabulary and the author's numbering alone: the embedder's word
    # must not decide it, or a banner it happens to lane ("Chemical Science") closes the front region
    def _body_start(it: dict[str, Any]) -> bool:
        text = (it.get("text") or "").strip()
        return it.get("label") == "section_header" and bool(text) and not it.get("_furniture_head") and (role_of(text, meaning=False) != "other" or numbering_depth(text) == 1)

    body_at = next((i for i, it in enumerate(items) if _body_start(it)), len(items))
    repeated = _repeated_short(doc)
    title = next((_norm_letters((it.get("text") or "")) for it in items if it.get("self_ref") == title_ref), "")
    for i, it in enumerate(items):
        text = (it.get("text") or "").strip()
        if it.get("label") != "section_header" or not text or it.get("self_ref") == title_ref or it.get("_built") or it.get("_runin"):
            continue
        why = "a page's folio" if _FOLIO.match(text) else None
        if why is None and title and len(title) >= 24 and len(text.split()) >= 4:
            other = _norm_letters(text)
            if other == title or (len(other) >= 24 and (title.startswith(other) or other.startswith(title))):
                why = "the paper's own title"  # its banner again over the body, or its running head
        if why is None:
            clean = normalise_heading(text)
            claimed = (role_of(text, meaning=False) != "other" or numbering_depth(text) is not None
                       or _FRONT_LABEL.match(text) or clean in _BOX_HEADINGS or _ABSTRACT_PART.match(text) or _MESSAGE_BOX.match(clean))
            kind = None if claimed else _front_kind(text, has_abstract_heading, meaning=False)
            if kind == "dates":
                why = kind  # "Received: 19 May 2025 / Accepted: 7 January 2026": a date line heads no
                # section at any page — of the front-matter shapes read as a heading past the second
                # page, it is the only one the witness never finds among the paper's own headings
                # (9 of 9, against an affiliation's 10 of 31 and an author line's 4 of 6)
            elif _page_of(it) not in (None, 1, 2) or claimed:
                continue  # the first pages, where a paper's furniture is printed; and never a
                # heading the vocabulary, the author's numbering, a label or a box already claims
            elif kind in ("authors", "affiliations", "correspondence"):
                why = kind  # a line of this shape heads no section, wherever the layout read it: RSC
                # prints its affiliations after the introduction's first lines
            elif i < body_at and kind in ("funding", "notice"):
                why = kind  # a word that could head a section of its own later: the front region only
            elif i < body_at and (_journal_name(text, journal) or (len(text.split()) <= 3 and _norm_letters(text) in repeated)):
                why = "the journal's name"
        if why:
            it["_furniture_head"] = why
            repairs["furniture_headings"] = repairs.get("furniture_headings", 0) + 1
            notes.append({"kind": "heading-refused", "node_id": None, "page": _page_of(it), "message": f"{text[:80]!r} reads as {why}, not a heading: the prose under it is the body's"})


def _body_heading(item: dict[str, Any]) -> bool:
    """A printed heading of the paper's own: not the title, not one of the front matter's labels
    or boxes, not a line of furniture, two words or more."""
    if item.get("label") != "section_header" or item.get("_furniture_head"):
        return False
    text = (item.get("text") or "").strip()
    return len(text.split()) >= 2 and not _FRONT_LABEL.match(text) and not _FURNITURE.search(text)


def _known_heading(item: dict[str, Any]) -> bool:
    """A header the vocabulary knows, or a numbered one: where the paper proper begins."""
    if item.get("label") != "section_header" or item.get("_furniture_head"):
        return False
    text = (item.get("text") or "").strip()
    return bool(text) and (role_of(text) != "other" or numbering_depth(text) is not None)


_ABSTRACT_PART = re.compile(r"^(?:background|objectives?|aims?|purpose|methods?|design|setting|participants?|patients?|interventions?|measurements?|main outcomes?(?: and measures?)?|results?|findings|conclusions?|interpretation|funding|importance|exposures?|limitations?|significance)\s*[:.]?\s*$", re.I)


_DEGREES = re.compile(r"\b(?:MD|PhD|Ph\.D|DDS|DVM|MSc|BSc|MPH|RN|FRCS|FACS|Dr)\b\.?")
_LICENCE = re.compile(
    r"author and source are credited|open-access article|distributed under the terms|creative commons"
    r"|creativecommons\.org|permits unrestricted|non-?commercial use|provided the original|all rights reserved"
    r"|early access|accepted manuscript|licen[cs]ed under|\bcc[- ]by\b"
    # the sentence as the layout model cuts it, which is how it reaches the body: a column break
    # leaves "International License, which permits any non-commercial use, sharing, …" on its own
    r"|international licen[cs]e|licen[cs]e,? which permits|sharing,? adaptation,? distribution and reproduction"
    r"|full list of author information",
    re.I,
)


def _never_a_title(text: str) -> bool:
    """What a first page's largest lines can be that a title is not: an author line
    ("Yonghan Cha, MD, PhD"), a licence sentence, a sentence of prose that starts
    lowercase or ends with a full stop."""
    t = text.strip()
    if not t:
        return True
    first = t.split()[0]
    if first.islower() and first.isalpha() and len(first) >= 2:
        return True  # a sentence's middle ("the original author…"); "circCACNA1D" and "p53" open titles
    if _LICENCE.search(t) or (_DEGREES.search(t) and _name_list(re.sub(_DEGREES.pattern, "", t))) or _looks_like_authors(t) or _name_list(t):
        return True
    words = t.split()
    if _DATE_LINE.search(t) and re.search(r"\b(?:19|20)\d{2}\b", t) and len(words) <= 30:
        return True  # "Received 22nd August 2026 Accepted 26th August 2026": a dates line
    return t.endswith(".") and len(words) >= 12 and not t.endswith(("et al.", "sp.", "spp."))


#: Ranking a title candidate that names the journal last ("A Nature Portfolio journal", which
#: Nature sets above the abstract and which took the title on 10.1038/s43247-025-02822-z) was tried
#: on 2026-09-20 and refused. Alone it changed nothing — the real title sits below the abstract,
#: past where the scan stops — and reading on to find it cost a heading elsewhere (the `prose > 3`
#: break below carries that measurement).
#: `_journal_name` is also too loose to ask here: it calls a figure panel's "A" the name of
#: "Proc Natl Acad Sci U S A". The wrong title survives; the prose under it does not move.
def _pick_title(items: list[dict[str, Any]], hint: str | None) -> str | None:
    """The self_ref of the item that is the paper's title.

    On a first page the title sits under the publisher's label ("PAPER", "Full length
    article", "RESEARCH ARTICLE") or the journal's name, and above the authors; the layout
    model may call it a title, a header, or plain text. The first title-like item wins —
    a Docling `title` over a header over text — and the PDF's own Title metadata, when it
    is one, decides between candidates or stands in when there are none.
    """
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    prose = 0
    past_heading = 0  # a structured abstract's labels (BACKGROUND, METHODS, RESULTS) can stand above the title on a first page
    for i, item in enumerate(items):
        label = item.get("label")
        text = re.sub(r"\s+", " ", (item.get("text") or "")).strip()
        page = _page_of(item)
        if _known_heading(item):
            if page not in (None, 1) or not (_ABSTRACT_PART.match(text) or text.lower().strip(" .:") in _GENERIC_LABELS or len(text.split()) <= 2 or (numbering_depth(text) is None and _means_label(text))):
                break  # the paper proper has begun; a structured abstract's labels and an article-type label ("Perspective") have not begun it
            past_heading = 3
            continue
        if page is not None and page > 2:
            break
        if label in ("text", "paragraph") and len(text.split()) >= 40:
            prose += 1
            if prose > 3:
                break  # three paragraphs in, the first page's furniture is behind us. Reading on
                # while the only candidate is the journal's line was tried (2026-09-20) and refused:
                # the scan then reaches the body's own first heading, which outranks a `text`
                # candidate near the top, and PNAS lost "Can White Noise Cause…" (faithful −0.090)
        if _never_a_title(text):
            continue
        if label == "title" and len(text.split()) >= 2 and not _FURNITURE.search(text) and text.lower().strip(" .:") not in _GENERIC_LABELS:
            candidates.append((0 + past_heading, i, item))  # the layout model's own word for it: doubted only when generic
        elif label in ("section_header", "text", "paragraph") and _title_like(text):
            candidates.append(({"section_header": 1}.get(label, 2) + past_heading, i, item))
        if len(candidates) >= 6:
            break
    if hint:
        h = _norm_letters(hint)[:30]
        for _, _, item in sorted(candidates, key=lambda c: (c[0], c[1])):
            if h and _norm_letters(item.get("text") or "")[:30] == h:
                return item.get("self_ref")
    if candidates:
        rank, _, item = min(candidates, key=lambda c: (c[0], c[1]))
        return item.get("self_ref")
    return None


def _norm_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _norm_letters(text: str) -> str:
    """Letters and digits only: "lower-limb" and "lowerlimb" are the same title."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


_FRONT_KINDS = {"authors", "affiliations", "dates", "correspondence", "keywords", "funding", "notice"}


#: A front-matter box whose own content is a few words or bullets: prose after it is the body.
_BOX_HEADINGS = frozenset({"keywords", "key words", "keyword", "highlights", "research highlights", "article info", "article information", "graphical abstract", "lay abstract", "lay summary", "plain language summary", "author summary", "significance", "significance statement", "impact statement", "key points"})


def _box_prose(text: str) -> bool:
    """Body prose, not a keyword list or a highlight: forty words or more in sentences."""
    words = text.split()
    return len(words) >= 40 and text.rstrip().endswith((".", ")", "]")) and text.count(". ") >= 1 and _prose_like(text) and not _REF_ENTRY.match(text)


#: A key-message box, set on the first page in or before the introduction: BMJ's three questions,
#: BMJ Open's strengths and limitations, Diabetes Care's article highlights, AME's highlight box.
_MESSAGE_BOX = re.compile(
    r"^(?:what (?:is|was) (?:already )?known(?: on this (?:topic|subject))?(?: and what is new)?\??"
    r"|what (?:this|the) (?:study|paper|article|report) adds\??"
    r"|how (?:this|the) study (?:might|may|could|will) affect research,? practice,? (?:and|or) policy\??"
    r"|strengths and limitations of (?:this|the) study|key messages?|highlight box"
    r"|why did we undertake this study\??|what (?:is|are) the specific questions? we wanted to answer\??"
    r"|what did we find\??|what are the implications of (?:our|these) findings\??)$"
)


def _message_ends(box: Node, text: str, label: str, root: Node) -> bool:
    """Whether this prose is the body's, read after a key-message box: the box's own blocks are
    bullets that cite nothing, and this prose cites (BMJ's introduction continued after "How this
    study might affect research, practice or policy"). Only on the first pages' box: once a
    methods, results or discussion section is open, a heading that asks a question is the body's."""
    kids = [c for c in box.children if c.type in ("paragraph", "list_item")]
    if not kids or label not in _PROSE or len(text.split()) < 40 or not _CITES.search(text):
        return False
    if any(_CITES.search(c.text or "") for c in kids):
        return False
    return not any(c is not box and c.type == "section" and c.role in ("methods", "results", "results-discussion", "discussion") for c in root.children)


def _back_ends(section: Node, text: str, label: str, root: Node) -> bool:
    """Whether this prose is the body's, read under a back-matter heading printed before the body:
    Scientific Reports sets an "Abbreviations" list on the first page, and the introduction — whose
    heading the paper never prints — follows it. The list's entries are a few words each and cite
    nothing; prose that runs long and cites is not one of them. Only while no methods, results or
    discussion section is open, so a real Abbreviations section at the end is untouched."""
    kids = [c for c in section.children if c.type in ("paragraph", "list_item", "text")]
    if not kids or label not in _PROSE or len(text.split()) < 40 or not _CITES.search(text):
        return False
    if (section.page or 9) > 3 or not all(len((c.text or "").split()) <= 10 for c in kids):
        return False
    return not any(c is not section and c.type == "section" and c.role in ("methods", "results", "results-discussion", "discussion") for c in root.children)


#: what a structured abstract calls its parts, printed as headings: Diabetes Care's "OBJECTIVE",
#: "RESEARCH DESIGN AND METHODS", "RESULTS", "CONCLUSIONS"; NEJM's and the Lancet's own words
_ABSTRACT_PART_WIDE = re.compile(
    r"^(?:background|objectives?(?: and hypothesis)?|aims?|purpose|rationale|context|importance"
    r"|methods?|research design and methods|(?:patients?|subjects?|participants?|materials?) and methods"
    r"|design(?: and setting)?|setting(?: and participants)?|participants?|patients?|interventions?"
    r"|exposures?|measurements?|outcome measures|main outcome measures|main outcomes?(?: and measures?)?"
    r"|data sources|study selection|data extraction(?: and synthesis)?|evidence (?:acquisition|synthesis|review)"
    r"|results?|main results|key results|findings|conclusions?(?: and relevance)?|interpretation"
    r"|significance|limitations|funding|(?:clinical )?trial registration|registration)\s*[:.]?\s*$",
    re.I,
)


def _body_prose_follows(items: list[dict[str, Any]], index: int, within: int = 4) -> bool:
    """Whether the heading at `index` opens on the body's prose: its first paragraph cites, or runs
    past what a structured abstract's part holds. A part is short and cites nothing. The first
    paragraph only — what follows may already be the body, read after the abstract's last part."""
    for it in items[index + 1 : index + 1 + within]:
        if it.get("label") in _HEADER:
            return False
        text = (it.get("text") or "").strip()
        if it.get("label") not in _PROSE | _LIST or not text:
            continue
        return _CITES.search(text) is not None or len(text.split()) > 150
    return False


def _mark_abstract_parts(items: list[dict[str, Any]], repairs: dict[str, int]) -> None:
    """A structured abstract printed as sections reads like the body by its names, and files
    whole sections of a paper under the wrong lane — Diabetes Care's introduction landed under
    "CONCLUSIONS". Three or more of a structured abstract's part names in a row on the first
    pages, each over short prose that cites nothing and before any section of the body, are the
    abstract's parts: marked here, laned `abstract` under the abstract itself when the tree is
    built. Three, because a body whose first three sections are named for an abstract's parts and
    hold a few uncited words each is not a paper."""
    run: list[int] = []
    for i, it in enumerate(items):
        if (_page_of(it) or 1) > 3:
            break
        if it.get("label") not in _HEADER or it.get("label") == "title":
            continue
        text = (it.get("text") or "").strip()
        if text and _ABSTRACT_PART_WIDE.match(normalise_heading(text)) and not _body_prose_follows(items, i):
            run.append(i)
            continue
        if len(run) >= 3:
            break
        run = []
    if len(run) >= 3:
        for i in run:
            items[i]["_abstract_part"] = True
        repairs["abstract_parts"] = repairs.get("abstract_parts", 0) + len(run)


_CITES = re.compile(
    r"\[\d{1,3}[\]\u2013,-]|\(\d{4}[a-z]?\)|et al\.|(?<=[A-Za-z)\]])\.\s?\d{1,3}(?:\s?[-\u2013,]\s?\d{1,3})*\s+[A-Z][a-z]|[.,;]\^\d{1,3}\b|[A-Za-z]{3,}\^\d{1,3}\b(?![.,]?\d)"
    r"|(?<=[a-z]) \(\d{1,3}(?:\s?[,\u2013-]\s?\d{1,3})*\)(?=[.,;:])"  # ASM's and PNAS's "control (1, 2)." — a number in brackets closed by punctuation, not an enumeration's "(1) the"
    r"|\([A-Z][A-Za-z&'\u2019. -]{1,60}?,? (?:19|20)\d{2}[a-z]?(?:[;,][^)]{0,80})?\)"  # "(Plastics Europe 2024)", "(Smith and Lee, 2019; Wu 2020)"
)  # "[12]", "(2019)", "et al.", or a superscript after the full stop: "(PL). 1 - 3 These"


def _front_by_meaning(text: str, repairs: dict[str, int] | None = None) -> str | None:
    """What a line of front matter the rules have no word for resembles — the embedder's
    answer among the kinds above, or None when it is unsure, absent, or says prose. A
    line of front matter is short and cites nothing: a block of more than forty words, or
    one with a citation in it, is prose whatever it resembles (an abstract paragraph that
    opens with a date read as `dates` once, and its citations went unlinked)."""
    o = _oracle()
    if o is None or len(text.split()) > 40 or _CITES.search(text):
        return None
    v = o.nearest("front", text)
    if v.name in _FRONT_KINDS:
        if repairs is not None:
            repairs["front_meaning"] = repairs.get("front_meaning", 0) + 1
        return v.name
    return None


def _front_kind(text: str, has_abstract_heading: bool, repairs: dict[str, int] | None = None, meaning: bool = True) -> str:
    """What a line of front matter is: authors, affiliations, dates, correspondence,
    keywords, a notice, the abstract when its heading was not detected, or other. The
    rules answer first; a line they have no word for is asked of the embedder, unless
    `meaning` is off (a paragraph inside the abstract stays there on a resemblance)."""
    t = text.strip()
    words = t.split()
    if _KEYWORDS.match(t):
        return "keywords"
    if _EDITOR_LINE.match(t) and len(words) <= 14:
        return "notice"  # "Academic Editors: Steven C. Cook and Simona Sagona" — MDPI's, beside the dates
    if (_DATE_LINE.search(t) and len(words) <= 30) or _BARE_DATE.match(t) or _dates_only(t):
        return "dates"  # …and the line that is nothing but dates and the words beside them  # "Received 22nd August 2026 …", or IOP's "21 January 2015" on a line of its own under "RECEIVED"
    if _CORRESPONDENCE.search(t) and len(words) <= 60:
        return "correspondence"
    if _FUNDING.match(t) and len(words) <= 80:
        return "funding"  # "The authors have no conflicts of interest to disclose.", "Funding: …"
    if _FURNITURE.search(t) and (len(words) <= 40 or _CITE_LINE.match(t)):
        return "notice"
    if _citation_line(t) or (_LICENCE.search(t) and len(words) <= 120):
        return "notice"  # the paper's own citation with its DOI; the licence sentence — at any length, never the abstract
    if _looks_like_authors(t) or _name_list(t):
        return "authors"  # before the length rule: twelve authors with their markers run past forty words
    if _AFFILIATION.search(t) and len(words) <= 60 and _caps_dense(t):
        return "affiliations"  # "Associations between wildfire smoke and cardiorespiratory emergency department visits varied …" names a department and is a sentence
    if re.match(r"^\d+\s*[A-Z]", t) and len(words) <= 40:
        return "affiliations"
    if _affiliation_like(t):
        return "affiliations"  # Wiley's footnote block, four numbered institutes: an institution's name and not one verb of prose, at any length
    if len(words) >= 40 and not has_abstract_heading:
        return "abstract"
    if t.lower().strip(" .:") in _GENERIC_LABELS or (len(words) <= 3 and t.isupper()):
        return "notice"
    return (_front_by_meaning(t, repairs) if meaning else None) or "other"


def _built_over(intro_items: list[dict[str, Any]], abstract_items: list[dict[str, Any]], repairs: dict[str, int]) -> list[dict[str, Any]]:
    """The paragraphs after the abstract that no heading claims, with the reader's headings
    over them: one built "Introduction" — or, when the stretch runs on into results and
    discussion (Wiley's communications print no heading before "Experimental Section"),
    the block lanes cut it into runs (structure.build_headings), each with a built heading,
    the first run being the introduction whatever its paragraphs resemble: its place says
    so. Every heading is labelled `built`."""
    from .structure import build_headings  # noqa: PLC0415 - structure imports this module

    o = _oracle()
    if o is not None and abstract_items:
        seeded = [abstract_items[-1], *intro_items]  # build_headings takes the first long block for the abstract and cuts what follows
        got, _ = build_headings(seeded, 0, None, o, repairs)
        if len(got) > len(seeded):
            out = got[1:]
            first = next((it for it in out if it.get("_built")), None)
            if first is not None and first.get("_built_lane") in ("other", "introduction"):
                first["text"], first["_built_lane"] = "Introduction", "introduction"
            return out
    header = {"self_ref": "#/texts/built~introduction", "parent": {"$ref": "#/body"}, "children": [], "label": "section_header", "text": "Introduction", "level": 1, "prov": list(intro_items[0].get("prov") or []), "_built": True, "_built_lane": "introduction"}
    repairs["built_headings"] = repairs.get("built_headings", 0) + 1
    return [header, *intro_items]


def build_tree(doc: dict[str, Any], key: str, title_hint: str | None = None, judge: Any = None, record: dict[str, Any] | None = None) -> Tree:
    """The node tree for one document, given Docling's exported dict.

    `title_hint` is a title the file itself declares (a PDF's Title metadata); it decides
    between candidates on the first page and stands in when the page offers none.
    `judge(a, b, ctx) -> bool | None` is asked about adjacent blocks the joining rules
    leave alone — see judge.py; None means no verdict, and the blocks stay apart.
    `record` is what the library knows of the paper (`{"journal": …}`): the journal's name tells a
    banner read as a heading from a heading. Every reader must pass it — `pairs` through
    `harness.read_paper` as much as the worker — or a measurement measures another reader.
    """
    pages = {int(k): Page(page_no=int(k), width=float(v["size"]["width"]), height=float(v["size"]["height"])) for k, v in (doc.get("pages") or {}).items()}
    title = (doc.get("name") or key).strip()

    root = Node(node_id=key, parent=None, ordinal=0, depth=0, type="document", label="document", level=None, role="other", heading=None, ancestry=[], text="", page=None, bbox=None, self_ref="#/body")
    # stack of (level, node); the root sits at level 0
    stack: list[tuple[int, Node]] = [(0, root)]
    counters: dict[str, int] = {}
    roles: dict[str, int] = {}
    caption_refs: set[str] = set()
    decorative = _decorative_pictures(doc)
    furniture = _recurring_furniture(doc)
    dropped: dict[str, int] = {}
    dropped_items: list[dict[str, Any]] = []
    repairs: dict[str, int] = {}
    notes: list[dict[str, Any]] = []
    journal = (record or {}).get("journal")
    _adopt_captions(doc, repairs)

    def next_id(parent: Node, kind: str) -> str:
        counters[parent.node_id] = counters.get(parent.node_id, 0) + 1
        return f"{parent.node_id}#{kind}-{counters[parent.node_id]}"

    def current_role(parent: Node) -> str:
        # The role of the top-level section, inherited; the document itself is `other`. A statement
        # nested inside the reference list ("Funding" set under the start of a Frontiers list) keeps
        # its paragraphs back matter, not entries.
        for level, node in reversed(stack):
            if level > 1 and node.role == "back":
                return "back"
        for level, node in stack:
            if level == 1:
                return node.role
        return parent.role if parent.type == "section" else "other"

    def attach(parent: Node, node: Node) -> None:
        node.ordinal = len(parent.children)
        node.depth = parent.depth + 1
        parent.children.append(node)
        roles[node.role] = roles.get(node.role, 0) + 1

    def make(parent: Node, kind: str, item: dict[str, Any], text: str, role: str, heading: str | None = None, level: int | None = None) -> Node:
        prov = _first_prov(item)
        page = pages.get(int(prov["page_no"])) if prov and "page_no" in prov else None
        ancestry = [n.heading for lvl, n in stack if lvl > 0 and n.heading]
        return Node(
            node_id=next_id(parent, kind),
            parent=parent.node_id,
            ordinal=0,
            depth=0,
            type=kind,
            label=str(item.get("label", kind)),
            level=level,
            role=role,
            heading=heading,
            ancestry=ancestry,
            text=text,
            page=int(prov["page_no"]) if prov and "page_no" in prov else None,
            bbox=_bbox_top_left(prov, page) if prov else None,
            self_ref=item.get("self_ref"),
            charspan=list(prov["charspan"]) if prov and prov.get("charspan") else None,
            pages=_pages_of(item),
            canonical=agreed(canonical_of(heading)[0], role) if kind == "section" and heading else None,  # the catalogue's exact word, in the section's own lane; the embedder's is asked where the section is made
        )

    prose_count = 0
    body_started = False  # the paper proper has begun: an introduction, methods, a numbered heading
    last_heading: str | None = None
    prose_since_heading = False
    items: list[dict[str, Any]] = []
    for item in _body_items(doc):
        rescued = rescue_merged_heading(item)
        items.extend(rescued if rescued else [item])
    kept: list[dict[str, Any]] = []
    for it in items:
        if it.get("self_ref") in furniture or it.get("_sidebar"):
            _drop(dropped, dropped_items, "furniture", it.get("text") or "")
        elif it.get("label") in _SKIP:  # a running head between two halves of a paragraph must not stand between them
            # recorded, not merely discarded: measured over the pilot's pages, the lines the
            # layout model labels a running head or a footer are the single largest class of
            # text the reading could not account for, and a count cannot tell a journal's URL
            # from a paragraph. Leaving text out is a decision; leaving it out in silence is not.
            _drop(dropped, dropped_items, "running", it.get("text") or "")
        else:
            kept.append(it)
    if pages:
        kept = _reading_order(kept, pages, repairs)
        # a PDF's fonts: "pH ¼ 7.4" was "pH = 7.4", "signi fi cantly" — see glyphs.py; every text,
        # the captions filed under their figures included
        every = {id(it): it for it in kept if it.get("label") not in _TABLE | _PICTURE}
        every.update((id(t), t) for t in doc.get("texts") or [] if t.get("label") not in _TABLE | _PICTURE)
        vocabulary = ligature_vocabulary(it.get("text") or "" for it in every.values())
        for it in every.values():
            text = it.get("text")
            if text:
                fixed = repair_glyphs(text, vocabulary)
                if fixed != text:
                    it["text"] = fixed
                    repairs["glyphs"] = repairs.get("glyphs", 0) + 1
        kept = _caption_tails(doc, kept, repairs)
    for k in ("recovered", "rebuilt", "formulas", "attached", "retexted_headings", "run_in_headings", "unfused_headings", "recovered_headings", "running_headings", "caps_headings", "depth_by_type", "depth_by_type_one_level"):
        if doc.get("_recovery", {}).get(k):
            repairs[k] = doc["_recovery"][k]
    for it in kept:
        if it.get("label") in _TEXTLIKE and it.get("text"):
            once = unrepeat(it["text"])
            if once != it["text"]:
                it["text"] = once
                repairs["unrepeated"] = repairs.get("unrepeated", 0) + 1  # a block that said its paragraph twice
    items = _stitch_fragments(kept, repairs, judge, doc.get("_indents"), float(doc.get("_unit") or 10.0))
    unfused: list[dict[str, Any]] = []
    for it in items:
        if it.get("label") == "section_header":
            it["text"] = _unspaced_heading(it.get("text") or "")
            two = split_fused_heading(it["text"]) if pages else None  # a PDF's layout model fuses lines; an XML's title is its title
            if two:
                unfused.append({**it, "text": two[0], "level": 1, "self_ref": f"{it.get('self_ref', '')}~top", **({"_typo_level": 1} if "_typo_level" in it else {})})
                unfused.append({**it, "text": two[1], "level": 2, "self_ref": f"{it.get('self_ref', '')}~sub", **({"_typo_level": 2} if "_typo_level" in it else {})})  # the row's look is the first heading's; the second is its subsection
                repairs["unfused"] = repairs.get("unfused", 0) + 1
                continue
        unfused.append(it)
    items = _infer_references(unfused, repairs)

    # --- the title, and the front matter between it and the first known heading ---------
    skip_refs: set[str] = set()  # items filed nowhere: a title taken from beyond the front matter
    title_ref = _pick_title(items, title_hint)
    if title_ref is None and title_hint:
        title = title_hint
        root.text = title_hint
    has_abstract_heading = any(it.get("label") == "section_header" and role_of(it.get("text") or "") == "abstract" for it in items)
    if pages:
        _refuse_front_furniture(items, title_ref, doc, journal, has_abstract_heading, repairs, notes)
    front_end = 0  # items[:front_end] are front matter
    long_prose = False
    for i, it in enumerate(items):
        page = _page_of(it)
        if _known_heading(it) or (page is not None and page > 2) or i >= 60:
            break
        if long_prose and _body_heading(it):
            break  # a heading of the paper's own after its prose has begun: the body, with no heading the vocabulary knows (an editorial's "Natural selfish genetic element systems")
        if it.get("label") in _PROSE and len((it.get("text") or "").split()) >= 40 and _prose_like((it.get("text") or "").strip()):
            long_prose = True
        front_end = i + 1
    items, front_end = build_headings(items, front_end, title_ref, _oracle(), repairs)  # a paper with no headings gets them built from its blocks
    front_items = items[:front_end]
    body_items = items[front_end:]
    if title_ref is not None and not any(it.get("self_ref") == title_ref for it in front_items):
        # a title beyond the front matter (RSC sets the journal's banner, the citation line and the
        # dates above it): the title is taken from where it stands, and the front matter is what
        # stands before it and after it up to the first heading the vocabulary knows
        front_items, body_items = [], items
        for ti, it in enumerate(items):
            vouched = bool(title_hint) and _norm_letters(it.get("text") or "")[:30] == _norm_letters(title_hint)[:30]
            if it.get("self_ref") == title_ref and (it.get("label") in ("title", "section_header") or vouched) and _page_of(it) in (None, 1):
                title = re.sub(r"\s+", " ", it.get("text") or "").strip() or title
                root.text = title
                end = ti + 1
                for j in range(ti + 1, len(items)):
                    page = _page_of(items[j])
                    if _known_heading(items[j]) or (page is not None and page > 2) or j - ti >= 60:
                        break
                    end = j + 1
                pre, post, rest = items[:ti], items[ti + 1 : end], items[end:]
                # a heading the vocabulary knows before the title: the layout model read the
                # introduction's first lines (RSC's left column, under the abstract) before the
                # title block. That heading and the prose right after it lead the body, and the
                # long paragraphs after the abstract continue it — an abstract in this layout is
                # one paragraph, and the front matter's own lines stay where they are
                lead: list[dict[str, Any]] = []
                start = next((k for k, x in enumerate(pre) if _known_heading(x)), None)
                if start is not None:
                    stop = start + 1
                    while stop < len(pre) and pre[stop].get("label") in ("text", "paragraph") and (pre[stop].get("text") or "").strip():
                        stop += 1
                    lead, pre = pre[start:stop], pre[:start] + pre[stop:]
                    a = next((k for k, x in enumerate(post) if x.get("label") in _PROSE and _front_kind((x.get("text") or "").strip(), has_abstract_heading, meaning=False) == "abstract"), None)
                    if a is not None:
                        moved = [x for x in post[a + 1 :] if x.get("label") in _TABLE | _PICTURE | _CAPTION or (x.get("label") in _PROSE and len((x.get("text") or "").split()) >= 15 and _front_kind((x.get("text") or "").strip(), has_abstract_heading, meaning=False) in ("abstract", "other"))]
                        post = post[: a + 1] + [x for x in post[a + 1 :] if not any(x is m for m in moved)]
                        lead += moved
                    repairs["reordered"] = repairs.get("reordered", 0) + len(lead)
                front_items, body_items = pre + [it] + post, lead + rest
                break
    if front_items:
        for it in front_items:
            if it.get("self_ref") == title_ref:
                title = re.sub(r"\s+", " ", it.get("text") or "").strip() or title
                root.text = title
        before_title = title_ref is not None
        related_content = False
        reviewers = False  # Frontiers' "EDITED BY" / "REVIEWED BY" block: names and affiliations that are not the paper's
        grouped: list[tuple[str, list[dict[str, Any]]]] = []
        abstract_items: list[dict[str, Any]] = []
        intro_items: list[dict[str, Any]] = []  # the introduction's opening, orphaned before its heading
        for it in front_items:
            if it.get("self_ref") == title_ref:
                before_title = False
                related_content = False  # what stood above the title is done with
                continue
            label = it.get("label")
            text = (it.get("text") or "").strip()
            if label in _TABLE | _PICTURE | _CAPTION:
                grouped.append(("keep", [it]))
                continue
            if not text:
                continue
            if before_title and len(text.split()) < 40:
                if (related_content or re.search(r"you may also like|related content", text, re.I)) and label != "footnote":
                    related_content = True  # IOP's "You may also like": other papers' titles and authors, to the end of the page (a footnote is the paper's own)
                    _drop(dropped, dropped_items, "label", text)
                    continue
                if re.match(r"^\s*(?:edited by|reviewed by|handling editor|academic editor|editors?:)", text, re.I):
                    reviewers = True
                fk = _front_kind(text, has_abstract_heading, repairs)
                if reviewers:
                    if fk in ("correspondence", "dates", "keywords", "funding") or _CITE_LINE.match(text) or re.match(r"^\s*(?:citation|copyright|©)", text, re.I):
                        reviewers = False  # the block ends where the paper's own lines resume
                    else:
                        _drop(dropped, dropped_items, "label", text)  # an editor's or a reviewer's name and institution
                        continue
                if fk in ("authors", "affiliations", "dates", "correspondence", "keywords", "funding"):
                    kind = fk  # RSC's affiliations are footnotes on the first page, its dates a line above the title: what the rules can name stays
                elif len(text.split()) <= 6 and not _FURNITURE.search(text) and label in _PROSE | _HEADER:
                    kind = "notice"  # "REVIEW", "ORIGINAL RESEARCH ARTICLE": what the publisher printed above the title, and what the paper's type is read from
                elif fk == "notice" and _citation_line(text):
                    kind = "notice"  # the paper's own citation with its DOI: kept, whatever its length
                elif label == "footnote" and not _FURNITURE.search(text):
                    kind = "other"  # a footnote above the title (RSC's ESI note, "Present address: …") is the paper's: kept, unnamed
                else:
                    _drop(dropped, dropped_items, "label", text)  # the journal's name, "Contents lists available at …", "Cite this:"
                    continue
                if grouped and grouped[-1][0] == kind:
                    grouped[-1][1].append(it)
                else:
                    grouped.append((kind, [it]))  # consecutive lines of one kind are one node, line by line (the paper's type reads the notices one line at a time)
                continue  # a block of forty words or more above the title is an abstract or a summary, never a label: it stays
            kind = _front_kind(text, has_abstract_heading, repairs)
            if related_content:
                kind = "notice"  # IOP's "You may also like": other papers' titles and authors, to the end of the page
            elif kind == "notice" and re.search(r"you may also like|related content", text, re.I):
                related_content = True
            # an abstract cites nothing and front matter cites nothing: a paragraph of fifteen words or more
            # with a citation marker is the introduction's, and so is every plain paragraph after it
            if not before_title and not has_abstract_heading and kind in ("abstract", "other") and label in _PROSE and _prose_like(text) and ((intro_items and (len(text.split()) >= 8 or _CITES.search(text))) or (abstract_items and _CITES.search(text) and len(text.split()) >= 15)):
                intro_items.append(it)  # after an abstract paragraph (the abstract itself may cite), and not with an "Abstract" heading later in the paper: what stands before it is its front matter, whatever it cites
                continue
            if kind == "abstract":
                abstract_items.append(it)
                continue
            if grouped and grouped[-1][0] == kind and kind != "keep":
                grouped[-1][1].append(it)
            else:
                grouped.append((kind, [it]))
        if grouped:
            front = make(root, "section", {"label": "section_header"}, "", "other", heading="Front matter", level=1)
            attach(root, front)
            for kind, group in grouped:
                if kind == "keep":
                    body_items.insert(0, group[0])
                    continue
                joined = "\n".join(re.sub(r"\s+", " ", g.get("text") or "").strip() for g in group)
                node = make(front, "meta", {**group[0], "label": kind}, joined, "other")
                node.label = kind
                attach(front, node)
        if intro_items:
            body_items[0:0] = _built_over(intro_items, abstract_items, repairs)
        if abstract_items:
            abstract = make(root, "section", {"label": "section_header", **{k: v for k, v in abstract_items[0].items() if k == "prov"}}, "", "abstract", heading="Abstract", level=1)
            attach(root, abstract)
            stack.append((1, abstract))
            for it in abstract_items:
                text = _ABSTRACT_LEAD.sub("", re.sub(r"\s+", " ", it.get("text") or "")).strip()
                attach(abstract, make(abstract, "paragraph", it, text, "abstract"))
        items = body_items

    if pages:
        _mark_abstract_parts(items, repairs)
    first_title_taken = title_ref is not None
    for index, item in enumerate(items):
        label = item.get("label", "text")
        self_ref = item.get("self_ref", "")
        if self_ref in caption_refs or self_ref in skip_refs:
            continue  # filed under its table or picture when that was made, or taken as the title
        text = (item.get("text") or "").strip()
        if label in _PROSE | _LIST | _HEADER and text and not re.search(r"[A-Za-z0-9\u0370-\u03ff]", text):
            _drop(dropped, dropped_items, "junk", text)  # ")", "|", a control character: nothing a reader would keep
            continue
        if label in _PROSE and len(text) <= 80 and re.match(r"^\s*(?:&|©|\(c\))\s*\d{4}\b", text):
            _drop(dropped, dropped_items, "furniture", text)  # "& 2012 Elsevier Ltd. All rights reserved."
            continue
        if label not in _HEADER and text:
            prose_count += 1
            prose_since_heading = True

        if label in _HEADER:
            if label == "title":
                if not first_title_taken:
                    first_title_taken = True
                    if text:
                        title = text
                    root.text = text
                continue
            text = undouble(text)
            if last_heading is not None and not prose_since_heading and _same_heading(text, last_heading):
                # The same heading twice with nothing between: a page break echoed it.
                continue
            following = next((it for it in items[index + 1 :] if (it.get("text") or "").strip() or it.get("label") in _TABLE | _PICTURE), None)
            heads_nothing = following is None or following.get("label") in _HEADER
            furniture = item.get("_furniture_head")
            if furniture or (heads_nothing and not body_started and _page_of(item) in (None, 1) and role_of(text) == "other" and numbering_depth(text) is None and _front_by_meaning(text) == "notice"):
                # the journal's name or the article's type set as a heading on the first page
                # ("RSC Advances", "RESEARCH ARTICLE"): front matter, not a section. Where the line's
                # own shape says so (`_refuse_front_furniture`) the prose under it is the body's and
                # this holds whatever follows; on the embedder's word alone it holds only when
                # another heading follows, since a wrong verdict would file that prose under the
                # section before.
                if not furniture:
                    repairs["front_meaning"] = repairs.get("front_meaning", 0) + 1
                front = next((c for c in root.children if c.type == "section" and c.heading == "Front matter"), None)
                if front is None:
                    front = make(root, "section", {"label": "section_header"}, "", "other", heading="Front matter", level=1)
                    attach(root, front)
                node = make(front, "meta", {**item, "label": "notice"}, text, "other")
                node.label = "notice"
                attach(front, node)
                continue
            level = int(item.get("level") or 1)
            if label == "title" or item.get("_built"):
                level = 1
            elif item.get("_runin"):
                open_top_node = next((n for lvl, n in stack if lvl == 1), None)
                level = 2 if open_top_node is not None and open_top_node.label != "built" else 1  # a heading printed run in at a paragraph's front is a subsection of the section it stands in, whatever its name ("Statistics" under Materials and Methods) — never of a heading the reader built
            else:
                open_top_node = next((n for lvl, n in stack if lvl == 1), None)
                level = infer_level(text, level, open_top_node is not None and open_top_node.label != "built", stated=not pages, typo_level=item.get("_typo_level"), list_number=bool(item.get("_list_number")), open_top_role=open_top_node.role if open_top_node is not None else None)
                if level > 1 and open_top_node is not None and open_top_node.label == "built" and numbering_depth(text) is None:
                    level = 1  # a printed heading is never the child of one the reader built: the built "Introduction" covers the prose before the first printed heading, no more
                if level == 1 and role_of(text, meaning=False) in ("other", "back") and pages and next((n.role for lvl, n in stack if lvl == 1), None) == "references" and _entries_follow(items, index):
                    level = 2  # a statement the layout model read between the entries — named by the catalogue, by meaning, or by the vocabulary itself ("Data availability statement", "Funding": Frontiers sets them in the left column under the start of the list, and 93 entries had filed as acknowledgements) — stays inside the list, since the list goes on after it
            as_part = bool(pages and item.get("_abstract_part") and not body_started and level == 1)
            if as_part:
                host = next((n for lvl, n in stack if lvl == 1 and n.role == "abstract"), None) or next((c for c in reversed(root.children) if c.type == "section" and c.level == 1 and c.role == "abstract"), None)
                if host is None:
                    while len(stack) > 1:
                        stack.pop()
                    host = make(root, "section", {**item, "label": "section_header"}, "", "abstract", heading="Abstract", level=1)
                    host.label = "built"  # the paper printed its abstract's parts and no heading over them
                    attach(root, host)
                    repairs["built_headings"] = repairs.get("built_headings", 0) + 1
                while len(stack) > 1 and stack[-1][1] is not host:
                    stack.pop()
                if stack[-1][1] is not host:
                    stack.append((1, host))
                level = 2
            number = top_number(text)
            open_top = next((n for lvl, n in stack if lvl == 1), None)
            if level > 1 and number is not None and open_top is not None and open_top.heading and top_number(open_top.heading) not in (None, number) and not item.get("_list_number"):
                # "3.2 …" arrives while "2. Methods" is open and no "3." was seen: the layout
                # model dropped the parent heading. Stand in an untitled section rather than
                # file results under methods; its role is `other`, which is honest.
                while len(stack) > 1:
                    stack.pop()
                read_before = next((c for c in reversed(root.children) if c.type == "section" and c.heading and c.label != "built" and top_number(c.heading) == number), None)
                if read_before is not None:
                    # …unless the parent *was* read, out of order: a two-column page whose right
                    # column came first puts "2 METHODS" and "2.1" before "1 INTRODUCTION", and
                    # "2.2" then arrives with the introduction open. The author numbered both, so
                    # this is the author's own word for where it belongs, not a guess
                    stack.append((1, read_before))
                    repairs["renumbered_parent"] = repairs.get("renumbered_parent", 0) + 1
                else:
                    ghost = make(root, "section", {"label": "section_header"}, "", "other", heading=f"{number}. (heading not detected)", level=1)
                    attach(root, ghost)
                    stack.append((1, ghost))
            open_here = next((n for lvl, n in stack if lvl == level), None)
            if open_here is not None and open_here.heading and _same_heading(text, open_here.heading):
                # "4. Discussion" again after a page break: the same section continues.
                while stack[-1][1] is not open_here:
                    stack.pop()
                last_heading = text
                prose_since_heading = False
                continue
            # pop to the nearest ancestor shallower than this header
            while len(stack) > 1 and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1]
            last_heading = text
            prose_since_heading = False
            role = role_of(text, meaning=False) if level == 1 else (role_of(text, meaning=False) if role_of(text, meaning=False) == "back" else current_role(parent))  # a statement nested inside the reference list is still a statement
            if role == "other" and level == 1 and text:
                by_rule = top_level_lane(text)
                role = by_rule or role_of(text)  # rules first: the vocabulary, then the catalogue ("Case presentation" is results, "Declaration of competing interest" is back), the embedder last
                if by_rule is None and role in _NOT_NUMBERED and top_number(text) is not None:
                    role = "other"  # a heading that carries a body number is a body section: of 3,664 back-matter sections across three corpora, the only numbered one was a review's "8. Regulatory and Ethical Considerations", laned back by meaning — unassignable beats misassigned
            if as_part:
                role = "abstract"  # a part of the structured abstract, whatever its name says
            if role == "abstract" and normalise_heading(text) != "abstract" and (body_started or top_number(text) is not None):
                role = "discussion"  # "6 Summary", a closing section: the abstract came first (a heading that says "Abstract" is the abstract, wherever the layout read it)
            if level == 1 and (role not in ("abstract", "other") or top_number(text) is not None):
                body_started = True
            if item.get("_built"):
                role = item.get("_built_lane") if item.get("_built_lane") in CANONICAL else "other"
                body_started = body_started or role != "other"
            node = make(parent, "section", item, "", role, heading=text or "(untitled section)", level=level)
            if item.get("_built"):
                node.label = "built"  # the reader's heading, not the author's: visible in the row
            if text:
                name, how = canonical_of(text, _oracle())
                node.canonical = agreed(name, role)  # a name whose lane is another's is no name: "Reference materials" under methods is not References
                if how == "meaning" and node.canonical is not None:
                    repairs["canonical_meaning"] = repairs.get("canonical_meaning", 0) + 1
            attach(parent, node)
            stack.append((level, node))
            continue

        parent = stack[-1][1]
        if len(stack) > 1 and stack[-1][0] > 1 and parent.role == "back" and next((n.role for lvl, n in stack if lvl == 1), None) == "references" and label in ("list_item", "text", "paragraph") and len(text) < 700 and _REF_ENTRY.match(text) and _A_YEAR.search(text):
            while len(stack) > 1 and stack[-1][0] > 1:
                stack.pop()  # an entry again: the statement read between the entries is over, the list goes on
            parent = stack[-1][1]
        box = next((n for lvl, n in reversed(stack) if lvl >= 1 and n.type == "section" and normalise_heading(n.heading or "") in _BOX_HEADINGS), None)
        message = None if box is not None else next((n for lvl, n in reversed(stack) if lvl >= 1 and n.type == "section" and _MESSAGE_BOX.match(normalise_heading(n.heading or ""))), None)
        early_back = None if (box is not None or message is not None) else next((n for lvl, n in reversed(stack) if lvl >= 1 and n.type == "section" and n.role == "back"), None)
        if pages and ((box is not None and box.children and label in _PROSE and _box_prose(text)) or (message is not None and _message_ends(message, text, label, root)) or (early_back is not None and _back_ends(early_back, text, label, root))):
            # a front-matter box holds a few words — the keywords, the highlights' bullets: prose read after
            # it is the body, which the layout read after the box (Wiley's KEYWORDS under the abstract, a
            # highlights box, Elsevier's article info; a key-message box's bullets, then prose that cites).
            # It joins the introduction already read, or opens one
            intro = next((c for c in reversed(root.children) if c.type == "section" and c.role == "introduction"), None)
            while len(stack) > 1:
                stack.pop()
            if intro is None:
                intro = make(root, "section", {**item, "label": "section_header"}, "", "introduction", heading="Introduction", level=1)
                intro.label = "built"
                attach(root, intro)
                repairs["built_headings"] = repairs.get("built_headings", 0) + 1
            stack.append((1, intro))
            repairs["box_prose" if early_back is None else "back_box_prose"] = repairs.get("box_prose" if early_back is None else "back_box_prose", 0) + 1
            body_started = True
            parent = intro
        read_before = next((c for c in reversed(root.children) if c.type == "section" and c.role == "introduction"), None) if body_started else None
        if parent.type == "section" and parent.role == "abstract" and parent.children and label in _PROSE and len(text.split()) >= 15 and _prose_like(text) and (
            (_CITES.search(text) and (not body_started or read_before is not None))
            or (read_before is not None and len(text.split()) >= 40 and (_page_of(item) or 0) > (parent.page or 0))
        ):
            # the abstract has a paragraph already and this one cites: the introduction has begun without its
            # heading (missed by the layout model, or never printed) — a built "Introduction" opens here. Where the
            # introduction's heading was read before the abstract (Elsevier's first page: the left column's
            # "1. Introduction" under the abstract box), the prose after the abstract goes back to it
            while len(stack) > 1:
                stack.pop()
            if read_before is not None:
                intro = read_before
                repairs["abstract_to_introduction"] = repairs.get("abstract_to_introduction", 0) + 1
            else:
                intro = make(root, "section", {**item, "label": "section_header"}, "", "introduction", heading="Introduction", level=1)
                intro.label = "built"
                attach(root, intro)
                repairs["built_headings"] = repairs.get("built_headings", 0) + 1
            stack.append((1, intro))
            body_started = True
            parent = intro
        if parent is root and text:
            # Prose before the first heading — authors, affiliations, dates, the abstract when
            # its heading was not detected — is front matter: kept, in a section that says so.
            front = next((c for c in root.children if c.type == "section" and c.heading == "Front matter"), None)
            if front is None:
                front = make(root, "section", {"label": "section_header"}, "", "other", heading="Front matter", level=1)
                attach(root, front)
            stack.append((1, front))
            parent = front
        role = current_role(parent)
        if label in _TABLE:
            node = make(parent, "table", item, text, role)
            node.table = _table_cells(item)
            attach(parent, node)
            for cap in item.get("captions", []):
                cap_item = _resolve(doc, cap["$ref"]) if "$ref" in cap else None
                if cap_item:
                    caption_refs.add(cap_item.get("self_ref", cap["$ref"]))
                    c = make(node, "caption", cap_item, (cap_item.get("text") or "").strip(), role)
                    attach(node, c)
            continue
        if label in _PICTURE:
            if item.get("self_ref") in decorative:
                _drop(dropped, dropped_items, "picture", item.get("text") or "")
                continue
            node = make(parent, "picture", item, text, role)
            attach(parent, node)
            for cap in item.get("captions", []):
                cap_item = _resolve(doc, cap["$ref"]) if "$ref" in cap else None
                if cap_item:
                    caption_refs.add(cap_item.get("self_ref", cap["$ref"]))
                    c = make(node, "caption", cap_item, (cap_item.get("text") or "").strip(), role)
                    attach(node, c)
            continue
        if label in _CAPTION:
            attach(parent, make(parent, "caption", item, text, role))
            continue
        if label in _LIST and not text:
            _drop(dropped, dropped_items, "junk", text)  # a list item with no words: Wiley's XML gives one per item and the words apart
            continue
        if label in _LIST:
            marker = (item.get("marker") or "").strip()
            if re.fullmatch(r"\[?\d{1,3}[\].)]?", marker) and not text.startswith(marker):
                text = f"{marker} {text}"  # the entry's number is part of its text: "[12] Smith, J. …"
            attach(parent, make(parent, "list_item", item, text, role))
            continue
        if label in _PROSE and text and not body_started and _ABSTRACT_BLOCK.match(text) and len(text.split()) >= 30 and not any(n.role == "abstract" for _, n in stack) and not any(c.type == "section" and c.role == "abstract" for c in root.children):
            # Elsevier's "A B S T R A C T" is a word in the text, not a heading: the abstract opens here
            while len(stack) > 1:
                stack.pop()
            abstract = make(root, "section", item, "", "abstract", heading="Abstract", level=1)
            attach(root, abstract)
            stack.append((1, abstract))
            attach(abstract, make(abstract, "paragraph", item, _ABSTRACT_BLOCK.sub("", text).strip(), "abstract"))
            continue
        early = (_page_of(item) or 9) <= 2 and parent.role not in ("references", "back") and _late_front(text)
        if label in _PROSE and text and (not body_started or early) and parent is not root:
            # keywords, a copyright line, the journal's home page, a correspondence address,
            # after the abstract's heading and before the paper proper: front matter. And the same
            # lines where the layout model read them after the body's first heading: MDPI sets
            # "Academic Editors", "Received / Revised / Accepted / Published" and its licence in the
            # left column of the first page, which the model reads between the introduction's
            # paragraphs, and 79 chunks of the working half carried text no paper of it has. A short
            # line of that shape on the first pages is front matter wherever it was read; the
            # embedder is not asked once the body has begun, only the line's own shape counts
            fk = _front_kind(text, True, repairs, meaning=not body_started and parent.role != "abstract")
            if fk in _FRONT_KINDS:
                front = next((c for c in root.children if c.type == "section" and c.heading == "Front matter"), None)
                if front is None:
                    front = make(root, "section", {"label": "section_header"}, "", "other", heading="Front matter", level=1)
                    attach(root, front)
                node = make(front, "meta", {**item, "label": fk}, re.sub(r"\s+", " ", text).strip(), "other")
                node.label = fk
                attach(front, node)
                continue
        kind = "paragraph"
        if label in ("formula", "code", "footnote"):
            kind = label
        if not text and kind == "paragraph":
            continue
        attach(parent, make(parent, kind, item, text, role))

    # a wrapper heading with nothing under it — PMC's "Associated Data", a "Declarations" whose
    # parts the file never gave — is not a section a reader can open
    for section in [n for n in _descendants(root) if n.type == "section" and not n.children and n.heading and _WRAPPER_HEADINGS.match(n.heading)]:
        parent = next((n for n in _descendants(root) if any(c is section for c in n.children)), None)
        if parent is not None:
            parent.children.remove(section)
            _drop(dropped, dropped_items, "empty", section.heading or "")
    tree = Tree(
        title=title,
        pages=[pages[k] for k in sorted(pages)],
        root=root,
        roles=dict(sorted(roles.items())),
        has_methods=roles.get("methods", 0) > 0,
        dropped=dropped,
        dropped_items=dropped_items,
        repairs=repairs,
        notes=notes,
    )
    lane_sections(tree, _oracle(), repairs)  # a top-level section whose heading names nothing, read by its paragraphs
    return tree
