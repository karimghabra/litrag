"""Each paper type's canonical skeleton, and every paper laid against its type's.

Three questions, all answered from the rows (`papers`, `nodes`) and nothing else:

1. **What does a paper of this type look like?** `skeletons` — per type, the slots its papers
   have, in the order they come, how many of the papers have each, how long each runs, and
   the printed headings that filled it. The contract a type makes (`confidence.EXPECTED`: a
   research paper has an introduction, methods, results and a discussion) is marked beside
   what was observed, and a slot the contract expects that no paper has is listed at share 0.
2. **How does this paper map onto it?** `mapping` — every printed section, top-level and
   below, with the slot it went to, *by which mechanism*, how sure, and what the reader nearly
   said where it declined; then which of the type's slots the paper matched, which it lacks,
   which it has that the type does not, and where its order departs from the type's.
3. **What does the paper look like re-hung on the skeleton?** `canonical_tree` — the paper's
   sections under the type's slots in the type's order, with `other` for what maps nowhere:
   the tree the window draws beside the printed one.

**The slot is the lane.** A slot is `nodes.role` of a top-level section — abstract,
introduction, methods, results, results-discussion, discussion, other — and the catalogue's
canonical names (`nodes.canonical`: "Materials and methods", "Study design", "Conclusions")
are a breakdown inside it, not slots of their own. Three reasons. The lane is what the reader
*asserts* and what retrieval partitions on (`facets.FACETS`), so a skeleton over lanes is a
skeleton over the thing that is used. A canonical name is often absent where the lane is
not — the embedder names lanes the catalogue has no name for, and a built heading's name is
the catalogue's name *for its lane* — so a skeleton over names would drop sections the reader
did file. And several names are one slot in two spellings (a methods section is "Materials
and methods" in one journal and "Experimental" in the next; the catalogue gives both one
name, but "Study design" and "Materials" are methods too when printed at the top). Where a
distinction inside a lane is real — Discussion then Conclusions — the per-slot `canonical`
breakdown keeps it, with its own share and position.

**Furniture** is left out of the skeleton and listed at its ends: the reader's own "Front
matter" container at the head, `references` and `back` at the tail (wherever the layout set a
funding statement, it is back matter, not a body slot).

**`other` is a slot, and it is not a guess.** A section whose lane was refused or never named
is `other`; it is never counted under the lane it was nearly given (invariant 5). The near
miss is shown as `guess`, and a missing slot says which `other` sections nearly filled it.

**The mechanism** is read, never presumed. `mechanism_of` documents the whole mapping; in
short, in order: the row's own `reasons` JSON when it has one (a withheld lane, a heading
the paragraphs disagreed with); the `built` label; inheritance from the parent for a
subsection; the reader's rules *replayed* on the stored heading (the vocabulary, the
catalogue — pure functions, no model, and marked `evidence: "replay"` because they are
today's rules, not necessarily those in force when the paper was read); the embedder's
stored verdict for that heading in `lanes.sqlite`, opened read-only, when it names the
stored lane; and `unknown` otherwise — the paragraphs' block classifier, the outline judge's
lane and the embedder without a stored verdict leave no mark on the row, and a mechanism
that cannot be read is not filled in.

    uv run --project parser python -m litrag_parser.canonical --store PATH [--paper KEY] [--json] [--lanes PATH|off]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .confidence import EXPECTED
from .facets import normalise, role_of
from .headings import canonical_of, top_level_lane
from .tree import _NOT_NUMBERED, top_number

#: the lanes a skeleton is made of, in the order the catalogue would set them; the tie-break when
#: two slots share a median position
BODY_ORDER = ("abstract", "introduction", "methods", "results", "results-discussion", "discussion", "other")
#: furniture: never a slot of the skeleton, listed at its ends
HEAD_FURNITURE = ("front",)
TAIL_FURNITURE = ("references", "back")
#: a slot at least this share of a type's papers have is one the type is taken to have: a paper of
#: the type without it is `missing` it, a paper with a slot under it has it `extra`
TYPICAL = 0.5
#: prose, for words and paragraphs
_PROSE = ("paragraph", "list_item")
_UNTITLED = ("(untitled section)", "(heading not detected)")
_OUTLINE_BUILT = "#section-outline-"  # outline.py: in the id of every section the outline judge built

#: every mechanism `mechanism_of` can return, and what it means. `evidence` says where the word
#: came from: `reasons` (the row's own JSON), `label` (the row's label or id), `tree` (its
#: parent), `replay` (today's rules run again on the stored heading), `lanes` (a verdict stored
#: in lanes.sqlite), or None (nothing to read).
MECHANISMS = {
    "vocabulary": "the vocabulary in facets.py names the heading exactly",
    "catalogue": "the catalogue in headings.py names the heading (an exact spelling, or a family for a body lane)",
    "embedder": "the embedder named the heading by resemblance (a stored heading verdict names this lane)",
    "position": "the heading names the abstract, and the body had begun: a closing summary is the discussion",
    "built": "the reader built this heading from a paper that printed none (structure.py); its lane is the paragraphs'",
    "outline": "the outline judge built this heading (outline.py)",
    "inherited": "a subsection takes its parent's lane",
    "withheld": "a lane was named by fewer routes than the canonical floor asks, and withheld: `other`, the lane kept as the guess",
    "disagreement": "the heading named a lane its paragraphs read as another; neither was asserted: `other`, the heading's lane kept as the guess",
    "numbered": "the heading resembles back matter but carries a body number, so it is a body section: `other`",
    "front": "the reader's own container for what comes before the first heading",
    "untitled": "a heading the layout dropped, stood in for by a visibly untitled section",
    "none": "nothing named the heading: `other`",
    "unknown": "the row does not say what named it (the paragraphs' classifier, the outline's lane, or the embedder without a stored verdict), and it is not presumed",
}


# -- reading the rows ------------------------------------------------------------------------------

def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _words(text: str | None) -> int:
    return len((text or "").split())


def _paper_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    have = _cols(conn, "papers")
    want = ["key", "title", "type", "subtype", "format", "confidence", "has_methods", "status"]
    sel = ", ".join(c if c in have else f"NULL AS {c}" for c in want)
    return [dict(zip(want, r)) for r in conn.execute(f"SELECT {sel} FROM papers ORDER BY key")]


def _nodes(conn: sqlite3.Connection, key: str) -> list[dict[str, Any]]:
    """A paper's nodes as dicts, with `children` linked and each section's `words` and
    `paragraphs` summed over its subtree. Columns a store predates read as None."""
    have = _cols(conn, "nodes")
    want = ["node_id", "parent", "ordinal", "depth", "type", "label", "level", "role", "heading", "canonical", "text", "guess", "confidence", "reasons"]
    sel = ", ".join(c if c in have else f"NULL AS {c}" for c in want)
    rows = [dict(zip(want, r)) for r in conn.execute(f"SELECT {sel} FROM nodes WHERE paper = ? ORDER BY depth, ordinal", (key,))]
    by_id = {r["node_id"]: r for r in rows}
    for r in rows:
        r["children"] = []
        try:
            r["reasons"] = json.loads(r["reasons"]) if r["reasons"] else None
        except ValueError:
            r["reasons"] = None
    for r in rows:
        if r["parent"] in by_id:
            by_id[r["parent"]]["children"].append(r)
    for r in rows:
        r["children"].sort(key=lambda c: c["ordinal"])

    def total(n: dict[str, Any]) -> tuple[int, int]:
        w = _words(n["text"]) if n["type"] in _PROSE else 0
        p = 1 if n["type"] == "paragraph" else 0
        for c in n["children"]:
            cw, cp = total(c)
            w, p = w + cw, p + cp
        n["words"], n["paragraphs"] = w, p
        return w, p

    for r in rows:
        if r["parent"] is None or r["parent"] not in by_id:
            total(r)
    return rows


def _root(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((r for r in rows if r["parent"] is None), None)


def _tops(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    root = _root(rows)
    return [c for c in root["children"] if c["type"] == "section"] if root else []


def slot_of(section: dict[str, Any]) -> str:
    """A top-level section's slot: `front` for the reader's front-matter container, else its lane."""
    if section.get("heading") == "Front matter" and section.get("role") == "other":
        return "front"
    return section.get("role") or "other"


def _furniture(slot: str) -> str | None:
    return "head" if slot in HEAD_FURNITURE else "tail" if slot in TAIL_FURNITURE else None


# -- the stored verdicts, read-only ----------------------------------------------------------------

class Verdicts:
    """The embedder's stored answers (`lanes.sqlite`), opened read-only and never asked anew.

    A verdict is looked up the way `meaning.Oracle._lookup` does it: under today's space for
    the kind, replayed under today's rule when the row carries its ranking, taken as written
    when it was written under today's rule (or before rules were stored), and not at all when a
    different rule wrote it — that answer was a rule no longer in force."""

    def __init__(self, path: Path | str):
        from . import meaning  # noqa: PLC0415 — only when a verdict store is asked for

        self._m = meaning
        self._conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
        model = os.environ.get("LITRAG_LANES_MODEL") or meaning.DEFAULT_MODEL
        self._kinds = {k: meaning.KINDS[k] for k in ("heading", "canonical") if k in meaning.KINDS}
        self._model = {k: f"{model}@{kind.space()}" for k, kind in self._kinds.items()}
        self._cols = _cols(self._conn, "verdicts")

    def get(self, kind_name: str, text: str) -> dict[str, Any] | None:
        kind = self._kinds.get(kind_name)
        if kind is None:
            return None
        ranked_col = "ranked" if "ranked" in self._cols else "NULL"
        rule_col = "rule" if "rule" in self._cols else "NULL"
        row = self._conn.execute(f"SELECT name, score, margin, {ranked_col}, {rule_col} FROM verdicts WHERE kind = ? AND key = ? AND model = ?", (kind_name, self._m.key_of(text), self._model[kind_name])).fetchone()
        if row is None:
            return None
        name, score, margin, ranked, rule = row
        top = None
        if ranked:
            pairs = [(g, c) for g, c in json.loads(ranked)]
            v = self._m._decide(pairs, kind.threshold, kind.margin, kind.lane_margin)
            name, score, margin = v.name, v.score, v.margin
            top = pairs[0][0] if pairs else None
        elif rule is not None and rule != kind.rule():
            return None
        return {"name": name, "cosine": score, "margin": margin, "top": top}

    def close(self) -> None:
        self._conn.close()


def _lanes_idle() -> bool:
    from . import lanes  # noqa: PLC0415

    return lanes.active() is None


def default_lanes(store: Path) -> Path | None:
    """`lanes.sqlite` beside the libraries: the store is `<root>/<library>/store.sqlite`."""
    p = Path(store).resolve().parent.parent / "lanes.sqlite"
    return p if p.exists() else None


# -- the mechanism ---------------------------------------------------------------------------------

def mechanism_of(section: dict[str, Any], parent_role: str | None = None, verdicts: Verdicts | None = None) -> dict[str, Any]:
    """Which mechanism gave a section its lane, read from the row — `{mechanism, evidence, guess,
    confidence, detail}`. In order, the first that applies:

    1. **`reasons`, the row's own JSON** (`nodes.reasons`), which the reader writes where it
       declined:
       - `{"recognised_by": [...], "withheld": lane}` (tree.py, the canonical floor) →
         `withheld`; the guess is `withheld`, and `recognised_by` says which routes knew it;
       - `{"heading": lane, "paragraphs": lane, "cosine", "margin"}` (structure._abstain) →
         `disagreement`; the guess is the heading's lane;
       - `{"vocabulary"|"catalogue"|"embedder"|"outline": lane, ...}` (the shape `tree.Node`
         documents for reasons to come) → the first of those, in that order, whose lane is the
         row's; else `unknown`.
       Any other shape → `unknown`, the JSON passed through as `detail`.
    2. **`label = 'built'`** → `outline` when the id carries the outline's mark, else `built`.
    3. **A subsection** (a parent that is a section) with its parent's lane → `inherited`.
    4. **The reader's own containers**: "Front matter" → `front`; a heading ending "(heading not
       detected)" or "(untitled section)" → `untitled`.
    5. **Today's rules, replayed** on the stored heading (`evidence: "replay"`): the vocabulary
       (`facets.role_of(meaning=False)`) names the row's lane → `vocabulary`; the vocabulary
       says abstract and the row says discussion → `position` (tree.py: a "Summary" after the
       body began closes it); where the vocabulary is silent, the catalogue
       (`headings.top_level_lane`) names the row's lane → `catalogue`.
    6. **The embedder's stored verdict** (`lanes.sqlite`, when given) names the row's lane →
       `embedder`, with its cosine and margin.
    7. More of tree.py's rules, replayed: `methods` from `role_of`'s last rule (a short heading
       carrying the word; only while no oracle is configured, so no model is ever asked) →
       `vocabulary`; an `other` heading with a body number that the catalogue or the stored
       verdict files as abstract, references or back matter → `numbered`.
    8. A row that is `other` and that nothing above names → `none` (the guess is the
       embedder's nearest lane when a stored ranking has one, marked as such); a named row
       nothing above explains → `unknown`.

    `guess` and `confidence` are the row's own columns wherever it has them."""
    role = section.get("role") or "other"
    heading = section.get("heading") or ""
    reasons = section.get("reasons")
    out: dict[str, Any] = {"mechanism": "unknown", "evidence": None, "guess": section.get("guess"), "confidence": section.get("confidence"), "detail": None}

    if reasons:
        out["evidence"] = "reasons"
        out["detail"] = reasons
        if isinstance(reasons, dict) and "withheld" in reasons:
            out["mechanism"] = "withheld"
            out["guess"] = out["guess"] or reasons.get("withheld")
        elif isinstance(reasons, dict) and "heading" in reasons and "paragraphs" in reasons:
            out["mechanism"] = "disagreement"
            out["guess"] = out["guess"] or reasons.get("heading")
        elif isinstance(reasons, dict):
            named = next((m for m in ("vocabulary", "catalogue", "embedder", "outline") if reasons.get(m) == role and role != "other"), None)
            out["mechanism"] = named or "unknown"
        return out

    if section.get("label") == "built":
        out["evidence"] = "label"
        out["mechanism"] = "outline" if _OUTLINE_BUILT in (section.get("node_id") or "") else "built"
        return out

    if parent_role is not None and role == parent_role:
        out["evidence"] = "tree"
        out["mechanism"] = "inherited"
        return out

    if heading == "Front matter" and role == "other":
        out["evidence"] = "label"
        out["mechanism"] = "front"
        return out
    if heading.endswith(_UNTITLED):
        out["evidence"] = "label"
        out["mechanism"] = "untitled"
        return out

    vocab = role_of(heading, meaning=False) if heading else "other"
    catalogue = top_level_lane(heading) if heading else None
    if role != "other":
        # tree.py's order: the vocabulary first, and the catalogue only where it is silent
        if vocab == role:
            return {**out, "mechanism": "vocabulary", "evidence": "replay"}
        if vocab == "abstract" and role == "discussion":
            return {**out, "mechanism": "position", "evidence": "replay"}
        if vocab == "other" and catalogue == role:
            return {**out, "mechanism": "catalogue", "evidence": "replay"}

    v = verdicts.get("heading", normalise(heading)) if verdicts is not None and heading else None
    if role != "other" and v is not None and v["name"] == role:
        return {**out, "mechanism": "embedder", "evidence": "lanes", "confidence": out["confidence"] if out["confidence"] is not None else v["cosine"], "detail": {"cosine": v["cosine"], "margin": v["margin"]}}

    if role == "methods" and heading and _lanes_idle() and role_of(heading) == "methods":
        # facets.role_of's last rule, with no embedder answering: a short heading that carries
        # the word itself ("Methods and Dataset") is methods. Replayed only while no oracle is
        # configured in this process, so that the replay can never ask a model.
        return {**out, "mechanism": "vocabulary", "evidence": "replay", "detail": {"rule": "a short heading carrying the word"}}

    embedder = v["name"] if v else None
    if role == "other" and heading and top_number(heading) is not None and ({catalogue, embedder} & _NOT_NUMBERED):
        # tree.py: a heading carrying a body number is a body section, whatever back matter it
        # resembles (a review's "8. Regulatory and Ethical Considerations")
        return {**out, "mechanism": "numbered", "evidence": "replay", "guess": out["guess"] or next(x for x in (catalogue, embedder) if x in _NOT_NUMBERED)}

    if role == "other":
        if vocab == "other" and catalogue is None and (v is None or v["name"] == "other"):
            out["mechanism"] = "none"
            out["evidence"] = "replay" if v is None else "lanes"
            if out["guess"] is None and v is not None and v.get("top") and v["top"] != "other":
                out["guess"] = v["top"]
                out["detail"] = {"guess_from": "lanes", "cosine": v["cosine"], "margin": v["margin"]}
            return out
        out["detail"] = {"replayed": {"vocabulary": vocab, "catalogue": catalogue, "embedder": v["name"] if v else None}}
        return out  # a rule names a lane today and the row says other: something withheld it, and the row does not say what
    out["detail"] = {"replayed": {"vocabulary": vocab, "catalogue": catalogue, "embedder": v["name"] if v else None}}
    return out


def canonical_by(section: dict[str, Any], verdicts: Verdicts | None = None) -> str | None:
    """How the catalogue's name was found: `table` or `pattern` (replayed), `embedder` (a stored
    `canonical` verdict names it), `built` (a built heading takes its lane's name), `unknown`;
    None for a section with no canonical name."""
    name = section.get("canonical")
    if name is None:
        return None
    if section.get("label") == "built":
        return "built"
    got, how = canonical_of(section.get("heading"))
    if got == name and how:
        return how
    if verdicts is not None:
        v = verdicts.get("canonical", section.get("heading") or "")
        if v is not None and v["name"] == name:
            return "embedder"
    return "unknown"


# -- the skeletons ---------------------------------------------------------------------------------

def _positions(body: list[dict[str, Any]]) -> dict[str, float]:
    """Each slot's first place among a paper's body sections, 0 to 1."""
    n = len(body)
    out: dict[str, float] = {}
    for i, s in enumerate(body):
        out.setdefault(slot_of(s), i / (n - 1) if n > 1 else 0.0)
    return out


def _median(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 3) if xs else None


def _order_key(slot: dict[str, Any]) -> tuple[float, int]:
    pos = slot["median_position"]
    return (pos if pos is not None else 2.0, BODY_ORDER.index(slot["slot"]) if slot["slot"] in BODY_ORDER else len(BODY_ORDER))


def _covered(lane: str, present: set[str]) -> bool:
    """A combined results-and-discussion stands for both halves, and both halves for it."""
    if lane in present:
        return True
    if lane in ("results", "discussion") and "results-discussion" in present:
        return True
    return lane == "results-discussion" and {"results", "discussion"} <= present


def skeletons(conn: sqlite3.Connection) -> dict[str, Any]:
    """Per paper type, its canonical skeleton: `{type: {type, papers, expected, slots, furniture}}`,
    types by paper count. Each slot: `{slot, papers, share, median_position, median_sections,
    words_median, expected, status, canonical: [{name, papers, share, median_position}],
    examples: [{heading, count}]}`, ordered by median position; `status` is `expected` (the
    type's contract names it), `typical` (a share of TYPICAL or more) or `observed`."""
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in _paper_rows(conn):
        rows = _nodes(conn, p["key"])
        if not rows:
            continue
        by_type[p["type"] or "untyped"].append({"paper": p, "tops": _tops(rows)})
    out: dict[str, Any] = {}
    for kind, papers in sorted(by_type.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        n = len(papers)
        acc: dict[str, dict[str, Any]] = defaultdict(lambda: {"papers": 0, "positions": [], "counts": [], "words": [], "canon": defaultdict(lambda: {"papers": set(), "positions": []}), "examples": Counter()})
        for i, pp in enumerate(papers):
            body = [s for s in pp["tops"] if _furniture(slot_of(s)) is None]
            pos = _positions(body)
            seen: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for s in pp["tops"]:
                seen[slot_of(s)].append(s)
            nb = len(body)
            place = {id(s): j for j, s in enumerate(body)}
            for slot, secs in seen.items():
                a = acc[slot]
                a["papers"] += 1
                if slot in pos:
                    a["positions"].append(pos[slot])
                a["counts"].append(len(secs))
                a["words"].append(sum(s["words"] for s in secs))
                for s in secs:
                    if s.get("heading") and slot not in ("front",):
                        a["examples"][normalise(s["heading"]) or s["heading"]] += 1
                    if s.get("canonical"):
                        c = a["canon"][s["canonical"]]
                        c["papers"].add(i)
                        if id(s) in place:
                            c["positions"].append(place[id(s)] / (nb - 1) if nb > 1 else 0.0)
        expected = list(EXPECTED.get(kind, ()))
        slots, head, tail = [], [], []
        for slot, a in acc.items():
            entry = {
                "slot": slot, "papers": a["papers"], "share": round(a["papers"] / n, 3),
                "median_position": _median(a["positions"]), "median_sections": _median([float(c) for c in a["counts"]]),
                "words_median": int(statistics.median(a["words"])) if a["words"] else 0,
                "expected": slot in expected,
                "canonical": sorted(({"name": name, "papers": len(c["papers"]), "share": round(len(c["papers"]) / a["papers"], 3), "median_position": _median(c["positions"])} for name, c in a["canon"].items()), key=lambda x: (-x["papers"], x["name"])),
                "examples": [{"heading": h, "count": k} for h, k in a["examples"].most_common(6)],
            }
            entry["status"] = "expected" if entry["expected"] else "typical" if entry["share"] >= TYPICAL else "observed"
            f = _furniture(slot)
            (head if f == "head" else tail if f == "tail" else slots).append(entry)
        present = {s["slot"] for s in slots}
        for lane in expected:
            if lane not in present:
                slots.append({"slot": lane, "papers": 0, "share": 0.0, "median_position": None, "median_sections": None, "words_median": 0, "expected": True, "status": "expected", "canonical": [], "examples": []})
        slots.sort(key=_order_key)
        tail.sort(key=lambda s: TAIL_FURNITURE.index(s["slot"]))
        out[kind] = {"type": kind, "papers": n, "expected": expected, "slots": slots, "furniture": {"head": head, "tail": tail}}
    return out


# -- one paper against its type --------------------------------------------------------------------

def _section_entry(s: dict[str, Any], parent_role: str | None, verdicts: Verdicts | None) -> dict[str, Any]:
    m = mechanism_of(s, parent_role, verdicts)
    return {
        "node_id": s["node_id"], "heading": s.get("heading"), "level": s.get("level"), "depth": s.get("depth"),
        "role": s.get("role"), "slot": slot_of(s) if parent_role is None else s.get("role"),
        "canonical": s.get("canonical"), "canonical_by": canonical_by(s, verdicts),
        "mechanism": m["mechanism"], "evidence": m["evidence"], "confidence": m["confidence"], "guess": m["guess"], "detail": m["detail"],
        "built": s.get("label") == "built", "words": s["words"], "paragraphs": s["paragraphs"],
        "children": [_section_entry(c, s.get("role"), verdicts) for c in s["children"] if c["type"] == "section"],
    }


def _paper(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    return next((p for p in _paper_rows(conn) if p["key"] == key), None)


def mapping(conn: sqlite3.Connection, key: str, *, verdicts: Verdicts | None = None, skeleton: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """One paper against its type's skeleton: `{paper, skeleton, sections, slots, order,
    unassigned_words_share}`, or None for a paper the store does not have.

    `slots`: every slot of the type's skeleton and of the paper — `matched` (the paper has it),
    `missing` (the contract expects it, or the type typically has it, and the paper has
    nothing in it; `near` lists the paper's `other` sections whose guess was that lane),
    `extra` (the paper has it, the type neither expects nor typically has it), `absent` (an
    occasional slot of the type this paper does not have). `order`: each body section whose
    slot the type sets before the slot of a section that came earlier in this paper.
    `unassigned_words_share`: the words of the paper's prose in `other`, of all its prose
    outside the front matter and the furniture."""
    p = _paper(conn, key)
    if p is None:
        return None
    rows = _nodes(conn, key)
    kind = p["type"] or "untyped"
    sk = skeleton if skeleton is not None else skeletons(conn).get(kind, {"type": kind, "papers": 0, "expected": list(EXPECTED.get(kind, ())), "slots": [], "furniture": {"head": [], "tail": []}})
    tops = _tops(rows)
    sections = [_section_entry(s, None, verdicts) for s in tops]
    for e in sections:
        e["furniture"] = _furniture(e["slot"])

    have: dict[str, list[str]] = defaultdict(list)
    for e in sections:
        have[e["slot"]].append(e["node_id"])
    present = set(have)
    by_slot = {s["slot"]: s for s in sk["slots"]}
    slots: list[dict[str, Any]] = []
    for s in sk["slots"]:
        wanted = s["expected"] or s["share"] >= TYPICAL
        if s["slot"] in have:
            status = "matched" if wanted else "extra"
        elif s["slot"] == "other":
            status = "absent"  # `other` is where what maps nowhere goes: a paper without any lacks nothing
        elif wanted and not _covered(s["slot"], present):
            status = "missing"
        elif wanted:
            status = "matched"  # a combined results-and-discussion stands for the half the skeleton names
        else:
            status = "absent"
        entry: dict[str, Any] = {"slot": s["slot"], "status": status, "expected": s["expected"], "share": s["share"], "sections": have.get(s["slot"], [])}
        if status == "matched" and not have.get(s["slot"]):
            entry["covered_by"] = [nid for lane in ("results-discussion", "results", "discussion") for nid in have.get(lane, [])]
        if status == "missing":
            entry["near"] = [e["node_id"] for e in sections if e["slot"] == "other" and e["guess"] == s["slot"]]
        slots.append(entry)
    for slot, ids in have.items():
        if slot not in by_slot and _furniture(slot) is None:
            slots.append({"slot": slot, "status": "extra", "expected": False, "share": 0.0, "sections": ids})
    for f in ("head", "tail"):
        for s in sk["furniture"][f]:
            slots.append({"slot": s["slot"], "status": "furniture" if s["slot"] in have else "absent", "expected": False, "share": s["share"], "sections": have.get(s["slot"], [])})
        for slot, ids in have.items():
            if _furniture(slot) == f and slot not in {s["slot"] for s in sk["furniture"][f]}:
                slots.append({"slot": slot, "status": "furniture", "expected": False, "share": 0.0, "sections": ids})

    # order: a body section whose slot the type sets earlier than one already passed; `other` floats
    pos = {s["slot"]: s["median_position"] for s in sk["slots"] if s["median_position"] is not None}
    order: list[dict[str, Any]] = []
    furthest: tuple[str, float, str] | None = None
    for e in sections:
        if e["furniture"] or e["slot"] == "other" or e["slot"] not in pos:
            continue
        here = pos[e["slot"]]
        if furthest is not None and here < furthest[1] and e["slot"] != furthest[0]:
            order.append({"node_id": e["node_id"], "heading": e["heading"], "slot": e["slot"], "after": furthest[0], "after_node": furthest[2], "message": f"{e['slot']} after {furthest[0]}: the type sets {e['slot']} at {here} and {furthest[0]} at {furthest[1]}"})
        if furthest is None or here > furthest[1]:
            furthest = (e["slot"], here, e["node_id"])

    root = _root(rows)
    front_ids = {e["node_id"] for e in sections if e["slot"] == "front"}
    prose = [r for r in rows if r["type"] in _PROSE and r.get("role") not in TAIL_FURNITURE and not _under(r, front_ids, rows)]
    total = sum(_words(r["text"]) for r in prose)
    other = sum(_words(r["text"]) for r in prose if r.get("role") == "other")
    return {
        "paper": {k: p[k] for k in ("key", "title", "type", "subtype", "format", "confidence")},
        "skeleton": sk["slots"], "furniture": sk["furniture"], "expected": sk["expected"],
        "sections": sections, "slots": slots, "order": order,
        "unassigned_words_share": round(other / total, 4) if total else None,
        "root": root["node_id"] if root else None,
    }


def _under(r: dict[str, Any], ids: set[str], rows: list[dict[str, Any]]) -> bool:
    if not ids:
        return False
    nid = r["node_id"]
    return any(nid == i or nid.startswith(i + "#") for i in ids) or r.get("parent") in ids


def canonical_tree(conn: sqlite3.Connection, key: str, *, verdicts: Verdicts | None = None, skeleton: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The paper re-hung on its type's skeleton: `{paper, slots: [{slot, status, furniture,
    sections: [{node_id, heading, canonical, paragraphs, words, moved_from?}]}]}` — the
    front furniture, the skeleton's slots in the type's order (each with what of this paper
    fills it, empty where it is missing), any slot of this paper the type lacks, `other`, the
    tail furniture. A subsection whose lane is not its parent's (a data statement inside a
    discussion) is hung under its own slot, `moved_from` its parent."""
    m = mapping(conn, key, verdicts=verdicts, skeleton=skeleton)
    if m is None:
        return None
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def leaf(e: dict[str, Any], moved_from: str | None = None) -> dict[str, Any]:
        d = {"node_id": e["node_id"], "heading": e["heading"], "canonical": e["canonical"], "mechanism": e["mechanism"], "paragraphs": e["paragraphs"], "words": e["words"]}
        if moved_from:
            d["moved_from"] = moved_from
        return d

    def walk(e: dict[str, Any], slot: str) -> None:
        for c in e["children"]:
            if c["role"] != e["role"] and c["role"] not in (None, "other"):
                buckets[c["role"]].append(leaf(c, e["node_id"]))
            walk(c, slot)

    for e in m["sections"]:
        buckets[e["slot"]].append(leaf(e))
        walk(e, e["slot"])
    status = {s["slot"]: s["status"] for s in m["slots"]}
    # the head furniture; the skeleton's slots in the type's order, `other` where the type sets
    # it; the paper's own slots the type lacks; `other` if the type never had it; the tail
    order = [*HEAD_FURNITURE, *(s["slot"] for s in m["skeleton"])]
    order += [s for s in buckets if s not in order and _furniture(s) is None and s != "other"]
    order += ["other", *TAIL_FURNITURE]
    out = []
    for slot in dict.fromkeys(order):
        secs = buckets.get(slot, [])
        if not secs and status.get(slot) != "missing" and slot != "other":
            continue  # an occasional slot of the type this paper does not have is not drawn
        out.append({"slot": slot, "status": status.get(slot, "extra" if secs else "absent"), "furniture": _furniture(slot), "sections": secs})
    return {"paper": m["paper"], "slots": out}


def types_overview(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Per type: `{type, papers, parsed, mean_confidence, with_methods, formats, subtypes}` —
    `with_methods` the share of its papers with a methods lane (`papers.has_methods`)."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in _paper_rows(conn):
        groups[p["type"] or "untyped"].append(p)
    out = []
    for kind, ps in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        conf = [p["confidence"] for p in ps if p["confidence"] is not None]
        hm = [p["has_methods"] for p in ps if p["has_methods"] is not None]
        out.append({
            "type": kind, "papers": len(ps), "parsed": sum(1 for p in ps if p["status"] == "parsed"),
            "mean_confidence": round(sum(conf) / len(conf), 3) if conf else None,
            "with_methods": round(sum(1 for x in hm if x) / len(hm), 3) if hm else None,
            "formats": dict(Counter(p["format"] or "unknown" for p in ps).most_common()),
            "subtypes": dict(Counter(p["subtype"] for p in ps if p["subtype"]).most_common()),
        })
    return out


# -- the command -----------------------------------------------------------------------------------

def open_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)


def _print_skeletons(over: list[dict[str, Any]], sk: dict[str, Any]) -> None:
    for t in over:
        s = sk.get(t["type"])
        print(f"{t['type']}: {t['papers']} papers · {t['formats']} · confidence {t['mean_confidence']} · methods {t['with_methods']}" + (f" · subtypes {t['subtypes']}" if t["subtypes"] else ""))
        if not s:
            continue
        mark = {"expected": "*", "typical": "+", "observed": " "}
        print("   " + "  ".join(f"{mark[x['status']]}{x['slot']} {x['share']:.2f}@{x['median_position']}" for x in s["slots"]))
        fur = s["furniture"]["head"] + s["furniture"]["tail"]
        if fur:
            print("   furniture: " + "  ".join(f"{x['slot']} {x['share']:.2f}" for x in fur))


def _print_mapping(m: dict[str, Any]) -> None:
    p = m["paper"]
    print(f"{p['key']} · {p['type']}{'/' + p['subtype'] if p['subtype'] else ''} · {p['format']} · confidence {p['confidence']} · unassigned {m['unassigned_words_share']}")
    print(f"   {p['title']}")

    def show(e: dict[str, Any], indent: int) -> None:
        extra = (f" guess={e['guess']}" if e["guess"] else "") + (f" conf={e['confidence']}" if e["confidence"] is not None else "") + (" [built]" if e["built"] else "")
        print(f"{' ' * indent}{(e['heading'] or '')[:60]:<60} → {e['slot'] or e['role']:<18} {e['mechanism']:<12} {e['canonical'] or '-'}{extra} ({e['words']}w)")
        for c in e["children"]:
            show(c, indent + 2)

    for e in m["sections"]:
        show(e, 3)
    print("   slots: " + "  ".join(f"{s['slot']}={s['status']}" for s in m["slots"] if s["status"] != "absent"))
    for o in m["order"]:
        print(f"   order: {o['message']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="litrag_parser.canonical", description=__doc__.split("\n\n")[0])
    ap.add_argument("--store", required=True, help="a library's store.sqlite, opened read-only")
    ap.add_argument("--paper", help="one paper's key: its mapping and its canonical tree")
    ap.add_argument("--lanes", help="the embedder's verdicts (default: lanes.sqlite beside the libraries); `off` for none")
    ap.add_argument("--json", action="store_true", help="print JSON")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    store = Path(args.store).expanduser()
    if not store.exists():
        print(json.dumps({"error": f"no store at {store}"}))
        return 2
    conn = open_readonly(store)
    lanes_path = None if args.lanes == "off" else (Path(args.lanes) if args.lanes else default_lanes(store))
    verdicts = Verdicts(lanes_path) if lanes_path and Path(lanes_path).exists() else None
    sk = skeletons(conn)
    if args.paper:
        p = _paper(conn, args.paper)
        if p is None:
            print(json.dumps({"error": f"no paper {args.paper}"}))
            return 2
        kind_sk = sk.get(p["type"] or "untyped")
        m = mapping(conn, args.paper, verdicts=verdicts, skeleton=kind_sk)
        t = canonical_tree(conn, args.paper, verdicts=verdicts, skeleton=kind_sk)
        if args.json:
            print(json.dumps({"mapping": m, "canonical_tree": t}, ensure_ascii=False, indent=1))
        else:
            _print_mapping(m)
    else:
        over = types_overview(conn)
        if args.json:
            print(json.dumps({"types": over, "skeletons": sk, "mechanisms": MECHANISMS}, ensure_ascii=False, indent=1))
        else:
            _print_skeletons(over, sk)
    return 0


if __name__ == "__main__":
    sys.exit(main())
