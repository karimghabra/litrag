"""How a paper is set, as a key — so a layout seen before can be recognised without being named.

A publisher's layout is a convention, and the reader's whole problem is that it has been
learning conventions by having them written into it one publisher at a time. A fingerprint is
the other way round: read the convention **off the document**, and let a paper that is set like
one already understood say so by itself.

What is in it is only what a typesetter chooses and a reader can see without knowing whose it
is — the page size, whether the text runs in one column or two, the face and size of the body,
the faces that stand apart from it and how, and where the type block sits on the page. No
journal name, no DOI prefix, no string from any publisher. Two papers from one journal's one
template land on the same key; the same journal's letters and its research articles usually do
not, which is right, because they are read differently.

`distance` is what makes it useful before a key ever repeats: a paper whose nearest known
template is far away is a first contact, and `PLAN.md` asks for those to be flagged for review
however confident the reading looks. Whether distance actually predicts a bad reading is a
measurement and not an assumption; it is priced in `campaign/reports/phase5.md` and nothing in
the reader acts on it until that number exists.

Nothing here asks a model and nothing keys on a publisher.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .typography import Row, Style, apart, body_style, dominant, styled_rows

#: a row shorter than this is a folio, a running head or a stray: it says nothing about the
#: type block, and letting it into the margins would make every paper's margins the page's
MIN_ROW_LETTERS = 12
#: sizes are rounded to the half point. Two settings of one template differ by rounding noise
#: in the fourth decimal; a real difference in body size is never smaller than half a point
SIZE_STEP = 2.0
#: a face used for fewer letters than this share of the body's is a symbol run or a logo
HEAD_SHARE = 0.004


def _round(size: float) -> float:
    return round(size * SIZE_STEP) / SIZE_STEP


#: an embedded subset carries a six-letter tag before its face's name, and the tag is random per
#: file: one publisher's two papers read `FVKCKB+ArnoPro-Regular` and `VEHTVM+ArnoPro-Regular`,
#: which is one typeface and was counted as two. Some producers hash the whole name instead
#: (`AdvTTd9b1c495`) and nothing can be done about those.
_SUBSET = re.compile(r"^[A-Z]{6}\+")


def face_of(name: str) -> str:
    return _SUBSET.sub("", name or "")


@dataclass(frozen=True)
class Fingerprint:
    """What a typesetter chose, and nothing about who they were."""

    width: float
    height: float
    columns: int
    body: tuple[str, int, float] | None
    #: the faces that stand apart from the body, with how they stand apart: ("bold", 11.0)
    heads: tuple[tuple[str, float], ...] = ()
    #: the type block as fractions of the page: left, top, right, bottom
    block: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    pages: int = 0

    def key(self) -> str:
        """The exact identity: two papers with this key are set the same way.

        Deliberately coarser than the fingerprint. The type block is measured to the thousandth
        of a page and the page count is whatever the paper is, so a key over the whole thing is
        unique to the paper: over 114 DEV papers it repeated **not once**. What a template fixes
        is the page, the columns, the body and the faces that stand apart; the block and the
        length are for `distance`, which is a question of degree.
        """
        what = [self.width, self.height, self.columns, self.body, sorted(self.heads)]
        return hashlib.sha1(json.dumps(what, sort_keys=True).encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "key": self.key()}


def fingerprint(pdf_path: Path) -> Fingerprint | None:
    """The document's own template, from its type alone. None for a PDF with no text layer."""
    try:
        rows_by_page = styled_rows(Path(pdf_path))
    except Exception:  # noqa: BLE001 — a PDF pdfium cannot read has no typography to fingerprint
        return None
    if not rows_by_page:
        return None
    body = body_style(rows_by_page)
    rows = [r for rows in rows_by_page.values() for r in rows if r.letters >= MIN_ROW_LETTERS]
    if not rows:
        return None

    widths = [max(r.r for r in rs) for rs in rows_by_page.values() if rs]
    heights = [max(r.t for r in rs) for rs in rows_by_page.values() if rs]
    width = _round(statistics.median(widths)) if widths else 0.0
    height = _round(statistics.median(heights)) if heights else 0.0

    heads: Counter[tuple[str, float]] = Counter()
    if body is not None:
        for row in rows:
            st = dominant(row)
            how = apart(st, body)
            if how:
                heads[(how, _round(st[2]))] += row.letters
    floor = HEAD_SHARE * sum(r.letters for r in rows)

    return Fingerprint(
        width=width, height=height,
        columns=_columns(rows, width),
        body=(face_of(body[0]), body[1], _round(body[2])) if body else None,
        heads=tuple(sorted(h for h, n in heads.items() if n >= floor)),
        block=_block(rows, width, height),
        pages=len(rows_by_page),
    )


def _columns(rows: list[Row], width: float) -> int:
    """One column or two, from where the rows begin.

    A two-column setting has two left edges and little between them; a one-column setting has
    one, plus indents. Counted on left edges rather than on gaps, because a gap between two
    blocks of a single column looks exactly like a gutter on any one page.
    """
    if width <= 0:
        return 1
    left = Counter(round(r.l / width, 2) for r in rows)
    common = [x for x, n in left.items() if n >= 0.15 * len(rows)]
    return 2 if any(x > 0.4 for x in common) and any(x < 0.25 for x in common) else 1


def _block(rows: list[Row], width: float, height: float) -> tuple[float, float, float, float]:
    """Where the type sits, as fractions of the page — the 5th and 95th percentiles of the
    rows' edges, so one stray row off the block does not become the margin."""
    if width <= 0 or height <= 0:
        return (0.0, 0.0, 1.0, 1.0)

    def pct(values: list[float], p: float) -> float:
        values = sorted(values)
        return values[min(int(p * len(values)), len(values) - 1)] if values else 0.0

    return (round(pct([r.l for r in rows], 0.05) / width, 3),
            round(pct([r.b for r in rows], 0.05) / height, 3),
            round(pct([r.r for r in rows], 0.95) / width, 3),
            round(pct([r.t for r in rows], 0.95) / height, 3))


#: what each part of a fingerprint is worth when two are compared. The body's face and size
#: carry most of it because they are what a template fixes hardest and what a reader leans on
WEIGHTS = {"page": 0.15, "columns": 0.2, "body": 0.35, "heads": 0.2, "block": 0.1}
#: how the body's weight divides between its face and its size. The face carries more: a
#: template reset a point smaller for a supplement is the same template, and a different face is
#: a different one. Weighting them alike made "same face, other size" and "other face, same
#: size" cost exactly the same, which a test caught.
FACE, SIZE = 0.7, 0.3


def distance(a: Fingerprint | None, b: Fingerprint | None) -> float:
    """0 for two papers set identically, 1 for two with nothing in common.

    Not a metric in the strict sense and not claimed to be one: it is a weighted disagreement,
    read only as "nearer" and "further".
    """
    if a is None or b is None:
        return 1.0
    d = 0.0
    d += WEIGHTS["page"] * (0.0 if abs(a.width - b.width) < 2 and abs(a.height - b.height) < 2 else 1.0)
    d += WEIGHTS["columns"] * (0.0 if a.columns == b.columns else 1.0)
    if a.body is None or b.body is None:
        d += WEIGHTS["body"] * (0.0 if a.body == b.body else 1.0)
    else:
        d += WEIGHTS["body"] * (FACE * (a.body[0] != b.body[0])
                                + SIZE * (abs(a.body[2] - b.body[2]) > 0.5))
    union = set(a.heads) | set(b.heads)
    d += WEIGHTS["heads"] * (0.0 if not union else 1.0 - len(set(a.heads) & set(b.heads)) / len(union))
    d += WEIGHTS["block"] * min(1.0, sum(abs(x - y) for x, y in zip(a.block, b.block)) / 0.4)
    return round(d, 4)


def nearest(fp: Fingerprint | None, known: dict[str, Fingerprint]) -> tuple[str | None, float]:
    """The known template this paper is nearest to, and how far. `(None, 1.0)` for a first
    contact against an empty memory — which is every paper, until one is remembered."""
    if fp is None or not known:
        return None, 1.0
    best, how_far = None, 1.0
    for name, other in known.items():
        d = distance(fp, other)
        if d < how_far:
            best, how_far = name, d
    return best, how_far
