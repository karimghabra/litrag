"""How far a reading can be trusted, from the reading alone.

Where a paper is held both as PDF and as JATS XML the two trees can be compared (`pairs.py`),
and that comparison says what goes wrong when a PDF is misread: almost never text that is
missing, mostly text that is all there and filed under the wrong lane — a top-level heading
the layout model dropped or fused with the next, so the results stay in the methods; a body
that begins with no heading and stays in the abstract; online methods printed after the
reference list and read as references; a highlights box that turns the body into back matter
— and text a block carries twice. None of the reader's older measurements (page coverage,
dropped lines, glyph residue, audit errors) predicts any of that (NOTES.md, 2026-09-17), so
the signals here are aimed at those failures and each is a plain measurement of the tree:

- where the prose lies: the share of it in the abstract, in back matter, in the reference
  list as paragraphs too long to be entries, in `other`, and in the single largest lane;
- the lanes a paper of its type should have and does not;
- text that repeats (six-word shingles seen more than once);
- headings that are not headings: fused with the next, a bullet, a sentence;
- paragraphs cut in two: ending without a full stop, starting in lower case; fragments;
- the reference list: its length, the share of it the text cites.

`signals(tree, kind)` returns them; `score(signals)` turns them into one number in [0, 1]
with the reasons it lost points, by thresholds measured on the pairs — see `CHECKS`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .citations import link_citations
from .citations import summarize as summarize_citations
from .pairs import LANES, Unit, units_of, words
from .tree import Tree, split_fused_heading

_BODY = ("introduction", "methods", "results", "results-discussion", "discussion", "other")
_END = re.compile(r"[.!?:;)\]”\"'’]\s*$|\.\s*\^?\[?\d[\d,–\- ]*\]?\s*$")  # a full stop, or one followed by a citation mark
_BULLET = re.compile(r"^\s*[•·▪■●○◦\-–—*»>]")
_UNTITLED = ("(untitled section)", "(heading not detected)")
K_DUP = 6

#: the lanes a paper of a type should have; a missing one is a heading the reader did not find
EXPECTED = {
    "research": ("introduction", "methods", "results", "discussion"),
    "review": ("introduction", "discussion"),
    "case-report": ("introduction", "discussion"),
    "protocol": ("introduction", "methods"),
    "data": ("methods",),
}


def _count(units: list[Unit]) -> int:
    return sum(sum(u.tokens.values()) for u in units)


def signals(tree: Tree, kind: dict[str, Any] | None = None) -> dict[str, Any]:
    """Plain measurements of one tree; every share is of words. Nothing here asks a model."""
    units = units_of(tree)
    prose = [u for u in units if u.kind in ("paragraph", "list_item") and not u.front and u.tokens]
    body = [u for u in prose if u.role in _BODY]
    abstract = [u for u in prose if u.role == "abstract"]
    back = [u for u in prose if u.role == "back"]
    refs_prose = [u for u in prose if u.role == "references" and u.kind == "paragraph" and sum(u.tokens.values()) >= 100]
    n_body, n_abs, n_back, n_refs_prose = _count(body), _count(abstract), _count(back), _count(refs_prose)
    n_all = max(n_body + n_abs + n_back + n_refs_prose, 1)
    by_lane = Counter()
    for u in body:
        by_lane[u.role] += sum(u.tokens.values())
    largest = max(by_lane.values(), default=0)

    # the lanes present, a combined results-and-discussion standing for both
    present = {u.role for u in prose} | {n.role for n in tree.walk() if n.type == "section"}
    if "results-discussion" in present:
        present |= {"results", "discussion"}
    paper_type = (kind or {}).get("type")
    expected = EXPECTED.get(paper_type or "", ())
    missing = [lane for lane in expected if lane not in present]

    # text that repeats: a block the layout model gave twice, or a paragraph glued to its own copy
    seen: Counter[str] = Counter()
    for u in prose:
        if u.role in LANES:
            ws = words(u.text)
            seen.update(" ".join(ws[i : i + K_DUP]) for i in range(len(ws) - K_DUP + 1))
    occurrences = sum(seen.values())
    repeated = sum(c - 1 for c in seen.values() if c > 1)

    headings = [n.heading for n in tree.walk() if n.type == "section" and n.heading and n.heading != "Front matter" and not n.heading.endswith(_UNTITLED) and n.label != "built"]
    odd = [h for h in headings if _BULLET.match(h) or h.rstrip().endswith((".", ",", ";")) or len(h.split()) > 16 or h[:1].islower() or split_fused_heading(h) is not None]
    sections = [n for n in tree.walk() if n.type == "section" and n.heading != "Front matter"]
    untitled = sum(1 for n in sections if (n.heading or "").endswith(_UNTITLED))
    built = sum(1 for n in sections if n.label == "built")

    long = [u for u in body if u.kind == "paragraph" and sum(u.tokens.values()) >= 20]
    unterminated = sum(1 for u in long if not _END.search(u.text.rstrip()))
    lower = sum(1 for u in long if u.text.lstrip()[:1].islower())
    fragments = sum(1 for u in body if u.kind == "paragraph" and sum(u.tokens.values()) < 6)

    cites = summarize_citations(*link_citations(tree, None))
    in_abstract = sum(1 for n in tree.walk() if n.role == "abstract" and n.type == "paragraph" and re.search(r"\[\d+(?:[,–-]\s*\d+)*\]|\bet al\.", n.text or ""))
    pages = max(len(tree.pages), 1)
    return {
        "words": n_body + n_abs,
        "abstract_share": round(n_abs / n_all, 4),
        "back_share": round(n_back / n_all, 4),
        "refs_prose_share": round(n_refs_prose / n_all, 4),
        "other_share": round(by_lane.get("other", 0) / max(n_body, 1), 4),
        "largest_lane_share": round(largest / max(n_body, 1), 4),
        "largest_lane": max(by_lane, key=by_lane.get) if by_lane else None,
        "missing_lanes": missing,
        "missing_lanes_n": len(missing),
        "type": paper_type,
        "type_unsettled": int(paper_type in (None, "other") and (kind or {}).get("source") in (None, "default", "none")),
        "repeated_share": round(repeated / occurrences, 4) if occurrences else 0.0,
        "headings": len(headings),
        "headings_per_page": round(len(headings) / pages, 3),
        "odd_headings": len(odd),
        "odd_heading_share": round(len(odd) / len(headings), 4) if headings else 0.0,
        "odd_heading_sample": odd[:4],
        "untitled_sections": untitled,
        "built_headings": built,
        "unterminated_share": round(unterminated / len(long), 4) if long else 0.0,
        "lowercase_start_share": round(lower / len(long), 4) if long else 0.0,
        "fragment_share": round(fragments / max(len(body), 1), 4),
        "abstract_paragraphs": len(abstract),
        "abstract_citing": in_abstract,
        "no_abstract": int(not abstract),
        "refs": cites["refs"],
        "cited_share": round(cites["cited_refs"] / cites["refs"], 4) if cites["refs"] else None,
        "no_refs": int(cites["refs"] == 0),
        "paragraphs_per_page": round(len(body) / pages, 3),
    }



# -- the score -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Check:
    """One way a reading goes wrong, as a measurement with two marks: below `limit` nothing is
    lost, at `worst` the whole `weight` is, and in between in proportion — a reading a little
    past the limit is a little less sure, not suddenly wrong."""

    name: str
    value: Callable[[dict[str, Any]], float | None]
    limit: float
    worst: float
    weight: float
    says: str  # the reason, for a person; `{v}` is the value as a percentage or a count

    def penalty(self, sig: dict[str, Any]) -> float:
        v = self.value(sig)
        if v is None or v <= self.limit:
            return 0.0
        return round(self.weight * min((v - self.limit) / (self.worst - self.limit), 1.0), 4)


def _largest(lane: str, types: tuple[str, ...] | None = None) -> Callable[[dict[str, Any]], float | None]:
    return lambda s: s["largest_lane_share"] if s.get("largest_lane") == lane and (types is None or s.get("type") in types) else None


#: Limits and worsts are read off the 199 papers held as both PDF and XML (NOTES.md,
#: 2026-09-17): each limit sits at or above the largest value a well-matched paper showed,
#: or, where the two groups overlap, where a flag is right three times in four.
CHECKS: tuple[Check, ...] = (
    Check("abstract", lambda s: s["abstract_share"], 0.10, 0.25, 0.5, "the abstract holds {v} of the prose: the body began with no heading the reader knew"),
    Check("back", lambda s: s["back_share"], 0.20, 0.50, 0.6, "{v} of the prose lies in back matter"),
    Check("references", lambda s: s["refs_prose_share"], 0.02, 0.20, 0.6, "{v} of the prose lies in the reference list as paragraphs too long to be entries"),
    Check("introduction", _largest("introduction"), 0.33, 0.70, 0.7, "the introduction holds {v} of the body: the sections after it were read as its children"),
    Check("methods", _largest("methods"), 0.45, 0.75, 0.6, "the methods hold {v} of the body: a heading after them was missed"),
    Check("discussion", _largest("discussion", ("review",)), 0.55, 0.90, 0.5, "the discussion holds {v} of a review's body"),
    Check("missing", lambda s: float(s["missing_lanes_n"]), 0.0, 2.0, 0.6, "{n} of the lanes a paper of this type has are missing"),
    Check("repeats", lambda s: s["repeated_share"], 0.04, 0.15, 0.4, "{v} of the text is there twice"),
    Check("cut", lambda s: s["unterminated_share"], 0.10, 0.35, 0.35, "{v} of the paragraphs end without a full stop: cut in two"),
    Check("headings", lambda s: float(s["odd_headings"]), 2.0, 8.0, 0.3, "{n} headings are not headings: a bullet, a sentence, two fused"),
    Check("type", lambda s: float(s["type_unsettled"]), 0.0, 1.0, 0.15, "no source says what kind of paper this is, and its shape does not either"),
)


def score(sig: dict[str, Any]) -> dict[str, Any]:
    """`{confidence, reasons, penalties}`: one number in (0, 1] — every check takes its share
    of what the ones before it left — and the reasons it is not 1, the heaviest first."""
    confidence = 1.0
    fired: list[tuple[float, str, str]] = []
    for c in CHECKS:
        pen = c.penalty(sig)
        if pen > 0:
            v = c.value(sig) or 0.0
            confidence *= 1.0 - pen
            fired.append((pen, c.name, c.says.format(v=f"{v:.0%}", n=int(v))))
    fired.sort(reverse=True)
    return {"confidence": round(confidence, 3), "reasons": [text for _, _, text in fired], "penalties": {name: pen for pen, name, _ in fired}}


def assess(tree: Tree, kind: dict[str, Any] | None = None) -> dict[str, Any]:
    """The signals and the score of one reading."""
    sig = signals(tree, kind)
    return {**score(sig), "signals": sig}


# -- calibration: the score against the pairs ------------------------------------------------------

def _auc(scores: list[float], labels: list[bool]) -> float | None:
    pos = [x for x, l in zip(scores, labels) if l]
    neg = [x for x, l in zip(scores, labels) if not l]
    if not pos or not neg:
        return None
    return round(sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg)), 3)


def _rank_correlation(xs: list[float], ys: list[float]) -> float:
    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        out = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return round(sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / den, 3) if den else 0.0


def calibrate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The score of every pair's PDF reading (from the signals stored in the pair record)
    against what the comparison with its XML found: *well matched* is faithful and precision
    both at least 0.9; *seriously mismatched* is either under 0.8."""
    rows = [(r, score(r["intrinsic"])) for r in records if r.get("faithful") is not None and r.get("intrinsic")]
    conf = [sc["confidence"] for _, sc in rows]
    well = [(r["faithful"] or 0) >= 0.9 and (r["precision"] or 0) >= 0.9 for r, _ in rows]
    serious = [(r["faithful"] or 0) < 0.8 or (r["precision"] or 0) < 0.8 for r, _ in rows]
    bands = []
    for lo, hi in ((0.9, 1.01), (0.75, 0.9), (0.5, 0.75), (0.0, 0.5)):
        inside = [i for i, c in enumerate(conf) if lo <= c < hi]
        bands.append({"confidence": f"{lo:g} to {min(hi, 1):g}", "papers": len(inside), "well_matched": sum(well[i] for i in inside), "seriously_mismatched": sum(serious[i] for i in inside), "mean_faithful": round(sum(rows[i][0]["faithful"] for i in inside) / len(inside), 3) if inside else None})
    checks = {}
    for c in CHECKS:
        fired = [i for i, (_, sc) in enumerate(rows) if c.name in sc["penalties"]]
        checks[c.name] = {"fires": len(fired), "on_badly_matched": sum(1 for i in fired if not well[i]), "on_seriously_mismatched": sum(1 for i in fired if serious[i])}
    return {
        "pairs": len(rows), "well_matched": sum(well), "seriously_mismatched": sum(serious),
        "auc_well": _auc(conf, well), "auc_not_serious": _auc(conf, [not x for x in serious]),
        "rank_correlation_with_faithful": _rank_correlation(conf, [r["faithful"] for r, _ in rows]),
        "bands": bands, "checks": checks,
        "sure_and_wrong": sorted(({"key": r["key"], "confidence": sc["confidence"], "faithful": r["faithful"], "precision": r["precision"]} for (r, sc), bad in zip(rows, serious) if bad and sc["confidence"] >= 0.9), key=lambda x: x["faithful"])[:12],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m litrag_parser.confidence", description="The confidence score against papers held as both PDF and XML.")
    ap.add_argument("--calibrate", nargs="+", metavar="PAIRS.json", help="pair records saved by `python -m litrag_parser.pairs --json`")
    ap.add_argument("--json", help="save the calibration here")
    args = ap.parse_args(argv)
    if not args.calibrate:
        ap.print_help()
        return 1
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    records: list[dict[str, Any]] = []
    for f in args.calibrate:
        records += json.loads(Path(f).read_text("utf-8"))["pairs"]
    cal = calibrate(records)
    if args.json:
        Path(args.json).write_text(json.dumps(cal, indent=1, ensure_ascii=False), "utf-8")
    print(f"{cal['pairs']} pairs · well matched {cal['well_matched']} · seriously mismatched {cal['seriously_mismatched']}")
    print(f"  the score ranks a well-matched paper above another {cal['auc_well']} of the time · a paper that is not seriously mismatched above one that is {cal['auc_not_serious']} · rank correlation with faithful {cal['rank_correlation_with_faithful']}")
    print("  confidence      papers  well matched  seriously mismatched  mean faithful")
    for b in cal["bands"]:
        print(f"  {b['confidence']:14} {b['papers']:7} {b['well_matched']:13} {b['seriously_mismatched']:21} {b['mean_faithful']!s:>14}")
    print("  check            fires  on badly matched  on seriously mismatched")
    for name, c in cal["checks"].items():
        print(f"  {name:15} {c['fires']:6} {c['on_badly_matched']:17} {c['on_seriously_mismatched']:24}")
    if cal["sure_and_wrong"]:
        print("  sure and seriously wrong:")
        for x in cal["sure_and_wrong"]:
            print(f"    {x['key']:40} confidence {x['confidence']} · faithful {x['faithful']} · precision {x['precision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

