"""Edges between the nodes of one paper: what a finding was measured by, what a paragraph
cites as a figure.

A query that tests a hypothesis finds results and then wants the methods that produced
them — not the methods section, but the two paragraphs about mechanical testing when the
finding is a modulus. That link is decided here, inside one paper, by elimination:

- the candidates are fixed first: the paper's own methods subsections (the headings
  under its methods section, or its methods paragraphs when there are none), a closed
  list of five to fifteen;
- a finding is a results paragraph (results, results-and-discussion, or a discussion
  paragraph that cites a figure or table);
- three kinds of evidence are tried in order and the one that decided is written on the
  edge: a **pointer** in the finding ("see Section 2.3"); **terms** a single candidate
  owns — words and word pairs that occur in that candidate and in no other, such as
  "compressive modulus" or "calcein", counted per paper with no model; and, last,
  **similarity** between the finding and the candidates from the embedder in
  `meaning.py`, taken only when the nearest clearly beats the next and stored as a
  verdict so a rebuild replays it. A finding that cites a figure reaches the figure's
  caption, and the caption's terms may point to the method (`caption`), which is how a
  terse finding still finds its way.
- a finding no evidence can place stays unlinked, and the count says so.

A paragraph that reports a modulus and a swelling ratio rested on two methods and gets
two edges. Every edge is a row (`edges`) a person can read and delete; pointers and
terms are pure functions of the rows, similarity replays from the oracle's store, so a
rebuild gives the same set of edges. Similarity is off unless `LITRAG_EDGES_SIMILARITY=on`:
measured against the findings whose pointer names their method, it agreed 3 times in 7,
below the gate for an edge on its own. Nothing here calls a generative model.

    uv run --project parser python -m litrag_parser.edges --measure --lib ~/.protracker/library/looped-ligament [--lib …]
        on the findings whose pointer names their method, hide the pointer and report how
        often terms and similarity agree with it — the gate before either is trusted alone
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .tree import Node, Tree

FINDING_LANES = {"results", "results-discussion"}
MIN_FINDING_WORDS = 8
SIMILARITY_THRESHOLD, SIMILARITY_MARGIN = 0.6, 0.05

_SECTION_POINTER = re.compile(r"(?:\b[Ss]ec(?:t(?:ion)?s?)?\.?|§)\s*(\d+(?:\.\d+)+)((?:\s*(?:,|and|&|–|-)\s*\d+(?:\.\d+)+)*)")
_MORE_NUMBERS = re.compile(r"\d+(?:\.\d+)+")
_FIGURE_REF = re.compile(r"\b(Fig(?:ure)?s?|Tables?|Schemes?)\.?\s*(S?\d+)((?:[a-z]?\s*(?:,|and|&|–|-)\s*S?\d+)*)", re.I)
_MORE_FIGURES = re.compile(r"S?\d+", re.I)
_CAPTION_HEAD = re.compile(r"^\s*(Fig(?:ure)?|Table|Scheme)\.?\s*(S?\d+)", re.I)
_NUMBERING = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+")
_TOKEN = re.compile(r"[a-z][a-z0-9\-]{3,}")
_STOP = frozenset(
    "that this with from were which their these than then have been also into over under between after before during while where when there here each both such same other more most less only very well used using use via per about above below within without through against toward towards among along across because however therefore thus although whereas whether either neither respectively significantly significant compared comparison observed obtained shown showed shows show found results result figure figures table tables data study studies group groups sample samples value values level levels increase increased decrease decreased higher lower high low approximately different difference differences similar similarly following followed further first second third according based method methods analysis analyses measured measurement measurements determined determination performed prepared preparation described".split()
)
@dataclass(frozen=True)
class Edge:
    paper: str
    src: str
    dst: str
    kind: str  # measured_by | cites_figure
    evidence: str  # pointer | caption | terms | similarity | (cites_figure: mention)
    detail: str
    score: float


# -- what can be linked ---------------------------------------------------------------------------


from .tree import _descendants as _walk  # noqa: E402 - the same pre-order walk the tree builder uses


def _words(text: str) -> list[str]:
    return [w for w in _TOKEN.findall(text.lower()) if w not in _STOP]


def _terms(text: str) -> set[str]:
    """Words of four letters or more that are not function words, and their pairs."""
    words = _words(text)
    out = set(words)
    out.update(f"{a} {b}" for a, b in zip(words, words[1:]))
    return out


def method_candidates(tree: Tree) -> list[tuple[Node, str]]:
    """The paper's methods subsections as (node, text) — the heading and every paragraph
    under it — or its methods paragraphs when the section has no subheadings."""
    tops = [n for n in tree.root.children if n.type == "section" and n.role == "methods"]
    out: list[tuple[Node, str]] = []
    for top in tops:
        subs = [c for c in top.children if c.type == "section"]
        if subs:
            # in the paper's order; a paragraph before the first subsection is the section's preamble
            # ("All reagents were from Sigma"), which every method shares and none owns
            seen_sub = False
            for c in top.children:
                if c.type == "section":
                    seen_sub = True
                    text = " ".join([c.heading or "", *(x.text for x in _walk(c) if x.type in ("paragraph", "list_item", "caption") and x.text)])
                    out.append((c, text))
                elif seen_sub and c.type == "paragraph" and len(c.text.split()) >= MIN_FINDING_WORDS:
                    out.append((c, c.text))
        else:
            for p in (x for x in _walk(top) if x.type == "paragraph" and len(x.text.split()) >= MIN_FINDING_WORDS):
                out.append((p, p.text))
    return out


def findings(tree: Tree) -> list[Node]:
    """Results paragraphs, and discussion paragraphs that cite a figure or table."""
    out = []
    for n in _walk(tree.root):
        if n.type != "paragraph" or len(n.text.split()) < MIN_FINDING_WORDS:
            continue
        if n.role in FINDING_LANES or (n.role == "discussion" and _FIGURE_REF.search(n.text)):
            out.append(n)
    return out


def figures(tree: Tree) -> dict[str, Node]:
    """Figure and table nodes by the number their caption opens with: "figure 3", "table s1"."""
    out: dict[str, Node] = {}
    for n in _walk(tree.root):
        if n.type in ("picture", "table"):
            for cap in n.children:
                if cap.type == "caption":
                    m = _CAPTION_HEAD.match(cap.text or "")
                    if m:
                        out.setdefault(f"{_figure_kind(m.group(1))} {m.group(2).lower()}", n)
    return out


def _figure_kind(word: str) -> str:
    w = word.lower()
    return "figure" if w.startswith("fig") else "table" if w.startswith("tab") else "scheme"


def _document_frequency(tree: Tree) -> dict[str, int]:
    """In how many of the paper's blocks each word and each word pair occurs."""
    df: Counter[str] = Counter()
    for n in _walk(tree.root):
        if n.text and n.type in ("paragraph", "list_item", "caption"):
            df.update(_terms(n.text))
    return dict(df)


COMMON_DF = 5  # a word in five or more of the paper's blocks is the paper's subject, not a method's mark
RARE_PAIR_DF = 4  # a word pair in four blocks or fewer is a mark even when a method says it once


def _ownership(cands: list[tuple[Node, str]], df: dict[str, int] | None = None) -> dict[str, int]:
    """Which candidate alone owns each of its *marks*, the terms that are that method's own
    subject: a word or pair from its heading, a word pair its text repeats, or a word pair
    said once that the paper says in few blocks ("compressive modulus"). A mark two
    candidates share belongs to none; a pair the whole paper uses ("time points",
    "supplementary material") is nobody's mark, which is what over-linked before."""
    seen: dict[str, int] = {}
    counts: Counter[str] = Counter()
    for i, (node, text) in enumerate(cands):
        marks = set()
        words = _words(text)
        common = lambda t: (df or {}).get(t, 0) >= COMMON_DF  # noqa: E731
        if node.type == "section" and node.heading:
            # a heading's word is a mark when the body uses it too ("Swelling" over "Swelling ratio was…");
            # a heading's pair is one on its own. The candidate's text opens with its heading: the body is the rest.
            body_text = text[len(node.heading):] if text.startswith(node.heading) else text
            body = set(_words(body_text))
            marks.update(t for t in _terms(_NUMBERING.sub("", node.heading)) if len(t) >= 5 and (" " in t or t in body) and not common(t))
        pairs = Counter(f"{a} {b}" for a, b in zip(words, words[1:]))
        marks.update(t for t, n in pairs.items() if (n >= 2 or (df or {}).get(t, 0) <= RARE_PAIR_DF) and not common(t))
        for t in marks:
            counts[t] += 1
            seen[t] = i
    return {t: i for t, i in seen.items() if counts[t] == 1}


def _by_terms(text: str, owned: dict[str, int], df: dict[str, int] | None = None) -> list[tuple[int, list[str]]]:
    """Candidates a text names by marks only they own, in candidate order — a word pair, or a
    heading word that is not one of the paper's commonplaces ("scaffold" in a scaffold paper
    names nothing)."""
    hits: dict[int, list[str]] = {}
    for t in sorted(_terms(text)):
        i = owned.get(t)
        if i is not None and (" " in t or (df or {}).get(t, 0) < COMMON_DF):
            hits.setdefault(i, []).append(t)
    return [(i, sorted(ts, key=lambda t: (" " not in t, t))) for i, ts in sorted(hits.items())]


# -- the edges ------------------------------------------------------------------------------------------


def link_edges(tree: Tree, key: str, oracle: Any = None) -> list[Edge]:
    """Every edge of one paper: `cites_figure` for each figure or table a paragraph names,
    `measured_by` for each finding whose method the evidence names."""
    edges: list[Edge] = []
    figs = figures(tree)
    cands = method_candidates(tree)
    numbered = {}
    for i, (node, _) in enumerate(cands):
        m = _NUMBERING.match(node.heading or "") if node.type == "section" else None
        if m:
            numbered[m.group(1)] = i
    df = _document_frequency(tree)
    owned = _ownership(cands, df) if len(cands) > 1 else {}

    cited_figures: dict[str, list[Node]] = {}
    for n in _walk(tree.root):
        if n.type != "paragraph" or not n.text:
            continue
        seen: set[str] = set()
        for m in _FIGURE_REF.finditer(n.text):
            kind = _figure_kind(m.group(1))
            for num in [m.group(2), *_MORE_FIGURES.findall(m.group(3) or "")]:
                target = figs.get(f"{kind} {num.lower()}")
                if target is not None and target.node_id != n.node_id and target.node_id not in seen:
                    seen.add(target.node_id)
                    edges.append(Edge(key, n.node_id, target.node_id, "cites_figure", "mention", f"{m.group(1)} {num}", 1.0))
                    cited_figures.setdefault(n.node_id, []).append(target)

    if not cands:
        return edges
    for f in findings(tree):
        linked: set[int] = set()
        # 1. a pointer: "(see Section 2.3)", "Sections 2.2 and 2.3"; "2.3.1" reaches 2.3 when only 2.3 is a candidate
        for m in _SECTION_POINTER.finditer(f.text):
            for num in [m.group(1), *_MORE_NUMBERS.findall(m.group(2) or "")]:
                i = _numbered_candidate(num, numbered)
                if i is not None and i not in linked:
                    linked.add(i)
                    edges.append(Edge(key, f.node_id, cands[i][0].node_id, "measured_by", "pointer", f"Section {num}", 1.0))
        if linked:
            continue
        # 2. terms only one candidate owns
        for i, ts in _by_terms(f.text, owned, df):
            linked.add(i)
            edges.append(Edge(key, f.node_id, cands[i][0].node_id, "measured_by", "terms", ", ".join(ts[:4]), 0.9))
        if linked:
            continue
        # 2b. the figure it cites: the caption's terms
        for fig in cited_figures.get(f.node_id, []):
            caption = " ".join(c.text for c in fig.children if c.type == "caption")
            for i, ts in _by_terms(caption, owned, df):
                if i not in linked:
                    linked.add(i)
                    edges.append(Edge(key, f.node_id, cands[i][0].node_id, "measured_by", "caption", f"{_caption_label(fig)}: {', '.join(ts[:4])}", 0.8))
        if linked:
            continue
        # 3. similarity, only when asked for (LITRAG_EDGES_SIMILARITY=on) and an oracle is there:
        # the nearest candidate, if clearly nearest. Off by default: measured at 3 of 7 against
        # pointers (NOTES.md, 2026-09-14), below the gate for an edge on its own.
        if oracle is not None and len(cands) > 1 and similarity_on():
            v = oracle.which(f.text[:600], [t[:600] for _, t in cands], threshold=SIMILARITY_THRESHOLD, margin=SIMILARITY_MARGIN)
            if v.sure:
                i = int(v.name)
                edges.append(Edge(key, f.node_id, cands[i][0].node_id, "measured_by", "similarity", f"cosine {v.score} margin {v.margin}", float(v.score)))
    return edges


def similarity_on() -> bool:
    """`LITRAG_EDGES_SIMILARITY=on`: resemblance may make an edge where no pointer, mark or
    caption did. Off until it passes its gate against pointers a person has confirmed."""
    return os.environ.get("LITRAG_EDGES_SIMILARITY", "off").strip().lower() in ("on", "1", "true", "yes")


def _numbered_candidate(num: str, numbered: dict[str, int]) -> int | None:
    """The candidate a section number names, or the deepest prefix of it that is one:
    "2.3.1" reaches "2.3" when the candidates stop at two levels."""
    parts = num.split(".")
    for k in range(len(parts), 1, -1):
        i = numbered.get(".".join(parts[:k]))
        if i is not None:
            return i
    return None


def _caption_label(fig: Node) -> str:
    for c in fig.children:
        if c.type == "caption":
            m = _CAPTION_HEAD.match(c.text or "")
            if m:
                return f"{m.group(1)} {m.group(2)}"
    return fig.type


def summarize(tree: Tree, edges: list[Edge]) -> dict[str, int]:
    """How the findings fared: how many, linked by which evidence, unlinked."""
    fs = findings(tree)
    by = Counter(e.evidence for e in edges if e.kind == "measured_by")
    linked = {e.src for e in edges if e.kind == "measured_by"}
    return {
        "findings": len(fs),
        "linked": sum(1 for f in fs if f.node_id in linked),
        "unlinked": sum(1 for f in fs if f.node_id not in linked),
        "pointer": by.get("pointer", 0),
        "terms": by.get("terms", 0),
        "caption": by.get("caption", 0),
        "similarity": by.get("similarity", 0),
        "cites_figure": sum(1 for e in edges if e.kind == "cites_figure"),
        "candidates": len(method_candidates(tree)),
    }


# -- the measurement ------------------------------------------------------------------------------------


def measure(libs: list[Path], oracle: Any = None) -> dict[str, Any]:
    """On every finding whose pointer names its method, hide the pointer and ask terms and
    similarity: how often each agrees with the pointer, and how often each answers at all."""
    import json

    from .library import parsed_papers
    from .recover import recover_from_pdf
    from .tree import build_tree

    truth = 0
    agree: Counter[str] = Counter()
    answered: Counter[str] = Counter()
    papers_with_pointers = 0
    totals: Counter[str] = Counter()
    for lib in libs:
        for row in parsed_papers(lib):
            doc = json.loads(row["raw"].read_text("utf-8"))
            recover_from_pdf(doc, row["source"] if row["format"] == "pdf" else None)
            tree = build_tree(doc, row["key"])
            edges = link_edges(tree, row["key"], oracle)
            s = summarize(tree, edges)
            for k, v in s.items():
                totals[k] += v
            pointed = [e for e in edges if e.evidence == "pointer"]
            if not pointed:
                continue
            papers_with_pointers += 1
            cands = method_candidates(tree)
            df = _document_frequency(tree)
            owned = _ownership(cands, df) if len(cands) > 1 else {}
            by_node = {n.node_id: n for n in _walk(tree.root)}
            targets: dict[str, set[str]] = {}
            for e in pointed:
                targets.setdefault(e.src, set()).add(e.dst)
            for src, dsts in targets.items():
                f = by_node[src]
                hidden = _SECTION_POINTER.sub(" ", f.text)
                truth += 1
                hits = {cands[i][0].node_id for i, _ in _by_terms(hidden, owned, df)}
                if hits:
                    answered["terms"] += 1
                    agree["terms"] += int(bool(hits & dsts))
                if oracle is not None and len(cands) > 1:
                    v = oracle.which(hidden[:600], [t[:600] for _, t in cands], threshold=SIMILARITY_THRESHOLD, margin=SIMILARITY_MARGIN)
                    if v.sure:
                        answered["similarity"] += 1
                        agree["similarity"] += int(cands[int(v.name)][0].node_id in dsts)
    return {
        "papers_with_pointers": papers_with_pointers,
        "pointed_findings": truth,
        "terms": {"answered": answered["terms"], "agree": agree["terms"], "precision": round(agree["terms"] / answered["terms"], 3) if answered["terms"] else None},
        "similarity": {"answered": answered["similarity"], "agree": agree["similarity"], "precision": round(agree["similarity"] / answered["similarity"], 3) if answered["similarity"] else None},
        "corpus": dict(totals),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="litrag_parser.edges", description=__doc__.split("\n\n")[0])
    ap.add_argument("--lib", action="append", default=[], help="a library directory (repeatable)")
    ap.add_argument("--measure", action="store_true", help="agreement of terms and similarity with the findings whose pointer names their method")
    args = ap.parse_args(argv)
    if not args.measure or not args.lib:
        ap.print_help()
        return 2
    from . import lanes

    libs = [Path(l).expanduser() for l in args.lib]
    lanes.configure_from_env(libs[0].resolve().parent)
    r = measure(libs, lanes.active())
    c = r["corpus"]
    print(f"{r['pointed_findings']} findings with a pointer in {r['papers_with_pointers']} papers")
    for k in ("terms", "similarity"):
        m = r[k]
        print(f"  {k:<11} answered {m['answered']:>4} of {r['pointed_findings']} · agreed with the pointer {m['agree']:>4} · precision {m['precision']}")
    print(f"corpus: findings {c.get('findings', 0)} · linked {c.get('linked', 0)} · unlinked {c.get('unlinked', 0)} · edges by pointer {c.get('pointer', 0)}, by terms {c.get('terms', 0)}, through captions {c.get('caption', 0)}, by similarity {c.get('similarity', 0)} · figure mentions {c.get('cites_figure', 0)} · method candidates {c.get('candidates', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
