"""The outline judge: a local model reads the whole paper and says where its sections are.

The comparison of PDF readings with their XML (`pairs.py`) showed that what a PDF loses is
rarely text and mostly structure: a heading the layout model dropped or fused, so the
results stay under the methods; a review's sections read as the introduction's children;
a body that starts with no heading and stays in the abstract. A person sees where a section
begins by reading; so does a language model on this machine, given the whole paper — a
paper of your median length is 11,000 tokens, Qwen 3's window is 40,000, and one answer
takes fifteen to twenty seconds on the card. Through Ollama on 127.0.0.1; nothing leaves the
machine.

What the model is asked: the paper as the reader built it, headings marked with `#` and
every paragraph numbered, the reference list left out; back comes a JSON outline — for every
section its title, its depth, its lane and the paragraph it starts at. What is taken from
the answer, and what is not:

- **a lane, where the rules gave none.** A section whose own heading the vocabulary and
  the catalogue do not know, and whose place under the section above it the reader only
  inferred (an unnumbered heading on a PDF page), took its lane from that section, or has
  `other`. What the model's word does there depends on the paper: in a review (or a paper
  whose type is not settled) a section the model puts outside the one it was nested under
  is a topical section, `other`, whatever the model calls it — a lane is a heading's word,
  the review's XML lanes it so, and Qwen 3 14B calls a review's sections methods and
  results-discussion by their sense; in a research paper a subsection is part of its
  section whatever the model calls it, and only a section the reader could not place
  (unnamed at the top level, or the body left under the abstract) takes the model's lane.
  A heading that names its own lane keeps it, a numbered subsection keeps the lane its
  numbering put it under (measured: a review's "1.1 Anatomy of the cornea" under "1.
  Introduction" is the introduction by the authors' own numbering, and taking the model's
  "other" there sent a well-read paper from 0.97 to 0.15 against its XML), a heading the
  reader built that names a lane is the reader's rule, and the model's disagreement is a
  note (`outline-disagreement`), never applied. An XML document is never judged: it states
  its own structure.
- **a boundary, where the reader had none.** Where the model says a section starts at a
  paragraph in the middle of a section, the reader cuts there and builds a heading from the
  model's title, labelled `built` like every heading the reader makes — never a heading the
  reader already has (the model's paragraph is off by one), never inside the abstract's own
  parts. The built heading's lane is given the way the reader gives any heading its lane: a
  top-level title names its own, a nested one inherits the section's above unless it names
  back matter — never the model's word (measured: a review's run-in "2.1. Non Surgical
  Approach" given the model's `methods` sent 0.996 to 0.594 against its XML; given the
  reader's rule, building is neutral on the text's lanes and adds the headings). Its place
  is where the paragraphs stood: beside the section its numbering matches, or after the
  top-level section it ends when its title names a lane, else a subsection. Text is never
  dropped, only regrouped.
- **not the depth.** Measured on the XML: meaning alone cannot tell a flat outline from a
  nested one (a journal's reviews are flat, the model nests them by sense), so a section
  the reader found keeps its depth.

Every answer is a row (`outlines`, keyed by the paper's text and the model), so a rebuild
replays it and never asks; an ingest asks only when `LITRAG_OUTLINE=on`, or the `outline`
parameter of the request, and only for papers no row answers. The XML side of a pair is
never judged: it is the witness the judge is measured against.

    python -m litrag_parser.outline --pdf-lib DIR --xml-lib DIR [--model M] [--limit N] [--json FILE]

measures a model: every pair's faithful score before and after the judge, the depth and
lane agreement with the XML, the seconds a paper took, the answers that could not be read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .facets import normalise, role_of
from .headings import top_level_lane
from .tree import _A_YEAR, _REF_ENTRY, Node, Tree, _descendants, numbering_depth

DEFAULT_MODEL = "qwen3:14b"
DEFAULT_URL = "http://127.0.0.1:11434"
PROMPT_VERSION = "outline-1"
LANES = ("abstract", "introduction", "methods", "results", "results-discussion", "discussion", "other", "back")
CONTENT = ("introduction", "methods", "results", "results-discussion", "discussion")
_ALIAS = {
    "conclusion": "discussion", "conclusions": "discussion", "summary": "discussion", "summary and conclusions": "discussion", "outlook": "discussion", "perspectives": "discussion", "limitations": "discussion",
    "results and discussion": "results-discussion", "results_and_discussion": "results-discussion", "findings": "results",
    "materials and methods": "methods", "method": "methods", "methodology": "methods", "experimental": "methods",
    "background": "introduction", "intro": "introduction",
    "acknowledgements": "back", "acknowledgments": "back", "funding": "back", "declarations": "back", "supplementary": "back", "appendix": "back", "footnotes": "back",
    "references": "back", "bibliography": "back", "keywords": "other", "highlights": "other", "title": "other", "front matter": "other", "none": "other", "": "other",
}
#: Papers whose subsections are parts of their sections: under a heading that names a lane or a
#: numbering, nothing is taken from the model there (a methods subsection the model calls
#: topical is still the methods); a section the reader could not place — unnamed at the top
#: level, or left under the abstract — takes the model's lane. In any other paper (a review,
#: or one whose type is not yet settled) the sections are the paper's own and a lane is a
#: heading's word: a section the model puts outside the one the reader nested it under is a
#: topical section, `other`, whatever the model calls it — the XML of a review lanes it so, and
#: Qwen 3 14B calls a review's sections methods and results-discussion by their sense.
RESEARCH_LIKE = ("research", "case-report", "protocol", "data")
#: Whether a boundary the model finds inside a section becomes a built heading ("any") or is
#: left alone ("never"). A built heading's lane is never the model's: it is given the way the
#: reader gives any heading its lane — the model adds structure, the rules still name the lane
#: (the first cut gave a built heading the model's lane, and a review's "2.1. Non Surgical
#: Approach" became methods, 0.996 → 0.594 against its XML). Measured on the pairs; see NOTES.md.
BUILD_POLICY = "any"
_BUILT = "#section-outline-"  # in the id of every section the outline built
BUDGET_WORDS = 18000  # a paper longer than this is sent as the first sixty words of every paragraph, which is where a section shows it began
EXCERPT_WORDS = 60
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_ENTRYISH = re.compile(r"\bdoi\b|\bet al\b|\bvol\.?\b|\bpp?\.\s*\d|\d+\s*[:(]\s*\d+|\bJ\b\.|\bProc\b", re.I)


def looks_like_references(text: str) -> bool:
    """A paragraph that is reference entries the reader did not recognise as a list: years and
    the furniture of citations, thick on the ground. Left out of what the model sees — four of
    31 first answers were "it seems you have shared a list of references" because a Frontiers
    PDF's list had been read as prose."""
    if len(text) < 700 and _REF_ENTRY.match(text) and _A_YEAR.search(text):
        return True  # one entry, the shape the reader knows: a Frontiers PDF's list read as 93 back-matter paragraphs
    years = len(_YEAR.findall(text))
    if years < 3:
        return False
    words = max(len(text.split()), 1)
    return years / words >= 0.03 and len(_ENTRYISH.findall(text)) >= 3

PROMPT = """You are given a scientific paper as it was read from its PDF. Headings the reader found are marked with '#'; every paragraph is numbered like [p12]; the reference list is left out. The reader may have missed a heading, run two headings into one line, or nested sections wrongly.

Return the paper's outline as a JSON array, in reading order, one object per section:
{"title": "<the heading as printed, or a short title you infer where the reader printed none>", "printed": true or false, "level": 1 or 2 or 3, "lane": "<abstract|introduction|methods|results|results-discussion|discussion|other|back>", "first_paragraph": <the number of the section's first paragraph>}

Rules: level 1 is a top-level section of the paper; subsections are 2, their subsections 3. "lane" is the kind of text the section holds: "introduction" for the background and aims, "methods" for how the work was done, "results" for what was found, "discussion" for what it means and for conclusions, "results-discussion" for a section that does both, "abstract" for the abstract, "back" for acknowledgements, funding, contributions, conflicts of interest, data statements and supplementary notes, and "other" for a topical section of a review or anything that is none of these. A highlights or key-points box is "other". Where the text moves from one kind to another with no heading printed, start a new section there with "printed": false. Answer with the JSON array only, compact, one object per line: no explanation before it, no prose after it, the first character of your answer is [ and the last is ]."""


def enabled() -> bool:
    return os.environ.get("LITRAG_OUTLINE", "").strip().lower() in ("on", "1", "true", "yes")


def default_model() -> str:
    return os.environ.get("LITRAG_OUTLINE_MODEL") or DEFAULT_MODEL


def default_url() -> str:
    return (os.environ.get("LITRAG_OLLAMA_URL") or DEFAULT_URL).rstrip("/")


# -- the paper, as the model sees it ---------------------------------------------------------------

def paragraphs_of(tree: Tree) -> list[Node]:
    """The paragraphs the model is shown, in reading order: prose and list items, the front
    matter and the reference list left out."""
    out = []
    for n in tree.walk():
        if n.type not in ("paragraph", "list_item") or not (n.text or "").strip():
            continue
        if n.role == "references" or (n.ancestry and n.ancestry[0] == "Front matter") or looks_like_references(n.text):
            continue
        out.append(n)
    return out


def prompt_text(tree: Tree) -> tuple[str, list[Node]]:
    """Headings marked, paragraphs numbered; long papers as excerpts."""
    paragraphs = paragraphs_of(tree)
    total = sum(len((n.text or "").split()) for n in paragraphs)
    excerpt = total > BUDGET_WORDS
    number = {id(n): i + 1 for i, n in enumerate(paragraphs)}
    lines: list[str] = []
    for n in tree.walk():
        if n.type == "section":
            if n.heading and n.heading != "Front matter" and not n.heading.endswith(("(untitled section)", "(heading not detected)")) and n.role != "references":
                lines.append(f"\n# {n.heading}")
        elif id(n) in number:
            words = (n.text or "").split()
            text = " ".join(words[:EXCERPT_WORDS]) + (" …" if excerpt and len(words) > EXCERPT_WORDS else "") if excerpt else " ".join(words)
            lines.append(f"[p{number[id(n)]}] {text}")
    return "\n".join(lines).strip(), paragraphs


def signature(text: str, model: str) -> str:
    return hashlib.sha1(f"{PROMPT_VERSION}\n{model}\n{text}".encode("utf-8")).hexdigest()


# -- the model ---------------------------------------------------------------------------------------

def ask(text: str, *, model: str | None = None, url: str | None = None, timeout: float = 1800.0) -> dict[str, Any]:
    """One request; `{content, prompt_tokens, answer_tokens, seconds}`. Thinking off, temperature
    zero, a window sized to the paper (Ollama's default is far too small for one)."""
    model = model or default_model()
    url = url or default_url()
    words = len(text.split())
    need = int(words * 1.45) + len(PROMPT.split()) * 2 + 3000
    num_ctx = min(40960, max(8192, ((need + 2047) // 2048) * 2048))
    body = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT + "\n\nPAPER:\n" + text}],
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "seed": 7, "num_ctx": num_ctx, "num_predict": 8000},
    }
    req = urllib.request.Request(f"{url}/api/chat", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return {
        "content": (resp.get("message") or {}).get("content", ""),
        "prompt_tokens": resp.get("prompt_eval_count"),
        "answer_tokens": resp.get("eval_count"),
        "seconds": round(time.time() - t0, 1),
        "num_ctx": num_ctx,
    }


def parse(content: str, n_paragraphs: int) -> list[dict[str, Any]] | None:
    """The JSON array in the answer, cleaned: lanes to the reader's names, levels 1 to 3,
    paragraph numbers in range, one section per starting paragraph, in order. None when
    there is no array to read."""
    s = content.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s).rstrip("`").strip()
    start = s.find("[")
    if start < 0:
        return None
    end = s.rfind("]")
    raw: Any = None
    if end > start:
        try:
            raw = json.loads(s[start : end + 1])
        except json.JSONDecodeError:
            raw = None
    if raw is None:
        # an answer cut short by the output budget, or trailing words: the complete objects before the cut
        cut = s.rfind("}", start)
        if cut > start:
            try:
                raw = json.loads(s[start : cut + 1] + "]")
            except json.JSONDecodeError:
                raw = None
    if not isinstance(raw, list):
        return None
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        try:
            first = int(e.get("first_paragraph"))
        except (TypeError, ValueError):
            continue
        if not 1 <= first <= n_paragraphs or first in seen:
            continue
        seen.add(first)
        lane = str(e.get("lane", "other")).strip().lower().replace(" and ", " and ")
        lane = _ALIAS.get(lane, lane)
        if lane not in LANES:
            lane = "other"
        try:
            level = int(e.get("level", 1))
        except (TypeError, ValueError):
            level = 1
        out.append({"title": str(e.get("title") or "").strip()[:200], "printed": bool(e.get("printed", True)), "level": min(max(level, 1), 3), "lane": lane, "first_paragraph": first})
    out.sort(key=lambda e: e["first_paragraph"])
    return out


# -- the rows ----------------------------------------------------------------------------------------

def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS outlines (paper TEXT NOT NULL, signature TEXT NOT NULL, model TEXT NOT NULL, outline TEXT NOT NULL, prompt_tokens INTEGER, answer_tokens INTEGER, seconds REAL, at TEXT NOT NULL, PRIMARY KEY (paper, signature))")


def stored(conn: sqlite3.Connection, paper: str, sig: str) -> list[dict[str, Any]] | None:
    _ensure(conn)
    row = conn.execute("SELECT outline FROM outlines WHERE paper = ? AND signature = ?", (paper, sig)).fetchone()
    return json.loads(row[0]) if row else None


def remember(conn: sqlite3.Connection, paper: str, sig: str, model: str, outline: list[dict[str, Any]], answer: dict[str, Any]) -> None:
    _ensure(conn)
    with conn:
        conn.execute("INSERT OR REPLACE INTO outlines (paper, signature, model, outline, prompt_tokens, answer_tokens, seconds, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (paper, sig, model, json.dumps(outline, ensure_ascii=False), answer.get("prompt_tokens"), answer.get("answer_tokens"), answer.get("seconds"), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))


# -- what is taken from the answer -------------------------------------------------------------------

def own_lane(heading: str | None) -> str:
    """The lane a heading names by itself — the vocabulary, the catalogue, the embedder —
    or `other`: what the rules said, which the judge never overrides."""
    if not heading or heading == "Front matter":
        return "other"
    return top_level_lane(heading) or role_of(heading)


def rules_silent(section: Node) -> bool:
    """Whether the section's lane is the reader's guess rather than a rule's word: its heading
    names no lane, it is unnumbered (a numbered heading's depth, and the lane it inherits with
    it, is the authors' structure), or the reader built it from nothing — a built heading that
    names a lane ("Introduction" over the text before the first heading) is the reader's rule,
    and a Scientific Reports paper's introduction went results-discussion on the model's word
    before it counted as one."""
    if own_lane(section.heading) != "other":
        return False
    if section.label == "built":
        return True
    return numbering_depth(section.heading or "") is None


def _from_outline(node: Node) -> bool:
    return _BUILT in node.node_id


def _section_of(node: Node, by_id: dict[str, Node]) -> Node | None:
    p = by_id.get(node.parent or "")
    while p is not None and p.type != "section":
        p = by_id.get(p.parent or "")
    return p


def _relabel(node: Node, parent: Node, ancestry: list[str]) -> None:
    node.parent = parent.node_id
    node.depth = parent.depth + 1
    node.ancestry = list(ancestry)
    for i, c in enumerate(node.children):
        c.ordinal = i
        _relabel(c, node, ancestry + ([node.heading] if node.type == "section" and node.heading else []))


def apply(tree: Tree, outline: list[dict[str, Any]], paragraphs: list[Node], repairs: dict[str, int], model: str, paper_type: str | None = None) -> dict[str, Any]:
    """The outline's lanes onto sections the rules left unnamed, and its boundaries as built
    headings; disagreements as notes. Returns what was done. `paper_type` is the reader's
    first word on the paper's type: in a research paper a subsection is part of its section."""
    by_id = {n.node_id: n for n in tree.walk()}
    existing = {normalise(n.heading) for n in tree.walk() if n.type == "section" and n.heading}
    first_paragraph: dict[str, Node] = {}  # section id → its first paragraph
    for p in paragraphs:
        s = _section_of(p, by_id)
        if s is not None and s.node_id not in first_paragraph:
            first_paragraph[s.node_id] = p
    lanes: dict[str, str] = {}  # section id → the model's lane
    titles: dict[str, str] = {}
    built = 0
    counter = 0
    for e in outline:
        p = paragraphs[e["first_paragraph"] - 1]
        section = _section_of(p, by_id)
        if section is None:
            continue
        starts_section = first_paragraph.get(section.node_id) is p
        same_title = normalise(e["title"]) == normalise(section.heading or "") if e["title"] else False
        if starts_section or same_title or (section.label == "built" and not _from_outline(section)):
            lanes[section.node_id] = e["lane"]
            titles[section.node_id] = e["title"]
            continue
        # a boundary inside a section: cut here and build the heading
        title = (e["title"] or "").strip()
        if BUILD_POLICY == "never" or e["lane"] == "abstract" or not title or normalise(title) in existing:
            continue  # the abstract is the reader's to find; a heading the reader has is never built twice (the model's paragraph is off by one)
        parent = by_id.get(p.parent or "")
        if parent is None or p not in parent.children:
            continue
        at = parent.children.index(p)
        moved: list[Node] = []
        for c in parent.children[at:]:
            if c.type == "section":
                break
            moved.append(c)
        if not moved:
            continue
        counter += 1
        # the new section stands where the paragraphs stood, so nothing is reordered. Its place, when the run ends
        # everything below the section it would stand beside: a numbered title beside the ancestor its numbering
        # matches ("4. Conclusions" read into "3.6", the last subsection of "3", is a top-level section after "3");
        # an unnumbered title that names a lane, as a top-level heading would, a top-level section after the one
        # it ends ("Results" read into the end of the methods) — though never out of the abstract, whose parts are
        # its own; a second boundary in one run a sibling after the first; anything else a subsection of the
        # section that held the paragraphs, whatever depth the model gave it (depth is not taken from the model)
        host, where = parent, at
        wanted = numbering_depth(title)
        anc, tail = parent, at + len(moved) == len(parent.children)
        while tail and anc is not None and anc.type == "section":
            d = numbering_depth(anc.heading or "")
            up = by_id.get(anc.parent or "")
            top = up is not None and up.type != "section"
            if wanted is not None:
                if (d is not None and d <= wanted) or top:
                    if up is not None and (d == wanted or (top and wanted == 1)):
                        host, where = up, up.children.index(anc) + 1
                    break
            elif top:
                if own_lane(title) != "other" and anc.role != "abstract" and anc.heading != "Front matter":
                    host, where = up, up.children.index(anc) + 1
                break
            tail = up is not None and bool(up.children) and up.children[-1] is anc
            anc = up
        if host is parent and _from_outline(parent) and parent.parent in by_id:
            host = by_id[parent.parent]
            where = host.children.index(parent) + 1
        # its lane as the reader gives any heading its lane: a top-level heading names its own, a nested one
        # inherits the section's above unless it names back matter — never the model's
        role = own_lane(title) if host.type != "section" else ("back" if role_of(title, meaning=False) == "back" else host.role)
        new = Node(node_id=f"{host.node_id}{_BUILT}{counter}", parent=host.node_id, ordinal=0, depth=host.depth + 1, type="section", label="built", level=((host.level or 0) + 1) if host.type == "section" else 1, role=role, heading=title, ancestry=[], text="", page=p.page, bbox=None, self_ref=None)
        for c in moved:
            parent.children.remove(c)
        host.children.insert(where, new)
        new.children = moved
        _relabel(new, host, list(host.ancestry) + ([host.heading] if host.type == "section" and host.heading else []))
        for holder in (parent, host):
            for i, c in enumerate(holder.children):
                c.ordinal = i
        for c in _descendants(new):
            by_id[c.node_id] = c
            c.role = role
        existing.add(normalise(title))
        first_paragraph[new.node_id] = p
        built += 1
    applied = disagreed = 0
    for sid, lane in lanes.items():
        section = by_id.get(sid)
        if section is None or section.role == "references":
            continue
        own = own_lane(section.heading)
        if not rules_silent(section):
            if lane in CONTENT and lane != section.role and (own != "other" or lane != "other"):
                disagreed += 1
                how = ("built heading names" if section.label == "built" else "heading names") if own != "other" else "numbering puts the section under"
                tree.notes.append({"kind": "outline-disagreement", "node_id": sid, "page": section.page, "message": f"the {how} {section.role}; {model} reads it as {lane} — the rule's word stands"})
            continue
        if lane not in LANES or section.role == lane:
            continue  # already there — a topical section that inherited `introduction` and is `other` to the model is not
        if lane == "abstract":
            continue  # the abstract is the reader's to find; a body section is never made one by the judge
        parent_section = _section_of(section, by_id)
        parent_ruled = parent_section is not None and parent_section.type == "section" and not rules_silent(parent_section) and own_lane(parent_section.heading) != "abstract" and section.label != "built"
        if paper_type in RESEARCH_LIKE:
            if parent_ruled:
                continue  # a subsection is part of its section, whatever the model calls it
        else:
            lane = "other"  # a review's section the model puts outside the one it was nested under: topical, as its XML would lane it
            if section.role == lane:
                continue
        applied += 1
        for n in _descendants(section):
            if n is not section and n.type == "section" and own_lane(n.heading) != "other" and n.label != "built":
                continue  # a subsection that names its own lane keeps it, and its text with it
            n.role = lane
        # a subsection named by its heading keeps its subtree: give those back
        for n in _descendants(section):
            if n is not section and n.type == "section" and own_lane(n.heading) != "other" and n.label != "built":
                keep = own_lane(n.heading)
                for m in _descendants(n):
                    m.role = keep
    if applied or built:
        roles: dict[str, int] = {}
        for n in tree.walk():
            if n is not tree.root:
                roles[n.role] = roles.get(n.role, 0) + 1
        tree.roles = dict(sorted(roles.items()))
        tree.has_methods = roles.get("methods", 0) > 0
    repairs["outline_lanes"] = repairs.get("outline_lanes", 0) + applied
    repairs["outline_built"] = repairs.get("outline_built", 0) + built
    repairs["outline_disagreements"] = repairs.get("outline_disagreements", 0) + disagreed
    return {"sections": len(outline), "lanes": applied, "built": built, "disagreements": disagreed}


def judge(tree: Tree, conn: sqlite3.Connection | None, paper: str, *, model: str | None = None, url: str | None = None, ask_model: bool = True, repairs: dict[str, int] | None = None, paper_type: str | None = None, pub_types: list[str] | str | None = None) -> dict[str, Any]:
    """The whole thing for one paper: the prompt, a stored answer or a new one (only with
    `ask_model`), and what was applied. `{model, asked, sections, lanes, built, disagreements,
    seconds, unreadable}`; `sections` is None when no answer is there. The paper's type as the
    reader first reads it (`paper_type`, else decided here from the tree and `pub_types`)
    says whether a subsection is its section's part."""
    model = model or default_model()
    repairs = tree.repairs if repairs is None else repairs
    if not tree.pages:
        return {"model": model, "asked": False, "sections": None, "skipped": "an XML states its own structure"}
    if paper_type is None:
        from . import lanes
        from .paper_type import decide as decide_type

        paper_type = decide_type(tree, pub_types=pub_types, oracle=lanes.active())["type"]
    text, paragraphs = prompt_text(tree)
    if not paragraphs:
        return {"model": model, "asked": False, "sections": None}
    sig = signature(text, model)
    outline = stored(conn, paper, sig) if conn is not None else None
    asked = False
    answer: dict[str, Any] = {}
    if outline is None and ask_model:
        try:
            answer = ask(text, model=model, url=url)
        except (urllib.error.URLError, OSError, ValueError) as e:
            return {"model": model, "asked": True, "sections": None, "error": str(e)[:200]}
        asked = True
        outline = parse(answer["content"], len(paragraphs))
        if outline is None:
            return {"model": model, "asked": True, "sections": None, "unreadable": answer["content"][:200], "seconds": answer.get("seconds")}
        if conn is not None:
            remember(conn, paper, sig, model, outline, answer)
    if outline is None:
        return {"model": model, "asked": False, "sections": None}
    done = apply(tree, outline, paragraphs, repairs, model, paper_type)
    return {"model": model, "asked": asked, "type": paper_type, **done, "seconds": answer.get("seconds"), "prompt_tokens": answer.get("prompt_tokens")}


# -- the measurement over the pairs ------------------------------------------------------------------

def measure(pdf_lib: Path, xml_lib: Path, *, model: str, limit: int | None = None, keys: list[str] | None = None, url: str | None = None) -> list[dict[str, Any]]:
    from .harness import read_paper
    from .pairs import compare, pairs_of

    out = []
    conn = sqlite3.connect(Path(pdf_lib) / "store.sqlite")
    for i, (p_row, x_row) in enumerate(pairs_of(Path(pdf_lib), Path(xml_lib))):
        if keys and p_row["key"] not in keys:
            continue
        if limit is not None and len(out) >= limit:
            break
        pdf_tree, pdf_kind, _ = read_paper(Path(pdf_lib), p_row)
        xml_tree, _, xml_bytes = read_paper(Path(xml_lib), x_row)
        before = compare(pdf_tree, xml_tree, xml_bytes)
        verdict = judge(pdf_tree, conn, p_row["key"], model=model, url=url, ask_model=True, paper_type=pdf_kind.get("type"))
        after = compare(pdf_tree, xml_tree, xml_bytes) if verdict.get("sections") is not None else before
        rec = {
            "key": p_row["key"], "title": xml_tree.title, "type": pdf_kind.get("type"), "model": model,
            "before": {k: before[k] for k in ("faithful", "recall", "precision")}, "after": {k: after[k] for k in ("faithful", "recall", "precision")},
            "headings_before": {k: before["headings"][k] for k in ("recall", "level_agree", "lane_agree")}, "headings_after": {k: after["headings"][k] for k in ("recall", "level_agree", "lane_agree")},
            "judge": verdict,
        }
        out.append(rec)
        f0, f1 = before["faithful"], after["faithful"]
        print(f"  {p_row['key']:38} faithful {f0:.3f} → {f1:.3f}  {'+' if f1 > f0 + 0.005 else '-' if f1 < f0 - 0.005 else '='}  sections {verdict.get('sections')} lanes {verdict.get('lanes', 0)} built {verdict.get('built', 0)} · {verdict.get('seconds') or '-'}s", flush=True)
    conn.close()
    return out


def summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    def mean(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 3) if xs else None

    answered = [r for r in records if r["judge"].get("sections") is not None]
    better = sum(1 for r in answered if r["after"]["faithful"] > r["before"]["faithful"] + 0.005)
    worse = sum(1 for r in answered if r["after"]["faithful"] < r["before"]["faithful"] - 0.005)
    return {
        "model": records[0]["model"] if records else None, "pairs": len(records), "answered": len(answered), "unreadable": sum(1 for r in records if r["judge"].get("unreadable")), "errors": sum(1 for r in records if r["judge"].get("error")),
        "faithful_before": mean([r["before"]["faithful"] for r in records]), "faithful_after": mean([r["after"]["faithful"] for r in records]),
        "precision_before": mean([r["before"]["precision"] for r in records]), "precision_after": mean([r["after"]["precision"] for r in records]),
        "well_matched_before": sum(1 for r in records if r["before"]["faithful"] >= 0.9 and r["before"]["precision"] >= 0.9), "well_matched_after": sum(1 for r in records if r["after"]["faithful"] >= 0.9 and r["after"]["precision"] >= 0.9),
        "seriously_off_before": sum(1 for r in records if r["before"]["faithful"] < 0.8 or r["before"]["precision"] < 0.8), "seriously_off_after": sum(1 for r in records if r["after"]["faithful"] < 0.8 or r["after"]["precision"] < 0.8),
        "better": better, "worse": worse, "lanes_applied": sum(r["judge"].get("lanes", 0) for r in answered), "built": sum(r["judge"].get("built", 0) for r in answered), "disagreements": sum(r["judge"].get("disagreements", 0) for r in answered),
        "headings_recall_before": mean([r["headings_before"]["recall"] for r in records if r["headings_before"]["recall"] is not None]), "headings_recall_after": mean([r["headings_after"]["recall"] for r in records if r["headings_after"]["recall"] is not None]),
        "lane_agree_before": mean([r["headings_before"]["lane_agree"] for r in records if r["headings_before"]["lane_agree"] is not None]), "lane_agree_after": mean([r["headings_after"]["lane_agree"] for r in records if r["headings_after"]["lane_agree"] is not None]),
        "seconds_per_paper": mean([r["judge"]["seconds"] for r in answered if r["judge"].get("seconds")]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m litrag_parser.outline", description="Measure a local model as the outline judge, on papers held as both PDF and XML.")
    ap.add_argument("--pdf-lib", required=True)
    ap.add_argument("--xml-lib", required=True)
    ap.add_argument("--model", default=None, help=f"an Ollama model; default {DEFAULT_MODEL} or LITRAG_OUTLINE_MODEL")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--key", action="append", default=[])
    ap.add_argument("--json", help="save every pair's record here (outside the repository)")
    args = ap.parse_args(argv)
    from . import lanes
    from .library import library_root

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    lanes.configure_from_env(library_root())
    model = args.model or default_model()
    print(f"outline judge: {model} over {Path(args.pdf_lib).name} against {Path(args.xml_lib).name}")
    records = measure(Path(args.pdf_lib), Path(args.xml_lib), model=model, limit=args.limit, keys=args.key or None)
    s = summary(records)
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": s, "pairs": records}, indent=1, ensure_ascii=False), "utf-8")
    print(json.dumps(s, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
