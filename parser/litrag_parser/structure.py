"""Lanes from content, where the headings are silent.

A heading is the author's statement of what a section is, and it wins: `facets.py` names
its lane exactly, `lanes.py` by meaning. Two things a heading cannot do. A paper may
print no headings at all (a letter, a short communication, a manuscript whose headings
the layout model missed), and a heading may name nothing ("5 Study population and
lineage distribution" is results; "3. Residue-level pooling and decoder" is methods).
Here the blocks themselves are read.

Two passes. Before the tree is built, a paper with no heading after its front matter has
every prose block scored against seven lane centroids and a position prior (`meaning.
KINDS["block"]`, from `data/block_lanes.json`) and the best run of lanes chosen by a
Viterbi pass with a fixed cost for switching lane. No shape is assumed — any combination of lanes, in the order the paper
presents them — and the one rule imposed is that nothing but back matter follows a
reference list. Each
run gets a heading built for it, in canonical words, marked `built` so a person sees
the reader wrote it and not the author; a run the scorer is unsure of gets an untitled
heading and stays `other`. After the tree is built, a top-level section whose heading
named nothing has its paragraphs scored the same way, and takes the lane when the
paragraphs are clearly of one of the lanes with a distinctive shape — methods, results,
references. A section whose heading did name a lane is scored too, and a strong
disagreement is written down as a note for the audit, never applied.

Every decision is a row in the oracle's store, keyed by the texts it was made from, so a
rebuild replays it without the embedder; the same paper gives the same headings on any
machine. With no oracle configured, nothing here happens.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .meaning import Oracle, Verdict

LANES = ("introduction", "methods", "results", "results-discussion", "discussion", "references", "back")
CANONICAL = {"introduction": "Introduction", "methods": "Materials and methods", "results": "Results", "results-discussion": "Results and discussion", "discussion": "Discussion", "references": "References", "back": "Back matter"}  # the catalogue's names (headings.CANON), the corpus's modal spellings
#: what a section with a heading that names nothing may take from its paragraphs: the lanes
#: whose paragraphs have a shape of their own (results and discussion together count as
#: results' shape). An introduction and a discussion read alike to an embedder, and a
#: review's topical section reads like both.
CONTENT_LANES = frozenset({"methods", "results", "results-discussion", "references"})
#: a stricter margin for a lane whose false positive is expensive: a section laned `references`
#: has its citation markers ignored by the linker, so the paragraphs must be unmistakably entries
LANE_MARGIN = {"references": 0.15}
RULE = "v1"  # the run rule (`viterbi`, `_runs`, MIN_PROSE, the `other` rule); bump it and stored runs are re-decided
#: what is worth a note when it contradicts a named heading: a section of citation-dense prose
#: reads as `references` to an embedder, and that is not a disagreement anyone needs to see
NOTE_LANES = frozenset({"methods", "results", "results-discussion"})
SWITCH_COST = 0.06  # in cosine units: a run must earn its lane over a few paragraphs to open
MIN_PROSE = 6  # blocks a paper needs before its headings are built
UNTITLED = "(untitled section)"

_PROSE_LABELS = {"text", "paragraph", "list_item"}


def _words(text: str) -> int:
    return len(text.split())


def _key(texts: list[str]) -> str:
    return "sha1:" + hashlib.sha1("\n␞\n".join(re.sub(r"\s+", " ", t).strip() for t in texts).encode("utf-8")).hexdigest()


def viterbi(scores: list[dict[str, float]], switch_cost: float = SWITCH_COST) -> list[str]:
    """The run of lanes that best explains a sequence of block scores: each block pays its
    lane's cosine, each change of lane costs `switch_cost`, and nothing but back matter
    (declarations, a publisher's note) follows references."""
    if not scores:
        return []
    lanes = [l for l in LANES if all(l in s for s in scores)]
    states = lanes + ["back~"]  # "back~": back matter after the reference list, which nothing follows

    def emission(t: int, state: str) -> float:
        return scores[t].get("back" if state == "back~" else state, 0.0)

    def allowed(prev: str, state: str) -> bool:
        if prev == "references":
            return state in ("references", "back~")
        if prev == "back~":
            return state == "back~"
        return state != "back~"

    best = [{s: emission(0, s) if s != "back~" else -1e9 for s in states}]
    back: list[dict[str, str]] = [{}]
    for t in range(1, len(scores)):
        row: dict[str, float] = {}
        ptr: dict[str, str] = {}
        for s in states:
            cands = [(best[t - 1][p] - (switch_cost if p != s else 0.0), p) for p in states if allowed(p, s)]
            score, prev = max(cands, key=lambda c: (c[0], c[1] == s))
            row[s] = score + emission(t, s)
            ptr[s] = prev
        best.append(row)
        back.append(ptr)
    last = max(states, key=lambda s: best[-1][s])
    path = [last]
    for t in range(len(scores) - 1, 0, -1):
        path.append(back[t][path[-1]])
    return ["back" if s == "back~" else s for s in reversed(path)]


def _runs(lanes: list[str]) -> list[tuple[int, int, str]]:
    """(start, end, lane) for each run of one lane, end exclusive."""
    out: list[tuple[int, int, str]] = []
    start = 0
    for i in range(1, len(lanes) + 1):
        if i == len(lanes) or lanes[i] != lanes[start]:
            out.append((start, i, lanes[start]))
            start = i
    return out


def build_headings(items: list[dict[str, Any]], front_end: int, title_ref: str | None, oracle: Oracle | None, repairs: dict[str, int]) -> tuple[list[dict[str, Any]], int]:
    """The pre-pass: a paper with no heading at all gets one built for each run of blocks
    of one lane. Returns the items with the built headers inserted, and where the front
    matter ends: with no heading to mark it, the first block of forty words or more after
    the title is the abstract (the rule `_front_kind` applies to it), and the runs begin at
    the block after it. When nothing is built — no oracle, an oracle that cannot answer,
    too few blocks — the items and the boundary come back untouched."""
    if oracle is None:
        return items, front_end
    # a "References" heading the reader inferred from the entries' shape (tree._infer_references)
    # is not the author's: the blocks before it still have no heading, and it stays where it is
    stop = next((i for i, it in enumerate(items) if it.get("_inferred")), len(items))
    # the title itself may carry the label `section_header` (Docling's usual word for a big first-page line)
    if any(it.get("label") == "section_header" and (it.get("text") or "").strip() and not it.get("_inferred") and it.get("self_ref") != title_ref for it in items):
        return items, front_end
    after_title = next((i + 1 for i, it in enumerate(items) if title_ref is not None and it.get("self_ref") == title_ref), 0)
    abstract = next((i for i in range(after_title, stop) if items[i].get("label") in _PROSE_LABELS and _words(items[i].get("text") or "") >= 40), None)
    if abstract is None:
        return items, front_end
    start = abstract + 1
    prose = [(i, it) for i, it in enumerate(items) if start <= i < stop and it.get("label") in _PROSE_LABELS and _words(it.get("text") or "") >= 8]
    if len(prose) < MIN_PROSE:
        return items, front_end
    texts = [(it.get("text") or "").strip() for _, it in prose]
    key = _key(texts) + f"@{SWITCH_COST}/{MIN_PROSE}/{RULE}/{sorted(LANE_MARGIN.items())}"
    known = oracle.recall("block", key)
    kind = oracle.kinds["block"]
    if known is not None:
        lanes = json.loads(known.name) if known.name.startswith("[") else []
    else:
        scores = oracle.scores("block", texts, [i / max(len(texts) - 1, 1) for i in range(len(texts))])
        if scores is None:
            return items, front_end
        path = viterbi(scores)
        lanes = []
        for s, e, lane in _runs(path):
            mean = sum(scores[i][lane] for i in range(s, e)) / (e - s)
            runner_up = sum(max((v for l, v in scores[i].items() if l != lane), default=0.0) for i in range(s, e)) / (e - s)
            sure = mean >= kind.threshold and mean - runner_up >= max(kind.margin, LANE_MARGIN.get(lane, 0.0))
            lanes += [lane if sure else "other"] * (e - s)
        oracle.remember("block", key, Verdict(json.dumps(lanes), round(sum(max(s.values()) for s in scores) / len(scores), 4), 0.0))
    if len(lanes) != len(prose):
        return items, front_end
    front_end = start  # only now: the boundary moves with the headers, never without them
    out = list(items)
    inserted = 0
    for start, end, lane in _runs(lanes):
        at = prose[start][0] + inserted
        heading = CANONICAL.get(lane, UNTITLED)
        header = {"self_ref": f"#/texts/built~{start}", "parent": {"$ref": "#/body"}, "children": [], "label": "section_header", "text": heading, "level": 1, "prov": list(prose[start][1].get("prov") or []), "_built": True, "_built_lane": lane}
        out.insert(at, header)
        inserted += 1
        repairs["built_headings"] = repairs.get("built_headings", 0) + 1
        rec = getattr(repairs, "record", None)
        if rec is not None:
            prov = (prose[start][1].get("prov") or [{}])[0]
            rec("built_headings", page=prov.get("page_no") if isinstance(prov, dict) else None,
                before=(prose[start][1].get("text") or "")[:200], after=heading,
                why=f"the paper printed no headings: these blocks read as {lane}, so a heading stands in")
    return out, front_end


def lane_sections(tree: Any, oracle: Oracle | None, repairs: dict[str, int]) -> None:
    """The post-pass: a top-level section whose heading names nothing takes the lane its
    paragraphs are clearly of, when that lane has a shape of its own; a section whose
    heading did name a lane has a strong disagreement noted, not applied."""
    if oracle is None:
        return
    from .tree import _descendants  # noqa: PLC0415 - tree imports this module

    kind = oracle.kinds["block"]
    changed = False
    order = [n for n in tree.walk() if n.type == "paragraph"]
    place = {id(n): i / max(len(order) - 1, 1) for i, n in enumerate(order)}  # where in the paper each paragraph sits
    for section in tree.root.children:
        if section.type != "section" or section.level != 1 or section.label == "built" or section.heading in ("Front matter", "Abstract") or section.role in ("abstract", "references", "back"):
            continue
        nodes = [n for n in _descendants(section) if n.type == "paragraph" and _words(n.text) >= 8]
        paras = [n.text.strip() for n in nodes]
        if len(paras) < 3:
            continue
        key = _key(paras) + f"@{RULE}/{sorted(LANE_MARGIN.items())}"
        v = oracle.recall("block", key)
        if v is None:
            scores = oracle.scores("block", paras, [place.get(id(n), 0.5) for n in nodes])
            if scores is None:
                break  # the embedder is gone: what was changed above is still recounted below
            mean = {l: sum(s.get(l, 0.0) for s in scores) / len(scores) for l in LANES}
            ranked = sorted(mean.items(), key=lambda x: (-x[1], x[0]))
            name, score = ranked[0]
            margin = score - ranked[1][1]
            v = Verdict(name if score >= kind.threshold and margin >= max(kind.margin, LANE_MARGIN.get(name, 0.0)) else "other", round(score, 4), round(margin, 4))
            oracle.remember("block", key, v)
        if section.role == "other":
            if v.sure and v.name in CONTENT_LANES:
                for n in _descendants(section):
                    n.role = v.name
                repairs["laned"] = repairs.get("laned", 0) + 1
                rec = getattr(repairs, "record", None)
                if rec is not None:
                    rec("laned", page=section.page, node_id=section.node_id, before=section.heading, after=v.name,
                        why=f"the heading names nothing the vocabulary knows; its paragraphs read as {v.name} (cosine {v.score}, margin {v.margin})")
                changed = True
            elif v.sure:
                tree.notes.append({"kind": "lane-suggested", "node_id": section.node_id, "page": section.page, "message": f"the paragraphs read as {v.name} (cosine {v.score}, margin {v.margin}); the heading names nothing, and only methods, results (with or without discussion) or references are taken from content"})
        elif v.sure and v.name in NOTE_LANES and v.name != section.role and not (v.name == "results-discussion" and section.role in ("results", "discussion")) and not (section.role == "results-discussion" and v.name in ("results", "discussion")):
            repairs["lane_disagreement"] = repairs.get("lane_disagreement", 0) + 1
            rec = getattr(repairs, "record", None)
            if rec is not None:
                rec("lane_disagreement", page=section.page, node_id=section.node_id, before=section.heading, after=f"reads as {v.name}",
                    why=f"the heading names {section.role}; the paragraphs read as {v.name} — noted, and the heading stands")
            tree.notes.append({"kind": "lane-disagreement", "node_id": section.node_id, "page": section.page, "message": f"the heading names {section.role}, the paragraphs read as {v.name} (cosine {v.score}, margin {v.margin}); the heading stands"})
    if changed:
        roles: dict[str, int] = {}
        for n in tree.walk():
            if n is not tree.root:
                roles[n.role] = roles.get(n.role, 0) + 1
        tree.roles = dict(sorted(roles.items()))
        tree.has_methods = roles.get("methods", 0) > 0
