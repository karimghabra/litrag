"""Two readings of one paper, compared: how far the tree read from a PDF lies from the tree
read from the publisher's JATS XML of the same paper.

The XML states what a PDF only shows — where a section starts and how deep it is, which
paragraph is which, what is a caption, how many references there are — so where one library
holds a paper as a PDF and another holds it as JATS, the XML's tree is the nearest thing
there is to the truth about the PDF's, and the agreement between the two measures the reader
paper by paper. That agreement is what a confidence score, computed from a PDF's reading
alone, has to predict (`confidence.py`); this module is what such a score is calibrated on.

Text is located by shingles of four words, letters only: citation numbers, superscripts and
the spelling of numbers differ between the formats, and nothing here depends on the two
formats cutting paragraphs alike. Once a paragraph is located, its presence is counted in
words, not shingles: a word the PDF hyphenated at a line's end is one word lost, not the four
shingles it stood in (measured: that noise alone cost every paper three to eight points of
shingle recall). For every paragraph of the XML's abstract and body:

- **recall** — the share of its words held by the pieces of the PDF's reading it lies in;
  **exact** is the stricter share of its shingles found, which also counts spelling;
- **faithful** — recall times the share of it that lies in a paragraph of the *same lane*:
  text that reached a caption, a heading, the front matter, a table or the wrong section is
  read, not read right;
- whether it arrived **intact** (in one paragraph), **split** over several, **merged** with
  its neighbour, or is **missing**;

and the other way round, **precision**: the share of the PDF's paragraphs' words that the XML
holds at all — what is left is running heads, sidebars and boilerplate read as prose. Beside
the text: the headings (found, spurious, at the right depth, in the right lane), the
reference list's length, the citation links, the captions, the title.

One DOI files once in a library, so the two readings live in two libraries:

    python -m litrag_parser.pairs --pdf-lib DIR --xml-lib DIR [--json FILE] [--show KEY]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .citations import link_citations
from .citations import summarize as summarize_citations
from .facets import normalise
from .tree import Tree

#: the lanes whose paragraphs are the paper's own prose: what `faithful` is measured over
LANES = ("abstract", "introduction", "methods", "results", "results-discussion", "discussion", "other")
K = 4  # words to a shingle
_LETTERS = re.compile(r"[a-z]{2,}")
_UNTITLED = ("(untitled section)", "(heading not detected)")
_SOFT = {0xAD: None, 0xFFFE: None}  # a soft hyphen, and the mark pdfium leaves for a hyphen at a line's end


def words(text: str) -> list[str]:
    """Letters only, ligatures and accents undone: "ﬁbrils [12] were 3.5 µm" → fibrils, were."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower().translate(_SOFT)
    return _LETTERS.findall(t)


def shingles(ws: list[str], k: int = K) -> frozenset[str]:
    """Every run of `k` words; a text shorter than that, two words or more, is one shingle."""
    if len(ws) >= k:
        return frozenset(" ".join(ws[i : i + k]) for i in range(len(ws) - k + 1))
    return frozenset([" ".join(ws)]) if len(ws) >= 2 else frozenset()


@dataclass
class Unit:
    """One piece of a tree that carries text: a paragraph, a caption, a heading, a line of front matter."""

    node_id: str
    kind: str  # paragraph | list_item | caption | heading | meta | table | footnote | formula | other
    role: str
    text: str
    front: bool  # under "Front matter"
    shingles: frozenset[str] = field(default_factory=frozenset)
    tokens: Counter[str] = field(default_factory=Counter)

    @property
    def prose(self) -> bool:
        return self.kind in ("paragraph", "list_item") and self.role in LANES and not self.front


def units_of(tree: Tree) -> list[Unit]:
    out: list[Unit] = []
    for n in tree.walk():
        front = n.heading == "Front matter" or bool(n.ancestry and n.ancestry[0] == "Front matter")
        if n.type == "section":
            if n.heading and n.heading != "Front matter" and not n.heading.endswith(_UNTITLED):
                out.append(Unit(n.node_id, "heading", n.role, n.heading, front))
            continue
        if n.type == "table" and n.table:
            text = " ".join(" ".join(str(c) for c in row) for row in n.table.get("cells", []))
            out.append(Unit(n.node_id, "table", n.role, text, front))
            continue
        if not n.text:
            continue
        kind = n.type if n.type in ("paragraph", "list_item", "caption", "meta", "footnote", "formula") else "other"
        out.append(Unit(n.node_id, kind, n.role, n.text, front))
    for u in out:
        ws = words(u.text)
        u.shingles = shingles(ws)
        u.tokens = Counter(ws)
    return out


def _held(u: Unit, landed: Counter[int], others: list[Unit]) -> tuple[float, float]:
    """The share of a unit's words held by the units it landed in, and by the one it mostly
    landed in. A unit it shares one stray phrase with is not where it lies."""
    n = sum(u.tokens.values())
    if not landed or not n:
        return 0.0, 0.0
    total = sum(landed.values())
    top = landed.most_common(1)[0][0]
    where = [j for j, c in landed.items() if c >= 2 or c / total >= 0.2 or j == top]
    pool: Counter[str] = Counter()
    for j in where:
        pool.update(others[j].tokens)
    held = sum(min(c, pool[w]) for w, c in u.tokens.items()) / n
    held_top = sum(min(c, others[top].tokens[w]) for w, c in u.tokens.items()) / n
    return held, held_top


def _index(units: list[Unit]) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for j, u in enumerate(units):
        for s in u.shingles:
            index.setdefault(s, []).append(j)
    return index


def _land(u: Unit, index: dict[str, list[int]]) -> Counter[int]:
    """Where a unit's shingles are found on the other side: `{unit index: shingles}`, each
    shingle given to the unit that holds most of this text (a phrase two paragraphs share goes
    with the rest of its paragraph)."""
    hits: Counter[int] = Counter()
    for s in u.shingles:
        for j in index.get(s, ()):
            hits[j] += 1
    landed: Counter[int] = Counter()
    for s in u.shingles:
        js = index.get(s)
        if js:
            landed[max(js, key=lambda j: (hits[j], -j))] += 1
    return landed


def _headings(tree: Tree) -> list[dict[str, Any]]:
    out = []
    for n in tree.walk():
        if n.type != "section" or not n.heading or n.heading == "Front matter" or n.heading.endswith(_UNTITLED):
            continue
        clean = normalise(n.heading)
        if clean:
            out.append({"heading": n.heading, "clean": clean, "words": frozenset(words(clean)), "top": (n.level or 9) <= 1, "role": n.role, "built": n.label == "built"})
    return out


def _same_heading(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a["clean"] == b["clean"]:
        return True
    wa, wb = a["words"], b["words"]
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= 0.8  # a colon, a dash, a dropped word of five


def compare_headings(pdf: Tree, xml: Tree) -> dict[str, Any]:
    """The XML's headings found among the PDF's (recall), the PDF's own headings the XML has
    (precision; a heading the reader built is its own and is left out), and for those found on
    both sides, whether top-level is top-level and the lane is the lane."""
    xs, ps = _headings(xml), [h for h in _headings(pdf)]
    taken: set[int] = set()
    matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for x in xs:
        for j, p in enumerate(ps):
            if j not in taken and _same_heading(x, p):
                taken.add(j)
                matched.append((x, p))
                break
    own = [p for p in ps if not p["built"]]
    own_matched = sum(1 for _, p in matched if not p["built"])
    tops = [(x, p) for x, p in matched if x["top"]]
    return {
        "xml": len(xs),
        "pdf": len(own),
        "built": sum(1 for p in ps if p["built"]),
        "recall": round(len(matched) / len(xs), 3) if xs else None,
        "precision": round(own_matched / len(own), 3) if own else None,
        "level_agree": round(sum(1 for x, p in matched if x["top"] == p["top"]) / len(matched), 3) if matched else None,
        "lane_agree": round(sum(1 for x, p in tops if x["role"] == p["role"]) / len(tops), 3) if tops else None,
        "missing": [x["heading"] for x in xs if not any(x is m for m, _ in matched)][:8],
        "spurious": [p["heading"] for j, p in enumerate(ps) if j not in taken and not p["built"]][:8],
    }


def _title_f1(a: str, b: str) -> float:
    wa, wb = Counter(words(a)), Counter(words(b))
    both = sum((wa & wb).values())
    return round(2 * both / (sum(wa.values()) + sum(wb.values())), 3) if both else 0.0


def _ratio(a: int, b: int) -> float | None:
    return round(min(a, b) / max(a, b), 3) if max(a, b) else None


def compare(pdf: Tree, xml: Tree, xml_bytes: bytes | None = None) -> dict[str, Any]:
    """The PDF's reading against the XML's, as one record; every share is weighted by words
    (shingles), so a long paragraph counts for more than a short one."""
    pu, xu = units_of(pdf), units_of(xml)
    p_index, x_index = _index(pu), _index(xu)

    # -- the XML's prose, looked for in the PDF's reading ---------------------------------------
    total = found = 0
    t_total = t_found = t_faithful = 0.0
    by_lane: dict[str, Counter[str]] = {}
    landed_in: Counter[str] = Counter()
    confusion: Counter[str] = Counter()
    state: Counter[str] = Counter()
    dominant: dict[int, list[tuple[int, float]]] = {}  # PDF unit → the XML paragraphs that lie in it
    missing_text: list[str] = []
    prose = [(i, u) for i, u in enumerate(xu) if u.prose and u.shingles]
    verdicts: dict[int, str] = {}
    for i, u in prose:
        n = len(u.shingles)
        landed = _land(u, p_index)
        got = sum(landed.values())
        same = sum(c for j, c in landed.items() if pu[j].prose and pu[j].role == u.role)
        total, found = total + n, found + got
        nt = sum(u.tokens.values())
        held, held_top = _held(u, landed, pu)
        in_lane = held * (same / got) if got else 0.0
        t_total, t_found, t_faithful = t_total + nt, t_found + held * nt, t_faithful + in_lane * nt
        lane = by_lane.setdefault(u.role, Counter())
        lane.update({"shingles": n, "words": nt, "found": round(held * nt), "faithful": round(in_lane * nt)})
        for j, c in landed.items():
            v = pu[j]
            where = "same lane" if v.prose and v.role == u.role else f"lane {v.role}" if v.prose else "front matter" if v.front or v.kind == "meta" else v.kind if v.kind != "list_item" and v.kind != "paragraph" else f"lane {v.role}"
            landed_in[where] += c
            if v.prose and v.role != u.role:
                confusion[f"{u.role} → {v.role}"] += c
        landed_in["nowhere"] += n - got
        if n >= 8:  # a paragraph long enough to say how it arrived
            top = landed.most_common(1)[0][0] if landed else None
            if held < 0.5:
                verdicts[i] = "missing"
                missing_text.append(u.text[:90])
            elif held_top >= 0.85:
                verdicts[i] = "intact"
                dominant.setdefault(top, []).append((i, held_top))
            else:
                verdicts[i] = "split"
    for j, inside in dominant.items():
        if len(inside) >= 2 and pu[j].kind in ("paragraph", "list_item"):
            for i, _ in inside:
                verdicts[i] = "merged"  # two of the XML's paragraphs in one of the PDF's
    state.update(verdicts.values())
    judged = sum(state.values())

    # -- the PDF's prose, looked for in the XML: what is left is not the paper's -----------------
    p_total = p_found = 0.0
    junk: list[str] = []
    for v in pu:
        if not (v.prose and v.shingles):
            continue
        nt = sum(v.tokens.values())
        held, _ = _held(v, _land(v, x_index), xu)
        p_total, p_found = p_total + nt, p_found + held * nt
        if len(v.shingles) >= 8 and held < 0.3:
            junk.append(v.text[:90])

    # -- the reference list, the citations, the captions, the title ------------------------------
    refs_x = [u for u in xu if u.role == "references" and u.kind in ("list_item", "paragraph") and u.shingles]
    refs_p = [u for u in pu if u.role == "references" and u.kind in ("list_item", "paragraph") and u.shingles]
    r_total = sum(len(u.shingles) for u in refs_x)
    r_found = sum(sum(1 for s in u.shingles if s in p_index) for u in refs_x)
    cx = summarize_citations(*link_citations(xml, xml_bytes))
    cp = summarize_citations(*link_citations(pdf, None))
    caps_x = [u for u in xu if u.kind == "caption" and len(u.shingles) >= 4]
    cap_hit = cap_prose = 0
    for u in caps_x:
        landed = _land(u, p_index)
        n = len(u.shingles)
        in_caption = sum(c for j, c in landed.items() if pu[j].kind == "caption")
        in_prose = sum(c for j, c in landed.items() if pu[j].prose)
        cap_hit += in_caption / n >= 0.6
        cap_prose += in_prose / n >= 0.6

    return {
        "title_same": _title_f1(pdf.title, xml.title) >= 0.9,
        "title_f1": _title_f1(pdf.title, xml.title),
        "xml_words": round(t_total),
        "recall": round(t_found / t_total, 4) if t_total else None,
        "exact": round(found / total, 4) if total else None,
        "faithful": round(t_faithful / t_total, 4) if t_total else None,
        "precision": round(p_found / p_total, 4) if p_total else None,
        "by_lane": {lane: {"words": c["words"], "recall": round(c["found"] / c["words"], 3), "faithful": round(c["faithful"] / c["words"], 3)} for lane, c in sorted(by_lane.items()) if c["words"]},
        "landed": {k: round(v / total, 4) for k, v in landed_in.most_common()} if total else {},
        "confusion": dict(confusion.most_common(6)),
        "paragraphs": {"judged": judged, **{k: round(state[k] / judged, 3) if judged else None for k in ("intact", "split", "merged", "missing")}},
        "missing_text": missing_text[:6],
        "junk": junk[:6],
        "junk_units": len(junk),
        "headings": compare_headings(pdf, xml),
        "references": {"xml": len(refs_x), "pdf": len(refs_p), "ratio": _ratio(len(refs_x), len(refs_p)), "recall": round(r_found / r_total, 3) if r_total else None},
        "citations": {"xml": cx["citations"], "pdf": cp["citations"], "ratio": _ratio(cx["citations"], cp["citations"]), "cited_refs_xml": cx["cited_refs"], "cited_refs_pdf": cp["cited_refs"]},
        "captions": {"xml": len(caps_x), "as_caption": round(cap_hit / len(caps_x), 3) if caps_x else None, "as_prose": round(cap_prose / len(caps_x), 3) if caps_x else None},
    }


# -- over two libraries ---------------------------------------------------------------------------

def pairs_of(pdf_lib: Path, xml_lib: Path) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """The papers one library holds as PDF and the other as JATS, by key."""
    from .library import parsed_papers

    xmls = {r["key"]: r for r in parsed_papers(xml_lib) if r["format"] == "jats"}
    return [(r, xmls[r["key"]]) for r in parsed_papers(pdf_lib) if r["format"] == "pdf" and r["key"] in xmls]


def run(pdf_lib: Path, xml_lib: Path, keys: list[str] | None = None, signals: bool = True) -> list[dict[str, Any]]:
    """Every pair read again from its saved Docling documents (no Docling, no model asked) and
    compared; with `signals`, the harness's measurements of the PDF's reading ride along — they
    are what a confidence score may use, the comparison is what it has to predict."""
    from .confidence import score as confidence_score
    from .confidence import signals as intrinsic
    from .harness import measure, read_paper

    out = []
    for p_row, x_row in pairs_of(pdf_lib, xml_lib):
        if keys and p_row["key"] not in keys:
            continue
        pdf_tree, pdf_kind, _ = read_paper(pdf_lib, p_row)
        xml_tree, xml_kind, xml_bytes = read_paper(xml_lib, x_row)
        rec = {"key": p_row["key"], "pdf_library": Path(pdf_lib).name, "xml_library": Path(xml_lib).name, "title": xml_tree.title, "type": xml_kind.get("type"), **compare(pdf_tree, xml_tree, xml_bytes)}
        rec["intrinsic"] = intrinsic(pdf_tree, pdf_kind)  # what the PDF's reading says of itself (confidence.py)
        rec["confidence"] = confidence_score(rec["intrinsic"])["confidence"]
        if signals:
            rec["signals"] = measure(pdf_tree, p_row["key"], "pdf", p_row["source"], pdf_kind)
            rec["xml_signals"] = {k: v for k, v in measure(xml_tree, x_row["key"], "jats", x_row["source"], xml_kind).items() if k in ("refs", "citations", "cited_refs", "paragraphs", "sections", "nodes", "errors", "warnings")}
        out.append(rec)
    return out


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    def col(path: str) -> list[float]:
        vals = []
        for r in records:
            v: Any = r
            for part in path.split("."):
                v = v.get(part) if isinstance(v, dict) else None
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vals.append(float(v))
        return vals

    faithful = sorted(col("faithful"))
    return {
        "pairs": len(records),
        "titles_same": sum(1 for r in records if r["title_same"]),
        "mean": {k: _mean(col(k)) for k in ("recall", "exact", "faithful", "precision", "paragraphs.intact", "paragraphs.split", "paragraphs.merged", "paragraphs.missing", "headings.recall", "headings.precision", "headings.level_agree", "headings.lane_agree", "references.ratio", "references.recall", "citations.ratio", "captions.as_caption", "captions.as_prose")},
        "faithful_quartiles": [faithful[int(q * (len(faithful) - 1))] for q in (0, 0.1, 0.25, 0.5, 0.75, 1)] if faithful else [],
        "faithful_at_least": {str(t): sum(1 for f in faithful if f >= t) for t in (0.95, 0.9, 0.8, 0.7, 0.5)},
    }


def report(records: list[dict[str, Any]], worst: int = 12) -> str:
    s = summary(records)
    m = s["mean"]
    lines = [
        f"{s['pairs']} papers read from both their PDF and their XML · titles the same {s['titles_same']}/{s['pairs']}",
        f"  text: recall {m['recall']} (spelled the same {m['exact']}) · faithful (same lane) {m['faithful']} · precision {m['precision']}",
        f"  paragraphs: intact {m['paragraphs.intact']} · split {m['paragraphs.split']} · merged {m['paragraphs.merged']} · missing {m['paragraphs.missing']}",
        f"  headings: found {m['headings.recall']} · the PDF's own in the XML {m['headings.precision']} · depth agrees {m['headings.level_agree']} · lane agrees {m['headings.lane_agree']}",
        f"  references: list length agrees {m['references.ratio']} · entries' text found {m['references.recall']} · citation links agree {m['citations.ratio']}",
        f"  captions: read as captions {m['captions.as_caption']} · read as prose {m['captions.as_prose']}",
        f"  faithful, min · p10 · q1 · median · q3 · max: {' · '.join(str(q) for q in s['faithful_quartiles'])}",
        "  papers at least this faithful: " + " · ".join(f"{t}: {n}" for t, n in s["faithful_at_least"].items()),
        "",
        f"least faithful {worst}:",
    ]
    for r in sorted(records, key=lambda r: r["faithful"] if r["faithful"] is not None else -1)[:worst]:
        h = r["headings"]
        lines.append(f"  {r['key']:38} faithful {r['faithful']} recall {r['recall']} precision {r['precision']} · headings {h['recall']}/{h['precision']} · refs {r['references']['pdf']}/{r['references']['xml']} · {str(r['title'])[:48]!r}")
        where = ", ".join([f"{k} {v}" for k, v in r["landed"].items() if k != "same lane"][:4])
        if where:
            lines.append(f"      the rest of the XML's prose: {where}")
    return "\n".join(lines)


def show(r: dict[str, Any]) -> str:
    h = r["headings"]
    out = [
        f"{r['key']} · {r['title']}",
        f"  recall {r['recall']} (spelled the same {r['exact']}) · faithful {r['faithful']} · precision {r['precision']} · title the same: {r['title_same']} ({r['title_f1']})",
        "  where the XML's prose landed: " + ", ".join(f"{k} {v}" for k, v in r["landed"].items()),
        "  by lane: " + ", ".join(f"{lane} {v['faithful']} of {v['words']} words" for lane, v in r["by_lane"].items()),
        "  lanes confused: " + (", ".join(f"{k} ({v})" for k, v in r["confusion"].items()) or "none"),
        f"  paragraphs: {r['paragraphs']}",
        f"  headings: {h['xml']} in the XML, {h['pdf']} in the PDF (+{h['built']} built) · found {h['recall']} · own {h['precision']} · depth {h['level_agree']} · lane {h['lane_agree']}",
        "    missing: " + (" | ".join(h["missing"]) or "none"),
        "    spurious: " + (" | ".join(h["spurious"]) or "none"),
        f"  references: {r['references']} · citations: {r['citations']} · captions: {r['captions']}",
    ]
    if r["missing_text"]:
        out.append("  paragraphs of the XML the PDF's reading does not hold:")
        out += [f"    - {t}" for t in r["missing_text"]]
    if r["junk"]:
        out.append(f"  paragraphs of the PDF's reading the XML does not hold ({r['junk_units']}):")
        out += [f"    - {t}" for t in r["junk"]]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m litrag_parser.pairs", description=__doc__.split("\n\n")[0])
    ap.add_argument("--pdf-lib", required=True, help="the library that holds the papers as PDF")
    ap.add_argument("--xml-lib", required=True, help="the library that holds the same papers as JATS XML")
    ap.add_argument("--json", help="save every pair's record here (keep it outside the repository)")
    ap.add_argument("--show", help="one paper, in full")
    ap.add_argument("--worst", type=int, default=12)
    ap.add_argument("--no-signals", action="store_true", help="the comparison alone, without the harness's measurements of the PDF")
    args = ap.parse_args(argv)
    from . import lanes
    from .library import library_root

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    lanes.configure_from_env(library_root())
    records = run(Path(args.pdf_lib), Path(args.xml_lib), keys=[args.show] if args.show else None, signals=not args.no_signals)
    if not records:
        print("no paper is held as PDF in the one library and as JATS in the other")
        return 1
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": summary(records), "pairs": records}, indent=1, ensure_ascii=False), "utf-8")
    print(show(records[0]) if args.show else report(records, args.worst))
    oracle = lanes.active()
    if oracle is not None and oracle.summary().get("down"):
        # found the hard way: a day of comparisons ran with Ollama stopped, and every heading the rules do not name read as `other` on both sides
        print(f"\nTHE EMBEDDER DID NOT ANSWER ({oracle.summary().get('error')}): headings no rule names read as `other` in both readings, so the lanes compared here are fewer than a reading with Ollama up would give. Start Ollama and run this again before trusting the numbers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
