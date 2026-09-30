"""What must be true of a reading, checked against the reading itself — and where it is not.

`confidence.py` asks how far a reading can be trusted and answers with one number and a
handful of shares. That is the right answer for a person deciding whether to read a paper's
tree, and the wrong one for a reader deciding what to do about it: a share cannot be repaired.
This module asks the same kind of question and answers with a **location** — this heading, this
paragraph, this page — because a repair has to be applied somewhere, and because an invariant
that cannot say where it failed cannot be priced against the witness either.

Each check returns `pass`, `fail` or `n/a`, and a failure carries one `Violation` per place.
`n/a` is a first-class answer and is not a pass: a paper with no reference list has not
satisfied I4, and counting it as satisfied is how a corpus average comes to mean nothing. How
often a check is not applicable is reported beside its precision for the same reason.

**Nothing here is a hard check until DEV has priced it.** Every invariant carries a `stance`,
and it starts at `advisory`: it may feed confidence and it may be shown to a person, but no
repair acts on it. `stance` is data rather than code, so the measurement moves it and a commit
says which number did. P(reading error | fails) and P(fails | reading error) come from the
witness — `campaign/reports/phase4.md` — and an invariant that fires often and predicts nothing
is demoted there rather than argued with here.

Nothing in this module asks a model, and nothing keys on a publisher. I1, I11 and I12 need the
PDF beside the tree and are `n/a` without it.

    python -m litrag_parser.invariants parser/tests/fixtures/*.docling.json
    python -m litrag_parser.invariants --lib ~/.protracker/library/looped-ligament [--json]
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Callable, Iterable

from .citations import Citation, Ref, _expand_numeric, _NUMERIC, link_citations
from .confidence import EXPECTED
from .edges import figures
from .pairs import LANES, Unit, units_of, words
from .tree import Node, Tree, build_tree, split_fused_heading

#: six words is `confidence.K_DUP`, and the two must agree: the same repetition seen twice under
#: two definitions would be two findings about one fault
K_DUP = 6
MIN_PARAGRAPH = 20  # words; below this an unterminated line is a caption fragment, not a cut paragraph
_TERMINAL = re.compile(r"[.!?:;)\]”\"'’]\s*$|\.\s*\^?\[?\d[\d,–\- ]*\]?\s*$")  # `confidence._END`
_BULLET = re.compile(r"^\s*[•·▪■●○◦\-–—*»>]")
_UNTITLED = ("(untitled section)", "(heading not detected)")
_HEADING_NUMBER = re.compile(r"^\s*\(?(\d{1,2}(?:\.\d{1,2})*)\)?[.)]?\s+\S")
_ENTRY_NUMBER = re.compile(r"^\s*\[?(\d{1,3})[\].)]\s+")
_FIGURE_CALL = re.compile(r"\b(Fig(?:ure)?s?|Tables?|Schemes?)\.?\s*(\d{1,2})\b", re.I)
_YEAR = re.compile(r"\b(?:1[89]|20)\d{2}\b")
_DOI = re.compile(r"10\.\d{4,9}/\S+")
_PROSE_LANES = frozenset(LANES) - {"references"}
MAX_HEADING_WORDS = 16  # `typography.MAX_WORDS`: a heading row, run-in headings included


@dataclass(frozen=True)
class Violation:
    """One place a check failed, and enough about it to look."""

    code: str
    severity: str  # error | warn | info — `audit.py`'s grading, so one vocabulary covers both
    message: str
    node_id: str = ""  # "" when the fault is the document's and not a node's
    page: int | None = None
    text: str = ""  # the offending text, trimmed: a violation nobody can see is a number, not a finding
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Result:
    code: str
    name: str
    status: str  # pass | fail | n/a
    reason: str
    violations: tuple[Violation, ...] = ()

    @property
    def failed(self) -> bool:
        return self.status == "fail"

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "name": self.name, "status": self.status, "reason": self.reason,
                "violations": [v.to_dict() for v in self.violations]}


def _trim(text: str, n: int = 110) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


class Context:
    """One paper, and the pieces more than one check needs — each read once.

    A check takes the context rather than the tree so that `link_citations` and `units_of`, both
    of which walk the whole tree, are not paid for five times over. Everything here is a plain
    read of the reading; nothing asks a model and nothing touches the network.
    """

    def __init__(self, tree: Tree, *, pdf_path: Path | None = None,
                 kind: dict[str, Any] | None = None, record: dict[str, Any] | None = None) -> None:
        self.tree = tree
        self.pdf_path = pdf_path
        self.kind = kind or {}
        self.record = record or {}

    @property
    def paper_type(self) -> str | None:
        return self.kind.get("type")

    @cached_property
    def nodes(self) -> list[Node]:
        return list(self.tree.walk())

    @cached_property
    def units(self) -> list[Unit]:
        return units_of(self.tree)

    @cached_property
    def prose(self) -> list[Unit]:
        return [u for u in self.units if u.prose and u.tokens]

    @cached_property
    def sections(self) -> list[Node]:
        return [n for n in self.nodes if n.type == "section"]

    @cached_property
    def titled(self) -> list[Node]:
        """Sections the paper itself titled: not front matter, not untitled, not one the reader built."""
        return [n for n in self.sections if n.heading and n.heading != "Front matter"
                and not n.heading.endswith(_UNTITLED) and n.label != "built"]

    @cached_property
    def _linked(self) -> tuple[list[Ref], list[Citation]]:
        return link_citations(self.tree, None)

    @property
    def refs(self) -> list[Ref]:
        return self._linked[0]

    @property
    def cites(self) -> list[Citation]:
        return self._linked[1]

    @cached_property
    def entries(self) -> list[Node]:
        return [n for n in self.nodes if n.role == "references"
                and n.type in ("list_item", "paragraph") and (n.text or "").strip()]

    @cached_property
    def accounting(self) -> dict[str, Any]:
        from .evaluate import accounting  # the campaign's definition of T1, not a second one

        return accounting(self.tree, self.pdf_path) if self.pdf_path else {}

    @cached_property
    def page_height(self) -> dict[int, float]:
        return {p.page_no: p.height for p in self.tree.pages}


# -- the checks ------------------------------------------------------------------------------
#
# Each takes a Context and returns a Result. A check never raises on a strange paper: a paper it
# cannot speak about is `n/a` with the reason, because a check that throws is a gate that goes
# red for the wrong reason and gets turned off.


def _ok(code: str, name: str, reason: str) -> Result:
    return Result(code=code, name=name, status="pass", reason=reason)


def _na(code: str, name: str, reason: str) -> Result:
    return Result(code=code, name=name, status="n/a", reason=reason)


def _bad(code: str, name: str, reason: str, vios: list[Violation]) -> Result:
    return Result(code=code, name=name, status="fail" if vios else "pass", reason=reason,
                  violations=tuple(vios))


#: the share of the PDF's text layer that must be in a node or in a dropped record. T1 asks for
#: 0.999; this is the invariant's own bar and the two are deliberately the same number, so that a
#: paper which passes I1 is a paper which meets the target rather than one which nearly does.
I1_FLOOR = 0.999


def i1_conservation(c: Context) -> Result:
    """Every word of the PDF's own text layer is in a node or in a dropped record that says why."""
    code, name = "I1", "conservation"
    if not c.pdf_path:
        return _na(code, name, "no PDF beside the tree: there is no text layer to conserve")
    acc = c.accounting
    if acc.get("error"):
        return _na(code, name, acc["error"])
    if not acc.get("layer_words"):
        return _na(code, name, "the PDF has no text layer")
    got, total = acc["accounted"], acc["layer_words"]
    lost = acc["unaccounted"]
    if got >= I1_FLOOR:
        return _ok(code, name, f"{got:.5f} of {total} words accounted")
    return _bad(code, name, f"{got:.5f} of {total} words accounted, {lost} unaccounted", [
        Violation(code=code, severity="error" if got < 0.98 else "warn",
                  message=f"{lost} of {total} text-layer words are in no node and in no dropped record",
                  detail=dict(acc))])


def i2_no_text_twice(c: Context) -> Result:
    """No prose said twice: a block the layout model gave the reader more than once."""
    code, name = "I2", "no-text-twice"
    if len(c.prose) < 2:
        return _na(code, name, "fewer than two prose paragraphs")
    seen: set[str] = set()
    vios: list[Violation] = []
    for u in c.prose:
        ws = words(u.text)
        grams = [" ".join(ws[i: i + K_DUP]) for i in range(len(ws) - K_DUP + 1)]
        if len(grams) >= 4:
            dup = sum(1 for g in grams if g in seen)
            share = dup / len(grams)
            if share >= 0.5:
                vios.append(Violation(
                    code=code, severity="error" if share >= 0.9 else "warn", node_id=u.node_id,
                    page=u.page, text=_trim(u.text),
                    message=f"{share:.0%} of this paragraph has already been read elsewhere",
                    detail={"repeated_share": round(share, 3), "words": len(ws)}))
        seen.update(grams)
    return _bad(code, name, f"{len(vios)} of {len(c.prose)} paragraphs repeat earlier text", vios)


def i3_heading_numbering(c: Context) -> Result:
    """Numbered headings run on: no gap, no going backwards, no orphan subsection.

    A gap names the number that is missing, which is the whole point of the check — "3 is missing
    between 2 and 4, look on the page 2 ends on" is a place to send a repair, and "the numbering
    is odd" is not. The commonest cause is a top-level heading the layout model fused with the
    paragraph under it, which is also the commonest cause of a whole lane landing in the wrong
    place (`confidence.py`).
    """
    code, name = "I3", "heading-numbering"
    numbered: list[tuple[Node, tuple[int, ...]]] = []
    for n in c.titled:
        m = _HEADING_NUMBER.match(n.heading or "")
        if m:
            numbered.append((n, tuple(int(p) for p in m.group(1).split("."))))
    if len(numbered) < 3:
        return _na(code, name, f"{len(numbered)} numbered headings: too few to say anything")
    vios: list[Violation] = []
    last_child: dict[tuple[int, ...], int] = {}
    seen: set[tuple[int, ...]] = set()
    for node, num in numbered:
        parent, child = num[:-1], num[-1]
        if parent and parent not in seen:
            vios.append(Violation(
                code=code, severity="warn", node_id=node.node_id, page=node.page,
                text=_trim(node.heading or ""),
                message=f"{'.'.join(map(str, num))} has no heading {'.'.join(map(str, parent))} above it",
                detail={"missing": ".".join(map(str, parent)), "kind": "orphan"}))
        prev = last_child.get(parent)
        if prev is None:
            if child > 1:
                vios.append(Violation(
                    code=code, severity="warn", node_id=node.node_id, page=node.page,
                    text=_trim(node.heading or ""),
                    message=f"numbering starts at {'.'.join(map(str, num))}, not 1",
                    detail={"kind": "gap", "missing": [".".join(map(str, parent + (i,))) for i in range(1, child)]}))
        elif child > prev + 1:
            missing = [".".join(map(str, parent + (i,))) for i in range(prev + 1, child)]
            vios.append(Violation(
                code=code, severity="error", node_id=node.node_id, page=node.page,
                text=_trim(node.heading or ""),
                message=f"{', '.join(missing)} missing before {'.'.join(map(str, num))}",
                detail={"kind": "gap", "missing": missing, "after": ".".join(map(str, parent + (prev,)))}))
        elif child <= prev:
            vios.append(Violation(
                code=code, severity="warn", node_id=node.node_id, page=node.page,
                text=_trim(node.heading or ""),
                message=f"{'.'.join(map(str, num))} comes after {'.'.join(map(str, parent + (prev,)))}",
                detail={"kind": "regression"}))
        last_child[parent] = max(child, prev or 0)
        seen.add(num)
    return _bad(code, name, f"{len(numbered)} numbered headings, {len(vios)} breaks", vios)


def i4_reference_list(c: Context) -> Result:
    """The reference list enumerates, its entries look like references, and it is long enough
    for the highest number the text cites."""
    code, name = "I4", "reference-list"
    entries = c.entries
    if len(entries) < 5:
        return _na(code, name, f"{len(entries)} reference entries: too few to check")
    vios: list[Violation] = []

    referencey = [n for n in entries if _YEAR.search(n.text or "") or _DOI.search(n.text or "")]
    share = len(referencey) / len(entries)
    if share < 0.7:
        vios.append(Violation(
            code=code, severity="error", message=f"only {share:.0%} of the reference entries carry a year or a DOI",
            detail={"kind": "not-references", "entries": len(entries), "referencey": len(referencey)}))

    printed: list[tuple[Node, int]] = []
    for n in entries:
        m = _ENTRY_NUMBER.match(n.text or "")
        if m:
            printed.append((n, int(m.group(1))))
    if len(printed) >= 0.8 * len(entries):
        nums = [p for _, p in printed]
        for (node, num), prev in zip(printed[1:], nums):
            if num != prev + 1:
                vios.append(Violation(
                    code=code, severity="error" if num > prev + 1 else "warn", node_id=node.node_id,
                    page=node.page, text=_trim(node.text),
                    message=f"entry {num} follows entry {prev}",
                    detail={"kind": "gap" if num > prev + 1 else "regression", "expected": prev + 1}))
        highest = max(nums)
    else:
        surnames = [(n, (n.text or "").strip().lower()) for n in entries]
        out_of_order = sum(1 for (_, a), (_, b) in zip(surnames, surnames[1:]) if b < a)
        if out_of_order > 0.1 * len(entries):
            vios.append(Violation(
                code=code, severity="info",
                message=f"entries neither enumerate nor sort: {out_of_order} of {len(entries)} out of order",
                detail={"kind": "unordered"}))
        highest = len(entries)

    cited = max((x.ref_no for x in c.cites), default=0)
    if cited > highest:
        vios.append(Violation(
            code=code, severity="error",
            message=f"the text cites [{cited}] and the list ends at {highest}",
            detail={"kind": "short-list", "highest_cited": cited, "entries": highest}))
    return _bad(code, name, f"{len(entries)} entries, {len(vios)} faults", vios)


def i5_citations_resolve(c: Context) -> Result:
    """Every numeric citation marker names an entry the list actually has."""
    code, name = "I5", "citations-resolve"
    entries = len(c.entries)
    if entries < 5:
        return _na(code, name, f"{entries} reference entries: nothing to resolve against")
    highest = max((r.ref_no for r in c.refs), default=entries)
    vios: list[Violation] = []
    markers = 0
    for n in c.nodes:
        if n.type not in ("paragraph", "list_item", "caption", "footnote") or n.role == "references":
            continue
        over: list[int] = []
        for m in _NUMERIC.finditer(n.text or ""):
            for num in _expand_numeric(m.group(1)):
                markers += 1
                if num > highest:
                    over.append(num)
        if over:
            vios.append(Violation(
                code=code, severity="warn", node_id=n.node_id, page=n.page, text=_trim(n.text),
                message=f"cites {', '.join(f'[{x}]' for x in sorted(set(over))[:6])}; the list ends at {highest}",
                detail={"kind": "dangling", "over": sorted(set(over)), "entries": highest}))
    if not markers:
        return _na(code, name, "no numeric citation markers: the paper cites by author and year")
    return _bad(code, name, f"{markers} markers, {len(vios)} paragraphs cite past the list", vios)


def i6_figures_and_captions(c: Context) -> Result:
    """Every "Fig. k" resolves to caption k, captions enumerate, and each hangs on a figure."""
    code, name = "I6", "figures-and-captions"
    captions = [n for n in c.nodes if n.type == "caption"]
    have = figures(c.tree)
    if not captions and not have:
        return _na(code, name, "no captions and no figures")
    vios: list[Violation] = []

    for cap in captions:
        parent = next((n for n in c.nodes if n.node_id == cap.parent), None)
        if parent is None or parent.type not in ("picture", "table"):
            vios.append(Violation(
                code=code, severity="warn", node_id=cap.node_id, page=cap.page, text=_trim(cap.text),
                message="a caption that hangs on no figure or table",
                detail={"kind": "orphan-caption", "parent": parent.type if parent else None}))

    numbers: dict[str, set[int]] = defaultdict(set)
    for label in have:
        head, _, num = label.partition(" ")
        if num.isdigit():
            numbers[head].add(int(num))
    for head, nums in sorted(numbers.items()):
        missing = sorted(set(range(1, max(nums) + 1)) - nums)
        if missing:
            vios.append(Violation(
                code=code, severity="warn",
                message=f"{head} {', '.join(map(str, missing))} never captioned; the paper reaches {head} {max(nums)}",
                detail={"kind": "caption-gap", "missing": missing, "what": head}))

    # supplementary numbers name another document and are not this paper's to resolve
    for n in c.nodes:
        if n.type not in ("paragraph", "list_item") or n.role not in _PROSE_LANES:
            continue
        unresolved = {f"{'table' if m.group(1).lower().startswith('tab') else 'figure'} {m.group(2)}"
                      for m in _FIGURE_CALL.finditer(n.text or "")} - set(have)
        if unresolved and have:  # a paper whose figures the reader found none of is I11's fault, not this one
            vios.append(Violation(
                code=code, severity="info", node_id=n.node_id, page=n.page, text=_trim(n.text),
                message=f"calls {', '.join(sorted(unresolved)[:4])}, which no caption names",
                detail={"kind": "dangling-call", "unresolved": sorted(unresolved)}))
    return _bad(code, name, f"{len(captions)} captions, {len(have)} numbered figures, {len(vios)} faults", vios)


#: a lane the type's contract requires, holding less than this share of the paper's prose, is
#: present in name only — a heading found and its body filed somewhere else
THIN_LANE = 0.02
FAT_LANE = 0.80  # one lane holding this much of the prose is a heading the reader never found


def i7_type_contract(c: Context) -> Result:
    """A paper of a type has the lanes its type requires, each holding a plausible share."""
    code, name = "I7", "type-contract"
    expected = EXPECTED.get(c.paper_type or "", ())
    if not expected:
        return _na(code, name, f"no contract for a paper of type {c.paper_type!r}")
    by_lane: Counter[str] = Counter()
    for u in c.prose:
        by_lane[u.role] += sum(u.tokens.values())
    if "results-discussion" in by_lane:  # a combined section stands for both of its lanes
        for lane in ("results", "discussion"):
            by_lane[lane] += by_lane["results-discussion"]
    total = max(sum(by_lane.values()), 1)
    if total < 200:
        return _na(code, name, f"{total} words of prose: too short to have a shape")
    vios: list[Violation] = []
    for lane in expected:
        share = by_lane.get(lane, 0) / total
        if not by_lane.get(lane):
            vios.append(Violation(
                code=code, severity="error",
                message=f"a {c.paper_type} paper with no {lane}",
                detail={"kind": "missing-lane", "lane": lane}))
        elif share < THIN_LANE:
            vios.append(Violation(
                code=code, severity="warn",
                message=f"{lane} holds {share:.1%} of the prose: present in name only",
                detail={"kind": "thin-lane", "lane": lane, "share": round(share, 4)}))
    biggest, n_big = by_lane.most_common(1)[0] if by_lane else ("", 0)
    if n_big / total > FAT_LANE:
        vios.append(Violation(
            code=code, severity="warn",
            message=f"{biggest} holds {n_big / total:.0%} of the prose: a heading the reader did not find",
            detail={"kind": "fat-lane", "lane": biggest, "share": round(n_big / total, 4)}))
    return _bad(code, name, f"a {c.paper_type} paper, {len(vios)} faults against its contract", vios)


#: the orders a paper's lanes may run in. Methods-last is a house style, not an error — Nature's
#: research papers print it after the discussion — so it is an allowed order rather than a
#: violation to be forgiven. `back` and `references` are interchangeable at the end because
#: acknowledgements sit on either side of the list depending on the journal.
ORDERS: tuple[tuple[str, ...], ...] = (
    ("abstract", "introduction", "methods", "results", "discussion"),
    ("abstract", "introduction", "methods", "results-discussion"),
    ("abstract", "introduction", "results", "discussion", "methods"),
    ("abstract", "introduction", "results-discussion", "methods"),
)
#: `pairs.LANES` is the lanes a *witness paragraph* can be in, and it stops at `other`: the
#: reference list and the back matter are not prose and it never scores them. A lane order runs
#: over the sections a paper prints, which do include them, so I8 has its own set. Getting this
#: wrong cost a check that silently could not fail — the tail rule below was unreachable, and
#: the test that caught it was the one asserting a stranded discussion *is* found.
_TAIL = ("back", "references")
_ORDERED_LANES = frozenset(LANES) - {"other"} | set(_TAIL)


def _is_subsequence(seq: Iterable[str], order: tuple[str, ...]) -> bool:
    it = iter(order)
    return all(any(step == lane for step in it) for lane in seq)


def i8_lane_order(c: Context) -> Result:
    """The lanes run in an order some journal actually prints."""
    code, name = "I8", "lane-order"
    seq: list[tuple[Node, str]] = []
    for n in c.sections:
        if n.depth == 1 and n.role in _ORDERED_LANES:
            if not seq or seq[-1][1] != n.role:
                seq.append((n, n.role))
    lanes = [r for _, r in seq]
    head = [l for l in lanes if l not in _TAIL]
    if len(head) < 3:
        return _na(code, name, f"{len(head)} top-level lanes: any order is an order")
    if any(_is_subsequence(head, order) for order in ORDERS):
        # the tail must still come last, wherever it came from
        first_tail = next((i for i, l in enumerate(lanes) if l in _TAIL), len(lanes))
        after = [(n, l) for (n, l), i in zip(seq, range(len(seq))) if i > first_tail and l not in _TAIL]
        if not after:
            return _ok(code, name, " → ".join(lanes))
        node, lane = after[0]
        return _bad(code, name, " → ".join(lanes), [Violation(
            code=code, severity="warn", node_id=node.node_id, page=node.page,
            text=_trim(node.heading or ""),
            message=f"{lane} comes after the references",
            detail={"kind": "after-the-tail", "lane": lane, "order": lanes})])
    # name the first lane that no allowed order can reach from what came before it
    best = max(ORDERS, key=lambda o: sum(1 for i in range(1, len(head) + 1) if _is_subsequence(head[:i], o)))
    cut = next(i for i in range(1, len(head) + 1) if not _is_subsequence(head[:i], best))
    node = next(n for n, l in seq if l == head[cut])
    return _bad(code, name, " → ".join(lanes), [Violation(
        code=code, severity="error", node_id=node.node_id, page=node.page,
        text=_trim(node.heading or ""),
        message=f"{head[cut]} comes after {head[cut - 1]}, which no journal prints",
        detail={"kind": "out-of-order", "lane": head[cut], "after": head[cut - 1], "order": lanes})])


def i9_paragraphs_well_formed(c: Context) -> Result:
    """A paragraph opens and closes like one, or there is somewhere it could join."""
    code, name = "I9", "paragraph-integrity"
    if not c.prose:
        return _na(code, name, "no prose paragraphs")
    order = {u.node_id: i for i, u in enumerate(c.prose)}
    vios: list[Violation] = []
    long = [u for u in c.prose if u.kind == "paragraph" and sum(u.tokens.values()) >= MIN_PARAGRAPH]
    for u in long:
        i = order[u.node_id]
        if not _TERMINAL.search((u.text or "").rstrip()):
            nxt = c.prose[i + 1] if i + 1 < len(c.prose) else None
            vios.append(Violation(
                code=code, severity="warn", node_id=u.node_id, page=u.page, text=_trim(u.text[-110:]),
                message="ends mid-sentence",
                detail={"kind": "unterminated", "join": nxt.node_id if nxt else None,
                        "join_lane": nxt.role if nxt else None,
                        "join_page": nxt.page if nxt else None}))
        if (u.text or "").lstrip()[:1].islower():
            prv = c.prose[i - 1] if i else None
            vios.append(Violation(
                code=code, severity="warn", node_id=u.node_id, page=u.page, text=_trim(u.text[:110]),
                message="starts in lower case",
                detail={"kind": "lowercase-start", "join": prv.node_id if prv else None,
                        "join_lane": prv.role if prv else None,
                        "join_page": prv.page if prv else None}))
    for u in c.prose:
        if u.kind == "paragraph" and sum(u.tokens.values()) < 6:
            vios.append(Violation(
                code=code, severity="info", node_id=u.node_id, page=u.page, text=_trim(u.text),
                message="a paragraph of fewer than six words",
                detail={"kind": "fragment"}))
    return _bad(code, name, f"{len(long)} full paragraphs, {len(vios)} faults", vios)


def i10_no_furniture_in_the_body(c: Context) -> Result:
    """No line that recurs across pages, and nothing that is only the paper's own metadata,
    sits in the body as prose."""
    code, name = "I10", "furniture-in-the-body"
    if len(c.tree.pages) < 3:
        return _na(code, name, f"{len(c.tree.pages)} pages: a line cannot recur across three of them")
    by_text: dict[str, list[Unit]] = defaultdict(list)
    for u in c.prose:
        key = re.sub(r"\d+", "#", re.sub(r"\W+", " ", (u.text or "").lower())).strip()
        if 3 <= len(key.split()) <= 25:
            by_text[key].append(u)
    vios: list[Violation] = []
    for key, us in by_text.items():
        pages = {u.page for u in us if u.page}
        if len(pages) >= 3:
            for u in us:
                vios.append(Violation(
                    code=code, severity="error", node_id=u.node_id, page=u.page, text=_trim(u.text),
                    message=f"this line is read as prose on {len(pages)} pages: it is a running head",
                    detail={"kind": "recurring", "pages": sorted(p for p in pages if p)}))

    marks = {re.sub(r"\W+", " ", str(v).lower()).strip()
             for v in (c.record.get("journal"), c.record.get("title"), c.tree.title) if v}
    marks = {m for m in marks if len(m.split()) >= 3}
    for u in c.prose:
        flat = re.sub(r"\W+", " ", (u.text or "").lower()).strip()
        if flat and flat in marks:
            vios.append(Violation(
                code=code, severity="warn", node_id=u.node_id, page=u.page, text=_trim(u.text),
                message="the paper's own title or journal, read as body prose",
                detail={"kind": "metadata"}))
    return _bad(code, name, f"{len(c.prose)} prose paragraphs, {len(vios)} of them furniture", vios)


#: a page whose text layer has words and whose nodes hold less than this share of them has had a
#: block dropped; `harness.page_coverage` already skips pages under forty words
PAGE_FLOOR = 0.90


def i11_geometry(c: Context) -> Result:
    """Reading order runs down each column, and every page with words has nodes."""
    code, name = "I11", "geometry"
    vios: list[Violation] = []
    if c.pdf_path:
        from .harness import page_coverage

        for page, share in page_coverage(c.tree, c.pdf_path):
            if share < PAGE_FLOOR:
                vios.append(Violation(
                    code=code, severity="error" if share < 0.5 else "warn", page=page,
                    message=f"page {page}: {share:.0%} of its words reached a node",
                    detail={"kind": "page-loss", "page": page, "coverage": share}))

    placed = [n for n in c.nodes if n.bbox and n.page and n.type in ("paragraph", "list_item", "section")]
    by_page: dict[int, list[Node]] = defaultdict(list)
    for n in placed:
        by_page[n.page].append(n)
    inversions = 0
    for page, ns in sorted(by_page.items()):
        width = next((p.width for p in c.tree.pages if p.page_no == page), 0.0)
        if not width or len(ns) < 4:
            continue
        # a block wider than three fifths of the page spans the columns and orders neither
        narrow = [n for n in ns if (n.bbox[2] - n.bbox[0]) < 0.6 * width]
        left = [n for n in narrow if (n.bbox[0] + n.bbox[2]) / 2 < width / 2]
        right = [n for n in narrow if (n.bbox[0] + n.bbox[2]) / 2 >= width / 2]
        for column, label in ((left, "left"), (right, "right")):
            column = sorted(column, key=lambda n: n.ordinal)
            for a, b in zip(column, column[1:]):
                if b.bbox[1] < a.bbox[1] - 2.0:  # bbox is [l, t, r, b] from the top: t grows downward
                    inversions += 1
                    vios.append(Violation(
                        code=code, severity="warn", node_id=b.node_id, page=page, text=_trim(b.text or b.heading or ""),
                        message=f"read after a block {a.bbox[1] - b.bbox[1]:.0f}pt below it in the {label} column",
                        detail={"kind": "inversion", "after": a.node_id, "column": label}))
    return _bad(code, name, f"{len(by_page)} placed pages, {inversions} inversions", vios)


MIN_CLASS = 4  # a style class of three is a coincidence, not a convention


def i12_style_classes(c: Context) -> Result:
    """Text set the same way is the same kind of thing — and the exceptions are listed.

    The reader's own principle in another form: a convention belongs to the document, so a font
    at a size used nine times for a heading and once for a paragraph is one misread block, not a
    tenth convention. This is the only check that reads the PDF's typography rather than the
    tree, and it is `n/a` without the file.
    """
    code, name = "I12", "style-classes"
    if not c.pdf_path:
        return _na(code, name, "no PDF beside the tree: the tree does not record how text was set")
    try:
        from .typography import dominant, styled_rows

        rows_by_page = styled_rows(c.pdf_path)
    except Exception as e:  # noqa: BLE001 — a PDF whose fonts pdfium cannot read has no style classes
        return _na(code, name, f"{type(e).__name__}: {e}")
    if not rows_by_page:
        return _na(code, name, "no styled rows: the PDF has no text layer")

    placed: dict[int, list[Node]] = defaultdict(list)
    for n in c.nodes:
        if n.bbox and n.page and n.type in ("paragraph", "list_item", "section", "caption", "footnote"):
            placed[n.page].append(n)
    members: dict[tuple[str, int, float], Counter[str]] = defaultdict(Counter)
    example: dict[tuple[tuple[str, int, float], str], Node] = {}
    for page, rows in rows_by_page.items():
        height = c.page_height.get(page)
        if not height:
            continue
        for row in rows:
            if row.letters < 12 or not row.spans:
                continue
            top = height - row.t  # styled rows are bottom-origin; a node's box is not
            owner = next((n for n in placed.get(page, ())
                          if n.bbox[0] - 2 <= row.cx <= n.bbox[2] + 2 and n.bbox[1] - 2 <= top <= n.bbox[3] + 2), None)
            if owner is None:
                continue
            font, weight, size = dominant(row)
            cls = (font, weight, round(size * 2) / 2)
            members[cls][owner.type] += 1
            example.setdefault((cls, owner.type), owner)
    vios: list[Violation] = []
    for cls, counts in sorted(members.items(), key=lambda kv: -sum(kv[1].values())):
        n = sum(counts.values())
        if n < MIN_CLASS or len(counts) < 2:
            continue
        kind, top = counts.most_common(1)[0]
        if top / n < 0.8:
            continue  # a class genuinely shared between two kinds is not an exception, it is the body
        for other, k in counts.items():
            if other == kind:
                continue
            node = example.get((cls, other))
            vios.append(Violation(
                code=code, severity="info", node_id=node.node_id if node else "",
                page=node.page if node else None, text=_trim(node.text or node.heading or "" if node else ""),
                message=f"set like {top} {kind}s, but read as a {other}",
                detail={"kind": "odd-one-out", "style": [cls[0], cls[1], cls[2]],
                        "majority": kind, "majority_n": top, "minority": other, "minority_n": k}))
    return _bad(code, name, f"{len(members)} style classes, {len(vios)} exceptions", vios)


def i13_headings_are_headings(c: Context) -> Result:
    """A heading is short, is followed by something, and does not end like a sentence."""
    code, name = "I13", "headings-are-headings"
    if not c.titled:
        return _na(code, name, "the paper printed no headings the reader kept")
    content = {"paragraph", "list_item", "table", "picture", "formula", "code", "footnote", "caption"}
    vios: list[Violation] = []
    for n in c.titled:
        head = (n.heading or "").strip()
        if len(head.split()) > MAX_HEADING_WORDS:
            vios.append(Violation(
                code=code, severity="warn", node_id=n.node_id, page=n.page, text=_trim(head),
                message=f"a heading of {len(head.split())} words",
                detail={"kind": "long", "words": len(head.split())}))
        if split_fused_heading(head) is not None:
            vios.append(Violation(
                code=code, severity="error", node_id=n.node_id, page=n.page, text=_trim(head),
                message="a heading with its first sentence fused onto it",
                detail={"kind": "fused"}))
        elif head.endswith((".", ",", ";")) and len(head.split()) > 4:
            vios.append(Violation(
                code=code, severity="warn", node_id=n.node_id, page=n.page, text=_trim(head),
                message="a heading that ends like a sentence",
                detail={"kind": "sentence"}))
        if _BULLET.match(head):
            vios.append(Violation(
                code=code, severity="warn", node_id=n.node_id, page=n.page, text=_trim(head),
                message="a bullet read as a heading",
                detail={"kind": "bullet"}))
        if not any(d.type in content for d in _descend(n)):
            vios.append(Violation(
                code=code, severity="warn", node_id=n.node_id, page=n.page, text=_trim(head),
                message="a heading with nothing under it",
                detail={"kind": "empty"}))
    return _bad(code, name, f"{len(c.titled)} headings the paper printed, {len(vios)} faults", vios)


def _descend(node: Node) -> Iterable[Node]:
    for child in node.children:
        yield child
        yield from _descend(child)


# -- the register ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Invariant:
    code: str
    name: str
    scope: str  # document | node — how it is priced: a document-scope failure is a fact about the paper
    stance: str  # advisory | hard — only `hard` may drive a repair, and only DEV may promote one
    fn: Callable[[Context], Result]


#: Every check starts advisory. `campaign/reports/phase4.md` prices them on DEV against the
#: witness and a commit promotes the ones that earn it, naming the number. Nothing is promoted
#: here by argument.
REGISTER: tuple[Invariant, ...] = (
    Invariant("I1", "conservation", "document", "advisory", i1_conservation),
    Invariant("I2", "no-text-twice", "node", "advisory", i2_no_text_twice),
    Invariant("I3", "heading-numbering", "node", "advisory", i3_heading_numbering),
    Invariant("I4", "reference-list", "document", "advisory", i4_reference_list),
    Invariant("I5", "citations-resolve", "node", "advisory", i5_citations_resolve),
    Invariant("I6", "figures-and-captions", "node", "advisory", i6_figures_and_captions),
    Invariant("I7", "type-contract", "document", "advisory", i7_type_contract),
    Invariant("I8", "lane-order", "node", "advisory", i8_lane_order),
    Invariant("I9", "paragraph-integrity", "node", "advisory", i9_paragraphs_well_formed),
    Invariant("I10", "furniture-in-the-body", "node", "advisory", i10_no_furniture_in_the_body),
    Invariant("I11", "geometry", "node", "advisory", i11_geometry),
    Invariant("I12", "style-classes", "node", "advisory", i12_style_classes),
    Invariant("I13", "headings-are-headings", "node", "advisory", i13_headings_are_headings),
)

BY_CODE = {inv.code: inv for inv in REGISTER}


def check(tree: Tree, *, pdf_path: Path | None = None, kind: dict[str, Any] | None = None,
          record: dict[str, Any] | None = None, only: Iterable[str] | None = None) -> list[Result]:
    """Every invariant against one reading, in register order.

    A check that raises is reported as `n/a` with the exception, never swallowed and never
    allowed to end the run: thirteen checks over a corpus will meet a paper that breaks one of
    them, and losing the other twelve to it would be the silent failure this campaign spent a
    phase removing.
    """
    ctx = Context(tree, pdf_path=pdf_path, kind=kind, record=record)
    wanted = set(only) if only else None
    out: list[Result] = []
    for inv in REGISTER:
        if wanted and inv.code not in wanted:
            continue
        try:
            out.append(inv.fn(ctx))
        except Exception as e:  # noqa: BLE001 — see the docstring
            out.append(_na(inv.code, inv.name, f"raised {type(e).__name__}: {e}"))
    return out


def summarize(results: Iterable[Result]) -> dict[str, Any]:
    results = list(results)
    vios = [v for r in results for v in r.violations]
    return {
        "checked": sum(1 for r in results if r.status != "n/a"),
        "failed": sorted(r.code for r in results if r.failed),
        "not_applicable": sorted(r.code for r in results if r.status == "n/a"),
        "violations": len(vios),
        "by_severity": dict(Counter(v.severity for v in vios)),
        "by_code": dict(Counter(v.code for v in vios)),
    }


def report(key: str, results: list[Result], *, min_severity: str = "info") -> str:
    order = {"error": 0, "warn": 1, "info": 2}
    floor = order[min_severity]
    lines = [f"# {key}"]
    for r in results:
        mark = {"pass": "ok ", "fail": "FAIL", "n/a": "-  "}[r.status]
        lines.append(f"{mark} {r.code} {r.name}: {r.reason}")
        for v in r.violations:
            if order[v.severity] <= floor:
                where = f" p{v.page}" if v.page else ""
                lines.append(f"       {v.severity:5} {v.node_id or '(document)'}{where}  {v.message}")
                if v.text:
                    lines.append(f"             {v.text}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("docs", nargs="*", help="saved Docling documents (*.docling.json)")
    ap.add_argument("--lib", help="a library root: every paper in it")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", help="comma-separated codes, e.g. I3,I9")
    ap.add_argument("--severity", default="info", choices=("error", "warn", "info"))
    args = ap.parse_args(argv)
    only = tuple(args.only.split(",")) if args.only else None
    if hasattr(sys.stdout, "reconfigure"):  # a violation quotes the paper, and papers are not cp1252
        sys.stdout.reconfigure(encoding="utf-8")

    papers: list[tuple[str, Tree, Path | None, dict[str, Any] | None]] = []
    if args.lib:
        from .harness import read_paper
        from .library import parsed_papers

        lib = Path(args.lib).expanduser()
        for row in parsed_papers(lib):
            try:
                tree, kind, _ = read_paper(lib, row)
            except Exception as e:  # noqa: BLE001 — name the paper and go on
                print(f"# {row['key']}: unreadable, {type(e).__name__}: {e}", file=sys.stderr)
                continue
            src = row.get("source")
            pdf = Path(src) if src and str(src).lower().endswith(".pdf") and Path(src).exists() else None
            papers.append((row["key"], tree, pdf, kind))
    for pattern in args.docs:
        for path in sorted(glob.glob(pattern)):
            p = Path(path)
            papers.append((p.name, build_tree(json.loads(p.read_text("utf-8")), p.stem), None, None))

    out: list[dict[str, Any]] = []
    for key, tree, pdf, kind in papers:
        results = check(tree, pdf_path=pdf, kind=kind, only=only)
        if args.json:
            out.append({"key": key, "summary": summarize(results),
                        "results": [r.to_dict() for r in results]})
        else:
            print(report(key, results, min_severity=args.severity))
    if args.json:
        print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
