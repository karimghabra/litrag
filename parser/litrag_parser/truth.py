"""The truth for the finding→method links, and the linker measured against it.

`edges.py` draws a `measured_by` edge from a finding to the methods subsection its evidence
names. Until a person has said which subsection each finding was in fact measured by, how
often those edges are right is a guess: the only truth was thirteen findings whose text
carried a pointer (NOTES.md, 2026-09-14). This is where the person says it, and where the
edges are scored against what was said.

- **The queue** (`queue`): findings to label, from the stored rows — no Docling, no model.
  Spread across papers, a few findings each (a cap per paper, so a paper's methods are read
  once for several findings), and across DOI registrant prefixes (publishers set their
  methods out differently); each paper's findings taken in turn by what linked them — a
  pointer, terms, a caption, similarity, or nothing — so linked and unlinked are mixed; the
  findings already labelled left out. The same seed gives the same list.
- **The labels** (`link_labels`, a table of the library store): per finding, each candidate
  method `yes` or `no`, or the one verdict `none` — no method in this paper — and, if the
  person names it, the paragraph inside a method the finding rests on. They are a person's
  work, not derived from the paper: no foreign key, and a reread or a rebuild, which replace
  the nodes, leave them. Each row keeps the finding's first words and the method's heading,
  so when a rebuild renumbers the nodes it is found again (`anchor`): by its id when that
  node still says the same thing, else by its words in the same paper, else by resemblance
  only when the nearest paragraph is near enough and clearly nearer than the next. A label
  that cannot be found again is counted, never guessed.
- **The measure** (`measure`): of the edges on labelled findings, per evidence kind, how
  many point at a method labelled `yes` (precision); of the methods labelled `yes`, how many
  an edge reaches (recall); the findings with a `yes` method and no edge (misses); the
  findings labelled `none` that got an edge anyway (false links); and, where a paragraph was
  named, how often a paragraph chooser names the same one — the method's first paragraph
  unless another chooser is given; the command line and the worker's `truth` op give query
  hydration's (`retrieve.best_paragraph`) — with the first paragraph's score beside it.

- **The model's labels** (`model_labels`, `labeller.py`): the local model answers the same
  question for the findings the queue would offer; the queue offers them first, never saying
  what it answered, so a person's labels on them are its audit (`agreement`). Once `AUDIT_MIN`
  are audited at `AGREEMENT_GATE`, `report` measures the edges again with them for the
  findings no person labelled; until then they are compared, never counted.

    uv run --project parser python -m litrag_parser.truth --lib DIR [--lib …] --measure [--json]
    uv run --project parser python -m litrag_parser.truth --lib DIR [--lib …] --export labels.jsonl
    uv run --project parser python -m litrag_parser.truth --lib DIR [--lib …] --import labels.jsonl
        a label set kept outside the library, moved between machines; an import finds every
        label's finding and method again in the library it lands in
"""

from __future__ import annotations

import argparse
import difflib
import getpass
import json
import os
import random
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .edges import findings as _findings
from .edges import method_candidates as _method_candidates
from .tree import Node, Tree
from .tree import _descendants as _walk

EVIDENCE = ("pointer", "terms", "caption", "similarity")  # the order edges.py tries them in
VERDICTS = ("yes", "no", "none")
TEXT_CHARS = 200  # what a label keeps of a finding, a method paragraph or a chosen paragraph
PER_PAPER = 5  # findings a paper gives the queue, those already labelled included
RESEMBLANCE, RESEMBLANCE_MARGIN = 0.9, 0.05  # a label found again by resemblance: near enough, and clearly nearer than the next
COLUMNS = ("paper", "finding", "finding_text", "method", "method_heading", "paragraph", "paragraph_text", "verdict", "by", "at")
TABLES = ("link_labels", "model_labels")  # a person's labels; the local model's (labeller.py), the same shape
AUDIT_MIN, AGREEMENT_GATE = 25, 0.9  # the model's labels stand once a person has labelled this many of its findings and agrees on this share

ParagraphChooser = Callable[[sqlite3.Connection, str, str], "str | None"]


# -- a paper from its rows ----------------------------------------------------------------------------------


def tree_of(conn: sqlite3.Connection, key: str) -> Tree | None:
    """A paper's tree from its `nodes` rows — enough of it for `edges.method_candidates` and
    `edges.findings`, which then answer as they did over the tree the rows were saved from."""
    rows = conn.execute(
        "SELECT node_id, parent, ordinal, depth, type, label, level, role, heading, ancestry, text, page FROM nodes WHERE paper = ? ORDER BY depth, ordinal",
        (key,),
    ).fetchall()
    by_id: dict[str, Node] = {}
    root: Node | None = None
    for r in rows:
        n = Node(
            node_id=r["node_id"], parent=r["parent"], ordinal=r["ordinal"], depth=r["depth"], type=r["type"], label=r["label"],
            level=r["level"], role=r["role"], heading=r["heading"], ancestry=json.loads(r["ancestry"] or "[]"), text=r["text"] or "",
            page=r["page"], bbox=None, self_ref=None,
        )
        by_id[n.node_id] = n
        if r["parent"] is None and root is None:
            root = n
    if root is None:
        return None
    for r in rows:  # by depth, then by ordinal: each parent's children arrive in their order
        if r["parent"] is not None and r["parent"] in by_id:
            by_id[r["parent"]].children.append(by_id[r["node_id"]])
    return Tree(title=key, pages=[], root=root, roles={}, has_methods=False)


@dataclass
class _Paper:
    key: str
    title: str
    doi: str | None
    tree: Tree
    by_id: dict[str, Node]
    candidates: list[Node]
    findings: list[Node]
    edges: dict[str, list[dict[str, Any]]]  # finding → its measured_by edges, in the linker's order
    norm: dict[str, str] = field(default_factory=dict)
    _prose: list[Node] | None = None

    def text(self, n: Node) -> str:
        if n.node_id not in self.norm:
            self.norm[n.node_id] = _norm(n.text)
        return self.norm[n.node_id]

    def prose(self, under: Node | None = None) -> list[Node]:
        """The paragraphs and list items, in reading order: the paper's, or those under one node."""
        if under is not None:
            return [x for x in _walk(under) if x.type in ("paragraph", "list_item")]
        if self._prose is None:
            self._prose = [x for x in _walk(self.tree.root) if x.type in ("paragraph", "list_item")]
        return self._prose


class _Papers:
    """Each paper read from its rows once per call, and only when asked for."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.info = {r["key"]: dict(r) for r in conn.execute("SELECT key, title, doi, status FROM papers")}
        self._cache: dict[str, _Paper | None] = {}

    def get(self, key: str) -> _Paper | None:
        if key not in self._cache:
            self._cache[key] = self._read(key)
        return self._cache[key]

    def _read(self, key: str) -> _Paper | None:
        tree = tree_of(self.conn, key)
        if tree is None:
            return None
        edges: dict[str, list[dict[str, Any]]] = {}
        for r in self.conn.execute("SELECT src, dst, evidence, detail FROM edges WHERE paper = ? AND kind = 'measured_by' ORDER BY rowid", (key,)):
            edges.setdefault(r["src"], []).append({"dst": r["dst"], "evidence": r["evidence"], "detail": r["detail"]})
        info = self.info.get(key) or {}
        return _Paper(
            key=key, title=info.get("title") or key, doi=info.get("doi"), tree=tree,
            by_id={n.node_id: n for n in _walk(tree.root)},
            candidates=[n for n, _ in _method_candidates(tree)], findings=_findings(tree), edges=edges,
        )


def registrant(doi: str | None) -> str:
    """A DOI's registrant prefix — 10.3390 is MDPI, 10.1016 Elsevier: the publisher, near enough."""
    m = re.match(r"\s*(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9})/", doi or "", re.I)
    return m.group(1) if m else "no-doi"


def evidence_of(edges: list[dict[str, Any]]) -> str:
    """What linked a finding: the first kind of evidence edges.py tried that made one of its
    edges, or `unlinked`."""
    kinds = {e["evidence"] for e in edges}
    return next((k for k in EVIDENCE if k in kinds), next(iter(sorted(kinds)), "unlinked"))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip().lower()


_NUMBER = re.compile(r"^\s*(?:[ivx]+|\d+(?:\.\d+)*|[a-z])[.)]?\s+", re.I)


def _unnumbered(heading: str) -> str:
    return _NUMBER.sub("", heading)


def _method_label(n: Node) -> str:
    """What a label keeps to find a method again: its heading, or a methods paragraph's first words."""
    return (n.heading or "") if n.type == "section" else (n.text or "")[:TEXT_CHARS]


# -- the queue ----------------------------------------------------------------------------------------------


def _candidate(n: Node, edge: dict[str, Any] | None) -> dict[str, Any]:
    paragraphs = [] if n.type != "section" else [
        {"node_id": p.node_id, "text": p.text[:TEXT_CHARS]} for p in _walk(n) if p is not n and p.type in ("paragraph", "list_item") and p.text
    ]
    return {
        "node_id": n.node_id, "type": n.type, "heading": n.heading if n.type == "section" else None,
        "text": "" if n.type == "section" else n.text[:TEXT_CHARS], "paragraphs": paragraphs,
        "edge": {"evidence": edge["evidence"], "detail": edge["detail"]} if edge else None,
    }


def _item(p: _Paper, f: Node, labels_now: list[dict[str, Any]]) -> dict[str, Any]:
    edges = p.edges.get(f.node_id, [])
    by_dst = {e["dst"]: e for e in edges}
    candidates = [_candidate(c, by_dst.get(c.node_id)) for c in p.candidates]
    known = {c.node_id for c in p.candidates}
    for e in edges:  # an edge to a node that is not (or no longer) a candidate is still a claim to judge
        if e["dst"] not in known and e["dst"] in p.by_id:
            known.add(e["dst"])
            candidates.append({**_candidate(p.by_id[e["dst"]], e), "extra": True})
    return {
        "paper": p.key, "title": p.title, "doi": p.doi, "prefix": registrant(p.doi), "evidence": evidence_of(edges),
        "finding": {"node_id": f.node_id, "text": f.text, "ancestry": f.ancestry, "page": f.page, "role": f.role},
        "candidates": candidates, "edges": edges, "labels": labels_now,
    }


def _turns(p: _Paper, seed: int) -> list[Node]:
    """A paper's findings in the order the queue offers them: one of each kind of evidence in
    turn (unlinked counting as a kind), shuffled within each kind by the seed and the paper.
    Computed over every finding, labelled or not, so labelling some does not reshuffle the rest."""
    rng = random.Random(f"{seed}:{p.key}")
    groups: dict[str, list[Node]] = {}
    for f in p.findings:
        groups.setdefault(evidence_of(p.edges.get(f.node_id, [])), []).append(f)
    for g in groups.values():
        rng.shuffle(g)
    kinds = sorted(groups)
    rng.shuffle(kinds)
    out: list[Node] = []
    while any(groups.values()):
        for k in kinds:
            if groups[k]:
                out.append(groups[k].pop(0))
    return out


def queue(conn: sqlite3.Connection, n: int = 100, seed: int = 0, per_paper: int = PER_PAPER, finding: str | None = None) -> dict[str, Any]:
    """The next findings to label: `{items, labelled, papers, audit}`, each item
    `{paper, title, doi, prefix, evidence, finding: {node_id, text, ancestry, page, role},
    candidates: [{node_id, type, heading, text, paragraphs: [{node_id, text}], edge, extra?}],
    edges: [{dst, evidence, detail}], labels: [{method, verdict, paragraph}]}` — the candidates
    in `edges.method_candidates` order.

    Papers are visited one prefix after another (the prefixes and each prefix's papers in an
    order the seed fixes), each giving up to `per_paper` findings — its labelled ones counted
    — until there are `n`. The findings the local model labelled and no person has come first
    (`audit` of them), so a person's first labels are its audit; an item never says what the
    model answered. `finding` asks for that one finding's item instead, labelled or not, with
    the labels it has."""
    papers = _Papers(conn)
    anchored = anchor(conn, papers)
    labelled: dict[str, list[dict[str, Any]]] = {}
    for a in anchored:
        labelled.setdefault(a["finding_now"] or a["finding"], []).append(a)
    if finding:
        row = conn.execute("SELECT paper FROM nodes WHERE node_id = ?", (finding,)).fetchone()
        p = papers.get(row["paper"]) if row else None
        if p is None or finding not in p.by_id:
            raise ValueError(f"no node {finding!r}")
        return {"items": [_item(p, p.by_id[finding], _labels_now(labelled.get(finding, [])))], "labelled": len(labelled), "papers": 1, "audit": 0}
    audit = _audit_first(conn, papers, labelled, n)
    drawn = audit + (draw(papers, n - len(audit), seed, per_paper, labelled, skip={f.node_id for _, f in audit}) if len(audit) < n else [])
    return {"items": [_item(p, f, []) for p, f in drawn], "labelled": len(labelled), "papers": len({p.key for p, _ in drawn}), "audit": len(audit)}


def _audit_first(conn: sqlite3.Connection, papers: _Papers, labelled: dict[str, Any], n: int) -> list[tuple[_Paper, Node]]:
    """The findings the local model labelled and no person has, in the order it labelled them
    (the queue's own order), so that a person's first labels are its audit."""
    out: list[tuple[_Paper, Node]] = []
    seen: set[str] = set()
    for a in anchor(conn, papers, table="model_labels"):
        fid = a["finding_now"]
        if fid is None or fid in labelled or fid in seen or len(out) >= n:
            continue
        seen.add(fid)
        p = papers.get(a["paper"])
        if p is not None and p.candidates and fid in p.by_id:
            out.append((p, p.by_id[fid]))
    return out


def draw(papers: _Papers, n: int, seed: int = 0, per_paper: int = PER_PAPER, labelled: dict[str, Any] | None = None, skip: set[str] | None = None) -> list[tuple[_Paper, Node]]:
    """The queue's draw: up to `n` findings, papers visited one prefix after another, each giving
    up to `per_paper` findings in `_turns` order. A finding in `labelled` (its rows, by finding)
    is left out and counted against its paper's cap; one in `skip` is only left out. With
    nothing labelled, the findings a person would be offered from scratch — what the model labels."""
    labelled = labelled or {}
    skip = skip or set()
    done: Counter[str] = Counter()
    for fid, rows in labelled.items():
        done[rows[0]["paper"]] += 1
    rng = random.Random(seed)
    strata: dict[str, list[str]] = {}
    for key in sorted(papers.info):
        if papers.info[key].get("status") == "parsed":
            strata.setdefault(registrant(papers.info[key].get("doi")), []).append(key)
    order = sorted(strata)
    for prefix in order:
        rng.shuffle(strata[prefix])
    rng.shuffle(order)
    pending = {prefix: list(strata[prefix]) for prefix in order}
    items: list[tuple[_Paper, Node]] = []
    while len(items) < n and any(pending.values()):
        for prefix in order:
            if len(items) >= n:
                break
            if not pending[prefix]:
                continue
            key = pending[prefix].pop(0)
            room = per_paper - done[key]
            p = papers.get(key) if room > 0 else None
            if p is None or not p.candidates:
                continue
            room -= sum(f.node_id in skip for f in p.findings)  # offered already: they count against the cap too
            fresh = [f for f in _turns(p, seed) if f.node_id not in labelled and f.node_id not in skip][: max(room, 0)]
            items.extend((p, f) for f in fresh)
    return items[:n]


def _labels_now(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"method": r["method_now"] if r["method_now"] is not None else r["method"], "verdict": r["verdict"], "paragraph": r["paragraph_now"], "by": r["by"], "at": r["at"]} for r in rows]


# -- labels -------------------------------------------------------------------------------------------------


def _labeller() -> str:
    try:
        return os.environ.get("LITRAG_LABELLER") or getpass.getuser()
    except Exception:  # noqa: BLE001 — no user name to be had: the label still counts
        return ""


def _within(p: _Paper, ancestor: str, node_id: str) -> bool:
    n = p.by_id.get(node_id)
    while n is not None:
        if n.node_id == ancestor:
            return True
        n = p.by_id.get(n.parent) if n.parent else None
    return False


def save_labels(conn: sqlite3.Connection, finding: str, labels: list[dict[str, Any]], *, by: str | None = None, at: str | None = None) -> list[dict[str, Any]]:
    """A finding's labels, replacing the ones it had (an empty list clears them): each
    `{method, verdict, paragraph?}` — `yes` or `no` for a method of the same paper, or one
    `{verdict: "none"}` for no method in this paper; `paragraph` only on a `yes`, a paragraph
    inside that method. Labels saved under the finding's id before a rebuild moved it are
    replaced too. Returns the rows as stored."""
    from .library import now_iso

    row = conn.execute("SELECT paper FROM nodes WHERE node_id = ?", (finding,)).fetchone()
    if row is None:
        raise ValueError(f"no node {finding!r}")
    papers = _Papers(conn)
    p = papers.get(row["paper"])
    assert p is not None
    f = p.by_id[finding]
    stamp, who = at or now_iso(), by if by is not None else _labeller()
    rows: dict[str, tuple[Any, ...]] = {}
    for label in labels:
        verdict = str(label.get("verdict") or "")
        if verdict not in VERDICTS:
            raise ValueError(f"a verdict is yes, no or none, not {verdict!r}")
        method = "" if verdict == "none" else str(label.get("method") or "")
        paragraph = str(label.get("paragraph") or "") or None
        m = p.by_id.get(method) if method else None
        if verdict != "none" and m is None:
            raise ValueError(f"no method {method!r} in {p.key}")
        if paragraph and (verdict != "yes" or paragraph not in p.by_id or not _within(p, method, paragraph)):
            raise ValueError(f"{paragraph!r} is not a paragraph of the method {method!r} labelled yes")
        para = p.by_id[paragraph] if paragraph else None
        rows[method] = (p.key, finding, f.text[:TEXT_CHARS], method, _method_label(m) if m else "", paragraph, para.text[:TEXT_CHARS] if para else None, verdict, who, stamp)
    verdicts = Counter(r[7] for r in rows.values())
    if verdicts["none"] and verdicts["yes"]:
        raise ValueError("a finding with no method in its paper has no method labelled yes")
    _write(conn, p, finding, list(rows.values()), papers)
    return [dict(zip(COLUMNS, r)) for r in rows.values()]


def _write(conn: sqlite3.Connection, p: _Paper | None, finding: str, rows: list[tuple[Any, ...]], papers: _Papers, key: str | None = None, table: str = "link_labels") -> None:
    """One finding's label rows in place of the ones it had — under this id, or under an id a
    rebuild has since moved to it."""
    assert table in TABLES
    key = p.key if p is not None else key
    stale = {finding} | {a["finding"] for a in anchor(conn, papers, paper=key, table=table) if a["finding_now"] == finding}
    with conn:
        conn.executemany(f"DELETE FROM {table} WHERE paper = ? AND finding = ?", [(key, s) for s in stale])
        conn.executemany(f"INSERT OR REPLACE INTO {table}({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})", rows)


def labels(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every label as stored, with where it is now: `finding_now`, `method_now` and
    `paragraph_now` (None when it could not be found again) and `how` (id · text · resemblance
    · lost · ambiguous · unread)."""
    return anchor(conn)


# -- finding a label again ---------------------------------------------------------------------------------


def _find(p: _Paper, node_id: str | None, text: str, pool: list[Node]) -> tuple[str | None, str]:
    """A node among `pool` from its id and its first words: the id while it still says them;
    else the one node whose text begins with them (or holds them); else the nearest by
    resemblance when near enough and clearly nearer than the next. `ambiguous` and `lost`
    name nothing."""
    want = _norm(text)
    n = p.by_id.get(node_id or "")
    if n is not None and any(m is n for m in pool) and (p.text(n).startswith(want) or not want):
        return n.node_id, "id"
    if not want:
        return None, "lost"
    for test in (lambda t: t.startswith(want), lambda t: want in t):
        hits = [m for m in pool if test(p.text(m))]
        if len(hits) == 1:
            return hits[0].node_id, "text"
        if len(hits) > 1:
            return None, "ambiguous"
    scored: list[tuple[float, str]] = []
    for m in pool:
        t = p.text(m)[: len(want) + 40]
        sm = difflib.SequenceMatcher(None, want, t, autojunk=False)
        if sm.real_quick_ratio() >= RESEMBLANCE and sm.quick_ratio() >= RESEMBLANCE:
            scored.append((sm.ratio(), m.node_id))
    scored.sort(reverse=True)
    if scored and scored[0][0] >= RESEMBLANCE and (len(scored) == 1 or scored[0][0] - scored[1][0] >= RESEMBLANCE_MARGIN):
        return scored[0][1], "resemblance"
    return None, "lost"


_ID_KIND = re.compile(r"#([a-z_]+)-\d+$")


def _find_method(p: _Paper, node_id: str, heading: str) -> str | None:
    """A method from its id while its heading is the same, else by its heading: the one
    section of the paper with it (a methods one when several), the numbering set aside if
    need be. A methods paragraph — a paper whose methods have no subheadings; its id says
    which it was — is found as a finding is, by its first words, among the methods' prose."""
    m = _ID_KIND.search(node_id)
    if m and m.group(1) in ("paragraph", "list_item"):
        found, _ = _find(p, node_id, heading, [x for x in p.prose() if x.role == "methods"])
        return found
    want = _norm(heading)
    n = p.by_id.get(node_id)
    if n is not None and n.type == "section" and _norm(n.heading or "") == want:
        return n.node_id
    sections = [x for x in p.by_id.values() if x.type == "section" and x.heading]
    for key in (lambda s: s, _unnumbered):
        hits = [x for x in sections if key(_norm(x.heading or "")) == key(want)]
        if len(hits) > 1:
            hits = [x for x in hits if x.role == "methods"]
        if len(hits) == 1:
            return hits[0].node_id
    return None


def anchor(conn: sqlite3.Connection, papers: _Papers | None = None, paper: str | None = None, table: str = "link_labels") -> list[dict[str, Any]]:
    """Every label row (or one paper's) with its finding, method and paragraph as they are now:
    a person's, or with `table="model_labels"` the local model's."""
    assert table in TABLES
    papers = papers or _Papers(conn)
    q = f"SELECT {', '.join(COLUMNS)} FROM {table}" + (" WHERE paper = ?" if paper else "") + " ORDER BY at, paper, finding, method"
    out: list[dict[str, Any]] = []
    for r in conn.execute(q, (paper,) if paper else ()):
        row = dict(r)
        p = papers.get(row["paper"])
        f_now = m_now = p_now = None
        how = "unread"
        if p is not None:
            f_now, how = _find(p, row["finding"], row["finding_text"], p.prose())
            if row["method"] == "":
                m_now = ""
            elif f_now is not None:
                m_now = _find_method(p, row["method"], row["method_heading"])
            if m_now and row["paragraph"]:
                p_now, _ = _find(p, row["paragraph"], row["paragraph_text"] or "", p.prose(p.by_id[m_now]))
        out.append({**row, "finding_now": f_now, "method_now": m_now, "paragraph_now": p_now, "how": how})
    return out


# -- the measure -------------------------------------------------------------------------------------------


def first_paragraph(conn: sqlite3.Connection, finding: str, method: str) -> str | None:
    """The baseline chooser: the method's first paragraph in reading order (the method itself
    when it is a paragraph)."""
    row = conn.execute("SELECT type FROM nodes WHERE node_id = ?", (method,)).fetchone()
    if row is None:
        return None
    if row["type"] in ("paragraph", "list_item"):
        return method

    def first(node_id: str) -> str | None:
        for k in conn.execute("SELECT node_id, type FROM nodes WHERE parent = ? ORDER BY ordinal", (node_id,)).fetchall():
            if k["type"] == "paragraph":
                return str(k["node_id"])
            if k["type"] == "section" and (got := first(str(k["node_id"]))):
                return got
        return None

    return first(method)


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def _hydration_chooser() -> ParagraphChooser:
    """The paragraph query hydration shows for a finding and a method (`retrieve.best_paragraph`),
    which the labels' paragraphs measure."""
    from .retrieve import best_paragraph

    return best_paragraph


SOURCES = ("person", "model", "both")


def _rows(c: sqlite3.Connection, papers: _Papers, source: str) -> list[dict[str, Any]]:
    """The label rows a measure reads: a person's; the model's; or a person's where a finding has
    them and the model's for the rest."""
    if source not in SOURCES:
        raise ValueError(f"labels come from {', '.join(SOURCES)}, not {source!r}")
    if source == "person":
        return anchor(c, papers)
    model = anchor(c, papers, table="model_labels")
    if source == "model":
        return model
    person = anchor(c, papers)
    theirs = {(a["paper"], a["finding_now"] or a["finding"]) for a in person}
    return person + [a for a in model if (a["paper"], a["finding_now"] or a["finding"]) not in theirs]


def measure(conn: sqlite3.Connection | Iterable[sqlite3.Connection], choose_paragraph: ParagraphChooser | None = None, source: str = "person") -> dict[str, Any]:
    """The linker against the labels of one library or several (see the module's docstring):
    a person's (`source`), the model's, or both — a person's where a finding has them.

    Precision counts an edge right when its method is labelled `yes`, wrong when labelled
    `no` or when the finding is labelled `none`, and leaves out (`unjudged`) an edge to a
    method the finding has no label for — a candidate that appeared after it was labelled."""
    conns = [conn] if isinstance(conn, sqlite3.Connection) else list(conn)
    choose = choose_paragraph or first_paragraph
    right: Counter[str] = Counter()
    judged: Counter[str] = Counter()
    unjudged: Counter[str] = Counter()
    reached: Counter[str] = Counter()
    verdicts: Counter[str] = Counter()
    lost: Counter[str] = Counter()
    yes_total = labelled = astray = outside = none_total = rows_total = 0
    papers_seen: set[tuple[int, str]] = set()
    misses: list[dict[str, Any]] = []
    false_links: list[dict[str, Any]] = []
    para_named = para_right = para_first = 0
    for ci, c in enumerate(conns):
        papers = _Papers(c)
        groups: dict[str, dict[str, Any]] = {}
        seen_lost: set[tuple[str, str]] = set()
        for a in _rows(c, papers, source):
            rows_total += 1
            verdicts[a["verdict"]] += 1
            if a["finding_now"] is None:
                if (a["paper"], a["finding"]) not in seen_lost:
                    seen_lost.add((a["paper"], a["finding"]))
                    lost["findings"] += 1
                continue
            g = groups.setdefault(a["finding_now"], {"paper": a["paper"], "text": a["finding_text"], "yes": {}, "no": set(), "none": False})
            if a["verdict"] == "none":
                g["none"] = True
            elif a["method_now"] is None:
                lost["methods"] += 1
            elif a["verdict"] == "yes":
                g["yes"][a["method_now"]] = a["paragraph_now"]
                if a["paragraph"] and a["paragraph_now"] is None:
                    lost["paragraphs"] += 1
            else:
                g["no"].add(a["method_now"])
        for fid, g in groups.items():
            labelled += 1
            papers_seen.add((ci, g["paper"]))
            p = papers.get(g["paper"])
            edges = p.edges.get(fid, []) if p else []
            for e in edges:
                ev = e["evidence"]
                if g["none"] or e["dst"] in g["no"] or e["dst"] in g["yes"]:
                    judged[ev] += 1
                    right[ev] += int(not g["none"] and e["dst"] in g["yes"])
                else:
                    unjudged[ev] += 1
            dsts = {e["dst"]: e["evidence"] for e in edges}
            heading = lambda m: _method_label(p.by_id[m]) if p and m in p.by_id else m  # noqa: E731
            if g["none"]:
                none_total += 1
                if edges:
                    false_links.append({"finding": fid, "paper": g["paper"], "text": g["text"], "edges": [{"method": heading(e["dst"]), "evidence": e["evidence"], "detail": e["detail"]} for e in edges]})
            elif not g["yes"]:
                outside += 1  # every candidate labelled no: the method is not among them
            else:
                yes_total += len(g["yes"])
                for m in g["yes"]:
                    if m in dsts:
                        reached[dsts[m]] += 1
                if not edges:
                    misses.append({"finding": fid, "paper": g["paper"], "text": g["text"], "methods": [heading(m) for m in g["yes"]]})
                elif not set(dsts) & set(g["yes"]):
                    astray += 1
            for m, para in g["yes"].items():
                if para:
                    para_named += 1
                    para_right += int(choose(c, fid, m) == para)
                    para_first += int(first_paragraph(c, fid, m) == para)  # the baseline beside it: the method's opening
    by_kind = {k: {"edges": judged[k], "right": right[k], "precision": _ratio(right[k], judged[k]), "unjudged": unjudged[k]} for k in EVIDENCE}
    by_kind["all"] = {"edges": sum(judged.values()), "right": sum(right.values()), "precision": _ratio(sum(right.values()), sum(judged.values())), "unjudged": sum(unjudged.values())}
    return {
        "source": source,
        "labels": rows_total,
        "verdicts": {v: verdicts[v] for v in VERDICTS},
        "findings": labelled,
        "papers": len(papers_seen),
        "unanchored": {"findings": lost["findings"], "methods": lost["methods"], "paragraphs": lost["paragraphs"]},
        "precision": by_kind,
        "recall": {"methods": yes_total, "reached": sum(reached.values()), "recall": _ratio(sum(reached.values()), yes_total), "by": {k: reached[k] for k in EVIDENCE}},
        "misses": {"count": len(misses), "findings": misses},
        "false_links": {"count": len(false_links), "findings": false_links},
        "astray": astray,  # a method labelled yes, and edges only to others
        "outside": outside,  # labelled, every candidate no: the method is not among the candidates
        "none": none_total,
        "paragraph": {"named": para_named, "right": para_right, "accuracy": _ratio(para_right, para_named), "chooser": getattr(choose, "__name__", type(choose).__name__),
                      "first_paragraph": _ratio(para_first, para_named)},
    }


def _groups(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Label rows by the finding they are on now: its methods labelled yes (each with its
    paragraph), those labelled no, and whether it was labelled `none`. A row that cannot be
    found again is left out."""
    out: dict[str, dict[str, Any]] = {}
    for a in rows:
        if a["finding_now"] is None:
            continue
        g = out.setdefault(a["finding_now"], {"paper": a["paper"], "text": a["finding_text"], "by": a["by"], "yes": {}, "no": set(), "none": False})
        if a["verdict"] == "none":
            g["none"] = True
        elif a["method_now"]:
            if a["verdict"] == "yes":
                g["yes"][a["method_now"]] = a["paragraph_now"]
            else:
                g["no"].add(a["method_now"])
    return out


def agreement(conn: sqlite3.Connection | Iterable[sqlite3.Connection]) -> dict[str, Any]:
    """The local model's labels against a person's, on the findings both labelled — the audit.

    A finding agrees when both name the same methods, `none` and "every candidate no" both
    naming none. Per method, of every method either judged: both yes, both no, the model's yes
    alone, the person's alone. Per paragraph, where both named one in the same method. The
    model's labels `stand` once `AUDIT_MIN` findings are audited and `AGREEMENT_GATE` of them
    agree; until then they are compared, never counted."""
    conns = [conn] if isinstance(conn, sqlite3.Connection) else list(conn)
    audited = agree = both_yes = both_no = model_only = person_only = para_both = para_same = labelled = 0
    models: Counter[str] = Counter()
    disagreements: list[dict[str, Any]] = []
    for c in conns:
        papers = _Papers(c)
        model = _groups(anchor(c, papers, table="model_labels"))
        labelled += len(model)
        models.update(g["by"] or "" for g in model.values())
        for fid, pg in _groups(anchor(c, papers)).items():
            mg = model.get(fid)
            if mg is None:
                continue
            audited += 1
            mine, its = set(pg["yes"]), set(mg["yes"])
            agree += int(mine == its)
            judged = mine | its | pg["no"] | mg["no"]
            both_yes, person_only, model_only = both_yes + len(mine & its), person_only + len(mine - its), model_only + len(its - mine)
            both_no += len(judged - mine - its)
            for m in mine & its:
                if pg["yes"][m] and mg["yes"][m]:
                    para_both += 1
                    para_same += int(pg["yes"][m] == mg["yes"][m])
            if mine != its:
                p = papers.get(pg["paper"])

                def said(g: dict[str, Any]) -> list[str]:
                    return [_method_label(p.by_id[m]) if p and m in p.by_id else m for m in g["yes"]] or (["no method in this paper"] if g["none"] else ["none of these"])

                disagreements.append({"finding": fid, "paper": pg["paper"], "text": pg["text"], "person": said(pg), "model": said(mg)})
    rate = _ratio(agree, audited)
    return {
        "labelled": labelled,  # findings the model labelled
        "models": dict(models),
        "audited": audited,
        "agree": agree,
        "agreement": rate,
        "methods": {"both_yes": both_yes, "both_no": both_no, "model_only": model_only, "person_only": person_only,
                    "agreement": _ratio(both_yes + both_no, both_yes + both_no + model_only + person_only)},
        "paragraphs": {"both": para_both, "same": para_same, "agreement": _ratio(para_same, para_both)},
        "disagreements": disagreements,
        "needed": AUDIT_MIN,
        "gate": AGREEMENT_GATE,
        "stands": audited >= AUDIT_MIN and rate is not None and rate >= AGREEMENT_GATE,
    }


def report(conn: sqlite3.Connection | Iterable[sqlite3.Connection], choose_paragraph: ParagraphChooser | None = None) -> dict[str, Any]:
    """What the `truth` op and `--measure` answer: the linker against a person's labels, and,
    when the local model has labelled findings, `model` — its agreement with the person, and
    once its labels stand, the linker measured again with them (`measure`, `source="both"`)."""
    conns = [conn] if isinstance(conn, sqlite3.Connection) else list(conn)
    out = measure(conns, choose_paragraph)
    a = agreement(conns)
    out["model"] = {**a, "measure": measure(conns, choose_paragraph, source="both") if a["stands"] else None} if a["labelled"] else None
    return out


# -- a label set outside the library ----------------------------------------------------------------------


def export_labels(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every label row as stored — the JSON lines of `--export`."""
    return [dict(r) for r in conn.execute(f"SELECT {', '.join(COLUMNS)} FROM link_labels ORDER BY paper, finding, method")]


def import_labels(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Label rows from elsewhere, one finding's set at a time, each finding and method found
    again in this library (`anchor`) and stored under the ids they have here; a finding that
    cannot be found is kept as it came, to be found after a later reading, and counted. A
    paper this library does not hold is skipped. Importing twice changes nothing."""
    papers = _Papers(conn)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault((str(r["paper"]), str(r["finding"])), []).append(r)
    out = Counter()
    for (key, finding), group in groups.items():
        if key not in papers.info:
            out["skipped"] += len(group)
            continue
        p = papers.get(key)
        f_now = _find(p, finding, str(group[0].get("finding_text") or ""), p.prose())[0] if p else None
        stored: list[tuple[Any, ...]] = []
        for r in group:
            if str(r.get("verdict")) not in VERDICTS:
                raise ValueError(f"a verdict is yes, no or none, not {r.get('verdict')!r}")
            m_now: str | None = "" if r["verdict"] == "none" or not r.get("method") else (_find_method(p, str(r["method"]), str(r.get("method_heading") or "")) if p and f_now else None)
            para_now = None
            if p and m_now and r.get("paragraph"):
                para_now = _find(p, str(r["paragraph"]), str(r.get("paragraph_text") or ""), p.prose(p.by_id[m_now]))[0]
            out["anchored" if f_now and m_now is not None else "unanchored"] += 1
            stored.append((key, f_now or finding, r.get("finding_text") or "", m_now if m_now is not None else r.get("method") or "", r.get("method_heading") or "",
                           para_now or r.get("paragraph"), r.get("paragraph_text"), r["verdict"], r.get("by"), r.get("at") or ""))
        _write(conn, p, f_now or finding, stored, papers, key=key)
        out["findings"] += 1
    out["imported"] = out["anchored"] + out["unanchored"]
    return {k: out[k] for k in ("imported", "findings", "anchored", "unanchored", "skipped")}


# -- the command line ---------------------------------------------------------------------------------------


def _open(lib: str) -> sqlite3.Connection:
    from .store import open_store

    path = Path(lib).expanduser() / "store.sqlite"
    if not path.exists():
        raise SystemExit(f"no library store at {path}")
    return open_store(path)


def _show(r: dict[str, Any]) -> None:
    def frac(a: int, b: int) -> str:
        return f"{a}/{b}  {a / b:.2f}" if b else "–"

    lost = r["unanchored"]
    print(f"{r['findings']} findings labelled in {r['papers']} papers ({r['labels']} labels: {r['verdicts']['yes']} yes, {r['verdicts']['no']} no, {r['verdicts']['none']} none)"
          + (f"; not found again after a rebuild: {lost['findings']} findings, {lost['methods']} methods, {lost['paragraphs']} paragraphs" if any(lost.values()) else ""))
    print("precision by evidence — edges on labelled findings that point at a method labelled yes:")
    for k in (*EVIDENCE, "all"):
        m = r["precision"][k]
        print(f"  {k:<11} {frac(m['right'], m['edges']):<14}" + (f" ({m['unjudged']} to a method with no label, left out)" if m["unjudged"] else ""))
    rc = r["recall"]
    print(f"recall: {rc['reached']} of {rc['methods']} methods labelled yes have an edge" + (f" ({rc['recall']:.2f})" if rc["recall"] is not None else ""))
    print(f"misses: {r['misses']['count']} findings with a method labelled yes and no edge; astray: {r['astray']} with edges only to other methods")
    print(f"false links: {r['false_links']['count']} of {r['none']} findings labelled 'no method in this paper' got an edge")
    if r["outside"]:
        print(f"not among the candidates: {r['outside']} findings with every candidate labelled no")
    pg = r["paragraph"]
    if pg["named"]:
        print(f"paragraph ({pg['chooser']}): the paragraph named {frac(pg['right'], pg['named'])}"
              + (f"; the method's first paragraph {pg['first_paragraph']:.2f}" if pg.get("first_paragraph") is not None else ""))
    m = r.get("model")
    if m:
        who = ", ".join(f"{k} {v}" for k, v in m["models"].items())
        print(f"\nthe local model labelled {m['labelled']} findings ({who}); a person has labelled {m['audited']} of them"
              + (f", agreeing on {frac(m['agree'], m['audited'])}" if m["audited"] else ""))
        mm = m["methods"]
        if m["audited"]:
            print(f"  per method: both yes {mm['both_yes']}, both no {mm['both_no']}, the model's yes alone {mm['model_only']}, the person's alone {mm['person_only']}"
                  + (f"; paragraphs {frac(m['paragraphs']['same'], m['paragraphs']['both'])}" if m["paragraphs"]["both"] else ""))
        for d in m["disagreements"][:10]:
            print(f"  - {d['text'][:90]}…\n      person: {'; '.join(d['person'])}\n      model:  {'; '.join(d['model'])}")
        if m["stands"]:
            print(f"its labels stand ({m['audited']} audited, agreement at least {m['gate']}); with them, for every finding either labelled:")
            _show(m["measure"])
        else:
            print(f"its labels are compared, not counted, until {m['needed']} are audited at {m['gate']} agreement")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="litrag_parser.truth", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--lib", action="append", default=[], help="a library directory (repeatable)")
    ap.add_argument("--measure", action="store_true", help="the linker against the labels")
    ap.add_argument("--json", action="store_true", help="the measure as JSON")
    ap.add_argument("--export", metavar="FILE", help="every label as JSON lines")
    ap.add_argument("--import", dest="import_file", metavar="FILE", help="labels from JSON lines, found again in the library that holds each paper")
    args = ap.parse_args(argv)
    if not args.lib or not (args.measure or args.export or args.import_file):
        ap.print_help()
        return 2
    conns = {lib: _open(lib) for lib in args.lib}
    try:
        if args.import_file:
            rows = [json.loads(line) for line in Path(args.import_file).read_text("utf-8").splitlines() if line.strip()]
            for lib, c in conns.items():
                held = {r[0] for r in c.execute("SELECT key FROM papers")}
                mine = [r for r in rows if r.get("paper") in held]
                rows = [r for r in rows if r.get("paper") not in held]
                print(f"{lib}: {json.dumps(import_labels(c, mine))}")
            if rows:
                print(f"{len(rows)} labels name papers no library given holds: not imported")
        if args.export:
            with open(args.export, "w", encoding="utf-8") as fh:
                n = 0
                for lib, c in conns.items():
                    for r in export_labels(c):
                        fh.write(json.dumps({**r, "lib": Path(lib).name}, ensure_ascii=False) + "\n")
                        n += 1
            print(f"{n} labels written to {args.export}")
        if args.measure:
            r = report(list(conns.values()), choose_paragraph=_hydration_chooser())
            if args.json:
                print(json.dumps(r, ensure_ascii=False, indent=1))
            else:
                _show(r)
    finally:
        for c in conns.values():
            c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
