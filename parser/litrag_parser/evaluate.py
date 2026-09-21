"""What a reading is worth, measured over publishers rather than over papers.

`pairs.py` says how far one PDF's reading lies from its XML twin. This says what a *corpus* of
such comparisons means, and it differs in three ways that decide whether the number is worth
anything.

**The unit of held-out-ness is the publisher, not the paper.** The reader's rules key on layout
conventions, and a convention belongs to a publisher: measured on the fourth held-out set, a
paper from a publisher the rules were already written on reads 0.966 faithful and one from a
publisher never seen reads 0.906 (NOTES.md, 2026-09-20). A corpus drawn by topic lands on the
same dozen publishers, so its aggregate measures transfer to new *papers* and is read as transfer
to new *layouts*. Everything here is therefore reported by familiarity, and every interval is
bootstrapped over publishers rather than over papers — resampling papers within one publisher
would call a dozen readings of one layout a dozen independent observations.

**What is asserted is separated from what is right.** A lane the reader declines to name is not
an error, it is a silence, and the two have to be counted apart or abstaining looks like
accuracy. So: *coverage* is the share of the witness's paragraphs that get a named lane at all,
and *precision* is, among those, the share that match. A reader can have either at the other's
expense, and only the pair of them says anything.

**Ingestion is measured without a witness.** `faithful` needs an XML twin and most papers have
none. Conservation does not: every word of the PDF's own text layer should be inside a node or
inside a dropped record that says why it was left out. That is the one measure that can run on
Karim's own libraries, which have no twins.

Nothing here asks a model, and nothing here writes to a library.
"""

from __future__ import annotations

import random
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .pairs import _SOFT, LANES, Unit, _held, _index, _land, _top, units_of
from .tree import Tree

#: a lane the reader names. `other` is a silence — the reader declining to say — and every
#: measure here keeps it apart from a lane it got wrong.
NAMED = tuple(lane for lane in LANES if lane != "other")


# ---- T1: conservation of the text layer, no witness needed ---------------------------------


#: T1's own tokens. `pairs.words` is `[a-z]{2,}` after folding, which is right for *locating* a
#: paragraph — digits and single letters differ between a PDF and its XML and would only add
#: noise. It is wrong for *conserving* one: under it a reading that dropped every number in
#: every table, every axis label, every p-value and every reference year would still score 1.0.
#: A paper is mostly numbers in the places most likely to be lost, so T1 counts them.
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def layer_words(text: str) -> list[str]:
    """Every alphanumeric run, folded — digits, single letters and non-Latin scripts included."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower().translate(_SOFT)
    return _TOKEN.findall(t)


def _layer_counts(lines: dict[int, list[Any]], known: Counter) -> Counter:
    """The text layer's words, with words the typesetter broke at a line end mended.

    pdfium marks such a break, but `recover.clean` strips the mark before a `Line` exists, so by
    the time the layer is read back "cell cul" and "tures modify" are four tokens where the page
    has three words and the tree — which takes Docling's own de-hyphenated text — has three.
    Measured over forty papers, this artefact alone was **63.6 per cent** of everything T1 called
    unaccounted for. It is typesetting, not lost text, and counting it as loss would have sent
    the whole of T1's budget after a defect that is not there.

    The mend is bounded so it cannot flatter the reader: a line's last token and the next line's
    first are joined **only** when the joined word is one the reading actually holds and neither
    fragment is. A reading that lost the word has neither, nothing is mended, and both fragments
    count against it exactly as before.
    """
    out: Counter = Counter()
    for page in sorted(lines):
        rows = [layer_words(getattr(ln, "text", "") or "") for ln in lines[page]]
        rows = [r for r in rows if r]
        for i, ws in enumerate(rows):
            nxt = rows[i + 1] if i + 1 < len(rows) else None
            if nxt and ws and nxt[0]:
                joined = ws[-1] + nxt[0]
                if known[joined] > out[joined] and not (known[ws[-1]] and known[nxt[0]]):
                    out[joined] += 1
                    out.update(ws[:-1])
                    rows[i + 1] = nxt[1:]
                    continue
            out.update(ws)
    return out


def accounting(tree: Tree, pdf_path: Path) -> dict[str, Any]:
    """Of every word in the PDF's own text layer, what share is in a node or in a dropped record.

    Counted as a multiset, so a word the layer has three times and the tree has twice is one word
    short rather than "present". Headings, captions and a table's cells all count as held: the
    question is whether the text is in the store at all, not whether it is in the right lane —
    which is `precision_and_coverage`'s question and needs a witness.
    """
    from .recover import pdf_lines

    try:
        lines = pdf_lines(pdf_path)
    except Exception as e:  # noqa: BLE001 — a PDF pdfium cannot read has no text layer to conserve
        return {"layer_words": 0, "error": f"{type(e).__name__}: {e}"}

    held: Counter[str] = Counter()
    for n in tree.walk():
        held.update(layer_words(n.text or ""))
        held.update(layer_words(n.heading or ""))
        if n.table:
            for row in n.table.get("cells", []):
                for cell in row:
                    held.update(layer_words(str(cell)))
    held.update(layer_words(tree.title or ""))

    dropped: Counter[str] = Counter()
    for item in tree.dropped_items:
        dropped.update(layer_words(item.get("text") or ""))

    layer = _layer_counts(lines, held + dropped)
    total = sum(layer.values())
    in_node = sum(min(c, held[w]) for w, c in layer.items())
    # only the words a node did not already hold count as dropped, so the two never double-count
    spare = Counter({w: c - min(c, held[w]) for w, c in layer.items()})
    in_dropped = sum(min(c, dropped[w]) for w, c in spare.items() if c)
    return {
        "layer_words": total,
        "in_a_node": in_node,
        "in_a_dropped_record": in_dropped,
        "unaccounted": total - in_node - in_dropped,
        "accounted": round((in_node + in_dropped) / total, 5) if total else None,
        "in_a_node_share": round(in_node / total, 5) if total else None,
    }


# ---- T2/T3: what the reader asserts, and whether it is right -------------------------------


@dataclass
class Landing:
    """One paragraph of the witness, and what the PDF's reading did with it."""

    paper: str
    prefix: str  # the publisher: what the bootstrap clusters on
    split: str
    familiar: bool  # a publisher the rules were already written on
    paper_type: str
    words: int
    xml_lane: str
    pdf_lane: str | None  # None: no block of the reading holds it
    asserted: bool  # the reader named a lane for it
    correct: bool  # ... and that lane is the witness's
    #: the witness paragraph this is about. Carried so an audit can find the text again and show
    #: it — a disagreement without its quote and its page is a number nobody can check.
    xml_node: str = ""
    #: the block of the *reading* it mostly landed in, when it landed anywhere. `xml_node` says
    #: which paragraph of the witness this row is about; this says which block of the PDF is
    #: answerable for it, which is what an invariant firing at a node has to be joined against.
    pdf_node: str = ""
    #: where it went when no lane was asserted — "front matter", "caption", "heading", "table",
    #: "nowhere", or "silent" for prose the reader kept but declined to lane. Without this the
    #: largest error class the reader has would be invisible: body prose swallowed by a
    #: publisher's furniture is *not* a wrong lane, it is a paragraph that never reached one, and
    #: counted as coverage alone it looks like honest abstention.
    where: str = ""

    @property
    def wrong(self) -> bool:
        return self.asserted and not self.correct


def landings(pdf: Tree, xml: Tree, *, paper: str, prefix: str, split: str,
             familiar: bool, paper_type: str = "?") -> list[Landing]:
    """Every prose paragraph of the XML, and the lane the PDF's reading put it under.

    The paragraph is located by the same four-word shingles `pairs.py` uses, and the lane taken
    is the one of the block it mostly landed in — `pairs._top`, which breaks a tie by the
    earliest block rather than by hash order."""
    pu, xu = units_of(pdf), units_of(xml)
    index = _index(pu)
    out: list[Landing] = []
    for u in xu:
        if not (u.prose and u.shingles):
            continue
        landed = _land(u, index)
        held, _ = _held(u, landed, pu) if landed else (0.0, 0.0)
        lane: str | None = None
        where = "nowhere"
        pdf_node = ""
        if landed and held >= 0.5:  # the same bar `pairs.py` uses before it calls a paragraph found
            top = pu[_top(landed)]
            pdf_node = top.node_id
            if top.prose:
                lane, where = top.role, "silent" if top.role == "other" else "a lane"
            elif top.front:
                where = "front matter"
            else:
                where = top.kind  # caption, heading, table, meta, footnote, formula
        asserted = lane in NAMED
        out.append(Landing(
            paper=paper, prefix=prefix, split=split, familiar=familiar, paper_type=paper_type,
            words=sum(u.tokens.values()), xml_lane=u.role, pdf_lane=lane,
            asserted=asserted, correct=bool(asserted and lane == u.role), where=where,
            xml_node=u.node_id, pdf_node=pdf_node,
        ))
    return out


# ---- the witness's opinion of one node, so a located finding can be priced ------------------


@dataclass(frozen=True)
class NodeVerdict:
    """What the witness says about one block of the PDF's reading.

    `landings` turns the comparison around the witness's paragraphs, which is right for asking
    how much of a paper was read correctly and useless for asking whether a finding is a real
    one. An invariant fires at a *node*, so pricing it needs the same comparison indexed the
    other way: this block, and whether the XML twin disagrees with what the reading did to it.

    Three disagreements, and they are not the same fault. `lane_wrong` is prose filed under the
    wrong heading. `merged` is two of the witness's paragraphs inside one of the reading's.
    `holds_a_split` is one of the witness's cut across this block and others. A block can be all
    three, and `wrong` is any of them.
    """

    node_id: str
    kind: str
    role: str
    page: int | None
    paragraphs: int  # the witness's paragraphs that lie mostly in this block
    lane_wrong: int  # ... of which this block's lane is not the witness's
    merged: bool
    holds_a_split: bool

    @property
    def wrong(self) -> bool:
        return bool(self.lane_wrong or self.merged or self.holds_a_split)


#: a witness paragraph with fewer shingles than this says nothing about how it arrived —
#: `pairs.compare` uses the same floor before it calls a paragraph intact, split or merged
MIN_SHINGLES = 8
_SPLIT_SHARE = 0.15  # a block holding less of a cut paragraph than this is a stray landing


def node_verdicts(pdf: Tree, xml: Tree) -> dict[str, NodeVerdict]:
    """Every block of the PDF's reading the witness can speak about, and what it says.

    Blocks the witness is silent about — a caption, a table, prose the XML does not have — are
    absent rather than present and correct. An invariant's precision is over the blocks that
    could have been judged, and counting the unjudgeable as right would flatter every check.
    """
    pu, xu = units_of(pdf), units_of(xml)
    index = _index(pu)
    dominant: dict[int, list[Unit]] = {}
    pieces: dict[int, int] = {}
    for u in xu:
        if not (u.prose and u.shingles) or len(u.shingles) < MIN_SHINGLES:
            continue
        landed = _land(u, index)
        if not landed:
            continue
        held, held_top = _held(u, landed, pu)
        if held < 0.5:
            continue  # the reading does not hold it: a loss, and no one block's fault
        if held_top >= 0.85:
            dominant.setdefault(_top(landed), []).append(u)
        else:
            got = sum(landed.values()) or 1
            for j, c in landed.items():
                if pu[j].prose and c / got >= _SPLIT_SHARE:
                    pieces[j] = pieces.get(j, 0) + 1
    out: dict[str, NodeVerdict] = {}
    for j in sorted(set(dominant) | set(pieces)):
        v = pu[j]
        inside = dominant.get(j, [])
        out[v.node_id] = NodeVerdict(
            node_id=v.node_id, kind=v.kind, role=v.role, page=v.page,
            paragraphs=len(inside),
            lane_wrong=sum(1 for u in inside if v.role in NAMED and v.role != u.role),
            merged=len(inside) >= 2 and v.kind in ("paragraph", "list_item"),
            holds_a_split=bool(pieces.get(j)),
        )
    return out


def _per_paper(rows: Sequence[Landing]) -> dict[str, Any]:
    """How much of a corpus one document is, and the typical paper behind the micro-average.

    A micro-average over paragraphs is a weighted average, and one document can own the weight.
    On DEV a single conference-proceedings supplement — every abstract in the volume its own
    paper to the witness — is **15 per cent of every paragraph in the split**, and it alone moves
    coverage from 0.779 to 0.675. Nothing was wrong with the number; what was wrong was reporting
    it without saying that.

    So: the share the largest document holds, and the median paper, beside the micro-average.
    Neither replaces it. If they disagree, the disagreement is the finding."""
    by_paper: dict[str, list[Landing]] = {}
    for r in rows:
        by_paper.setdefault(r.paper, []).append(r)
    if not by_paper:
        return {}
    sizes = sorted((len(v) for v in by_paper.values()), reverse=True)
    precisions, coverages = [], []
    for group in by_paper.values():
        a = [r for r in group if r.asserted]
        coverages.append(len(a) / len(group))
        if a:
            precisions.append(sum(1 for r in a if r.correct) / len(a))
    mid = lambda xs: round(sorted(xs)[len(xs) // 2], 5) if xs else None  # noqa: E731
    return {
        "largest_paper_share": round(sizes[0] / len(rows), 4),
        "precision_median_paper": mid(precisions),
        "coverage_median_paper": mid(coverages),
    }


def precision_and_coverage(rows: Sequence[Landing]) -> dict[str, Any]:
    """Micro over paragraphs, macro over publishers, and the counts both rest on.

    Reported twice, because a quarter of the witness's own paragraphs are laned `other` and on
    those two readings of "right" are defensible:

    - **strict** (`precision`): an asserted lane on a paragraph the witness lanes `other` is
      wrong. The reader claimed a lane the paper does not have. Note that under this reading it
      is *impossible* to score correct on such a paragraph, since asserting means naming a lane
      and being correct means matching `other`.
    - **on named** (`precision_on_named`): those paragraphs are set aside, and what is left is
      the question "when the paper names a lane, does the reader name the same one?"

    Neither is the truth on its own. `NOTES.md` records the case that makes it: Cureus wraps a
    whole systematic review in one "Review" section which its XML lanes `other`, and the reader
    calls its parts methods — forty-four chunks on one paper, and "a journal's convention, not
    an error". So both are printed, always, and the gap between them is the size of the question.
    """
    n = len(rows)
    asserted = [r for r in rows if r.asserted]
    correct = [r for r in asserted if r.correct]
    named = [r for r in rows if r.xml_lane in NAMED]  # the witness named a lane of its own
    named_asserted = [r for r in named if r.asserted]
    named_correct = [r for r in named_asserted if r.correct]
    by_prefix: dict[str, list[Landing]] = {}
    for r in rows:
        by_prefix.setdefault(r.prefix, []).append(r)
    per_publisher = []
    for pref, group in by_prefix.items():
        a = [r for r in group if r.asserted]
        if a:
            per_publisher.append(sum(1 for r in a if r.correct) / len(a))
    return {
        "paragraphs": n,
        "publishers": len(by_prefix),
        "papers": len({r.paper for r in rows}),
        "asserted": len(asserted),
        "correct": len(correct),
        "wrong": len(asserted) - len(correct),
        "precision": round(len(correct) / len(asserted), 5) if asserted else None,
        "coverage": round(len(asserted) / n, 5) if n else None,
        "precision_macro": round(sum(per_publisher) / len(per_publisher), 5) if per_publisher else None,
        # the witness's own silences set aside: of the paragraphs the paper itself lanes, how
        # often does the reader name the same lane, and how many does it name at all
        "witness_named": len(named),
        "witness_other": n - len(named),
        "precision_on_named": round(len(named_correct) / len(named_asserted), 5) if named_asserted else None,
        "coverage_on_named": round(len(named_asserted) / len(named), 5) if named else None,
        "wrong_where_witness_said_other": sum(1 for r in asserted if r.xml_lane not in NAMED),
        **_per_paper(rows),
        # what happened to everything that was not asserted. A reader can reach any precision by
        # asserting less, and this is where that would show: prose swallowed by front matter or
        # read as a caption is a loss, not the same thing as a lane honestly left unnamed.
        "not_asserted": dict(sorted(Counter(r.where for r in rows if not r.asserted).items(),
                                    key=lambda kv: (-kv[1], kv[0]))),
    }


def by_lane(rows: Sequence[Landing]) -> dict[str, dict[str, Any]]:
    """The same, per lane of the witness — so "methods keeps useful coverage" is checkable."""
    out: dict[str, dict[str, Any]] = {}
    for lane in LANES:
        group = [r for r in rows if r.xml_lane == lane]
        if group:
            out[lane] = precision_and_coverage(group)
    return out


def confusion(rows: Sequence[Landing], limit: int = 12) -> list[tuple[str, int]]:
    """Where the wrong assertions went, most first: `witness -> reading`."""
    c: Counter[str] = Counter(f"{r.xml_lane} -> {r.pdf_lane}" for r in rows if r.wrong)
    return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]


# ---- intervals, clustered on the publisher -------------------------------------------------


def bootstrap(rows: Sequence[Landing], stat: Callable[[Sequence[Landing]], float | None],
              *, draws: int = 2000, seed: int = 7, alpha: float = 0.05) -> dict[str, Any]:
    """A percentile interval, resampling **publishers** with replacement, not paragraphs.

    Papers from one publisher share a layout, so their readings fail together; resampling
    paragraphs would treat a dozen readings of one template as a dozen independent observations
    and give an interval far too narrow to mean anything."""
    by_prefix: dict[str, list[Landing]] = {}
    for r in rows:
        by_prefix.setdefault(r.prefix, []).append(r)
    prefixes = sorted(by_prefix)
    point = stat(rows)
    if not prefixes or point is None:
        return {"point": point, "lo": None, "hi": None, "draws": 0, "publishers": len(prefixes)}
    rng = random.Random(seed)
    got: list[float] = []
    for _ in range(draws):
        pick: list[Landing] = []
        for _ in prefixes:
            pick.extend(by_prefix[prefixes[rng.randrange(len(prefixes))]])
        value = stat(pick)
        if value is not None:
            got.append(value)
    got.sort()
    if not got:
        return {"point": point, "lo": None, "hi": None, "draws": 0, "publishers": len(prefixes)}
    lo = got[max(0, int(alpha / 2 * len(got)) - 1)]
    hi = got[min(len(got) - 1, int((1 - alpha / 2) * len(got)))]
    return {"point": round(point, 5), "lo": round(lo, 5), "hi": round(hi, 5),
            "draws": len(got), "publishers": len(prefixes), "seed": seed}


def precision_of(rows: Sequence[Landing]) -> float | None:
    """Strict: an asserted lane on a paragraph the witness lanes `other` counts wrong."""
    asserted = [r for r in rows if r.asserted]
    return (sum(1 for r in asserted if r.correct) / len(asserted)) if asserted else None


def precision_on_named_of(rows: Sequence[Landing]) -> float | None:
    """The witness's own silences set aside: where the paper names a lane, does the reader?"""
    asserted = [r for r in rows if r.asserted and r.xml_lane in NAMED]
    return (sum(1 for r in asserted if r.correct) / len(asserted)) if asserted else None


def coverage_of(rows: Sequence[Landing]) -> float | None:
    return (sum(1 for r in rows if r.asserted) / len(rows)) if rows else None


# ---- risk against coverage -----------------------------------------------------------------


def risk_coverage(rows: Sequence[Landing], score: Callable[[Landing], float],
                  steps: int = 20) -> list[dict[str, Any]]:
    """Precision as the bar rises: the curve that says what abstention buys.

    A target met only at trivial coverage is not met, and this is what shows it."""
    asserted = [r for r in rows if r.asserted]
    if not asserted:
        return []
    scored = sorted(asserted, key=score, reverse=True)
    out = []
    for i in range(1, steps + 1):
        take = scored[: max(1, round(len(scored) * i / steps))]
        out.append({
            "coverage": round(len(take) / len(rows), 5),
            "precision": round(sum(1 for r in take if r.correct) / len(take), 5),
            "asserted": len(take),
        })
    return out


# ---- the split rules, enforced rather than intended -----------------------------------------


class SplitViolation(RuntimeError):
    """A split was read in a way the protocol does not allow."""


#: everything a sealed split may show: numbers about the corpus, never about a paper. A key not
#: on this list does not reach a SEALED or EXAM report, however it is asked for.
AGGREGATE = (
    "split", "corpus", "commit", "manifest_sha256", "flags",
    "overall", "precision_ci", "precision_on_named_ci", "coverage_ci",
    "by_lane", "by_type", "familiar", "novel", "confusion", "conservation",
)


#: what may be looked at, per split (`PLAN.md` §3). `FITTED` is not one of the campaign corpus's
#: splits: it is the old pair libraries, every publisher of which the reader was built on. It is
#: the *other* half of T3 — precision on a publisher already fitted — and nothing is held back
#: from it, because there is nothing left to hold back.
VISIBLE = {"DEV": "anything", "FITTED": "anything: these publishers are what the reader was built on",
           "VAL": "aggregates, per-publisher numbers, a logged inspection budget",
           "SEALED": "aggregates only", "EXAM": "scored once, after the code is frozen",
           "RESERVE": "not scored: it replaces a VAL publisher spent by inspection"}


def check_no_leak(manifest: dict[str, Any]) -> list[str]:
    """No publisher, DOI or PMCID in two splits, and no novel publisher that is really fitted.

    Good intentions erode by day five, so this is a check rather than a habit."""
    trouble: list[str] = []
    fitted = set(manifest.get("fitted_prefixes") or [])
    seen_prefix: dict[str, str] = {}
    seen_id: dict[str, str] = {}
    for r in manifest["papers"]:
        pre, split = r["prefix"], r["split"]
        if pre in fitted:
            trouble.append(f"{pre} is in `fitted_prefixes` and also drawn into {split}")
        if pre in seen_prefix and seen_prefix[pre] != split:
            trouble.append(f"publisher {pre} is in both {seen_prefix[pre]} and {split}")
        seen_prefix[pre] = split
        for ident in (r.get("doi"), r.get("pmcid")):
            if not ident:
                continue
            ident = str(ident).lower()
            if ident in seen_id and seen_id[ident] != split:
                trouble.append(f"{ident} is in both {seen_id[ident]} and {split}")
            seen_id[ident] = split
    return sorted(set(trouble))


def redact(split: str, report: dict[str, Any]) -> dict[str, Any]:
    """What a split is allowed to show. SEALED gives aggregates and nothing per paper.

    The harness enforces this rather than the person running it remembering to."""
    if split in ("DEV", "FITTED", "VAL"):
        return report
    if split in ("SEALED", "EXAM"):
        # A whitelist, not a blacklist. Naming the keys to *remove* means every key added to the
        # report later is visible on SEALED by default, and the one that leaks will be the one
        # nobody thought about. Naming the keys to keep means a new one is invisible until
        # somebody decides it is an aggregate.
        out = {k: report[k] for k in AGGREGATE if k in report}
        out["redacted"] = f"{split}: {VISIBLE[split]}"
        return out
    raise SplitViolation(f"{split} is not a split that is scored ({VISIBLE.get(split, 'unknown')})")
