"""Does each node make sense next to its neighbours?

Docling reads a paper into items and `tree.py` files them; both get things wrong in ways
a person spots at once — a paragraph that is one letter, a sentence that starts with
"=", an equation that vanished between "the equation below:" and "where …", a heading
echoed after a page break, a journal's logo filed as a figure on every page. This walks a
tree in reading order and names those, node by node, so a fix can be tested and a library
checked after every change to the reader.

    python -m litrag_parser.audit parser/tests/fixtures/*.docling.json
    python -m litrag_parser.audit --lib ~/.protracker/library/looped-ligament [--json]

The worker answers the same as the `audit` op. Findings are graded: `error` is a node
that is wrong, `warn` is one that is probably wrong, `info` is a fact worth a look.
"""

from __future__ import annotations

import glob
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .tree import Node, Tree, build_tree

SEVERITY = {"error": 0, "warn": 1, "info": 2}
_CONTENT = {"paragraph", "list_item", "formula", "code", "footnote", "caption", "table", "picture"}
_PROSE = {"paragraph", "list_item", "footnote"}
_TERMINAL = '.!?:;)]"\'”’…'
_GENERIC_TITLES = {"original research", "original article", "research article", "article", "review", "review article", "abstract", "introduction", "letter", "communication", "full paper", "short communication", "editorial", "case report", "brief report", "untitled"}
_COLON_LEADS = re.compile(r"(equations?|formulae?|formulas|expression|relation|given by|defined as|calculated (?:as|by|using|according to|with|from)|computed (?:as|by|using|from)|expressed as|determined (?:as|by|using|from)|estimated (?:as|by|using|from)|obtained (?:as|by|using|from)|law|ratio was calculated)\s*[:.]?\s*$", re.I)
_EQUATION_LINE = re.compile(r"^[^.]{0,80}[=≈≤≥<>]|^\(?\d{1,2}[.)]\s|^[a-z]\)\s|^[•·\-–]\s")  # a line that is an equation, or the first item of a list
_SYMBOL_START = re.compile(r"^[=/)\],;:%+<>|&_]")  # not * or †: footnote markers
_UNTITLED = re.compile(r"heading not detected|untitled", re.I)


@dataclass
class Finding:
    kind: str
    severity: str
    node_id: str
    message: str
    text: str
    page: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _preview(text: str, n: int = 120) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text).strip().lower()


def _reading_order(tree: Tree) -> list[Node]:
    return [n for n in tree.walk() if n.type in _CONTENT]


def audit_tree(tree: Tree, paper_type: str | None = None) -> list[Finding]:  # noqa: C901 - one block per rule
    """Every node against its neighbours, and the paper against what its type promises:
    `paper_type` (paper_type.py) makes a missing methods section a warning for research
    and nothing for a review, a letter, an editorial, a correction."""
    out: list[Finding] = []

    def add(kind: str, severity: str, node: Node, message: str) -> None:
        out.append(Finding(kind, severity, node.node_id, message, _preview(node.text or node.heading or ""), node.page))

    if not any(n.type in ("paragraph", "list_item", "table", "picture", "formula") for n in tree.walk()):
        add("empty-document", "error", tree.root, "no paragraph, list, table or figure at all: the reader produced nothing")
        return out

    # --- the paper -------------------------------------------------------------------------
    title = (tree.title or "").strip()
    if not title or title.lower() in _GENERIC_TITLES or (title.isupper() and len(title.split()) <= 3):
        add("generic-title", "warn", tree.root, f"the title is {title!r}: a running head or a heading taken for the title")
    if not tree.has_methods and paper_type not in ("review", "letter", "editorial", "correction", "data"):
        add("no-methods", "warn" if paper_type in ("research", "case-report", "protocol") else "info", tree.root, f"no methods section found ({'a ' + paper_type + ' paper should have one' if paper_type in ('research', 'case-report', 'protocol') else 'fine for a review; a parse failure for a study'})")
    prose = [n for n in tree.walk() if n.type in _PROSE]
    other = sum(1 for n in prose if n.role == "other")
    if prose and other / len(prose) > 0.5:
        add("other-heavy", "info", tree.root, f"{other} of {len(prose)} prose nodes are `other`: headings matched nothing in the vocabulary")

    # --- sections ---------------------------------------------------------------------------
    seen_headings: list[tuple[str, Node]] = []
    for n in tree.walk():
        if n.type != "section":
            continue
        if not n.children:
            add("empty-section", "info", n, "a section with nothing in it")
        if n.heading is None or _UNTITLED.search(n.heading or ""):
            add("untitled-section", "info", n, "a section whose heading was not detected")
            continue
        key = _norm(n.heading)
        if key and len(key) > 3:
            for prev_key, prev in seen_headings[-1:]:
                if prev_key == key and prev.parent == n.parent:
                    add("echoed-heading", "error", n, f"heading repeats {prev.node_id}: a heading echoed across a page break, or a running head")
            seen_headings.append((key, n))
        if title and key == _norm(title) and n.parent != tree.root.node_id:
            add("title-as-heading", "warn", n, "a section carries the paper's title as its heading")
        if n.label == "built":
            add("built-heading", "info", n, f"a heading the reader built from the paragraphs' content ({n.role}): the paper printed none here")
    # what the reader noticed and did not act on: a section whose paragraphs read as another lane
    by_id = {n.node_id: n for n in tree.walk()}
    for note in tree.notes:
        node = by_id.get(str(note.get("node_id")))
        if node is not None:
            add(str(note.get("kind", "note")), "info", node, str(note.get("message", "")))

    # --- nodes against their neighbours --------------------------------------------------
    order = _reading_order(tree)
    for i, n in enumerate(order):
        prev = order[i - 1] if i > 0 else None
        nxt = order[i + 1] if i + 1 < len(order) else None
        text = (n.text or "").strip()
        if n.type == "picture":
            if not any(c.type == "caption" for c in n.children):
                small = n.bbox is not None and (n.bbox[2] - n.bbox[0]) < 60 and (n.bbox[3] - n.bbox[1]) < 60
                add("uncaptioned-picture", "warn" if small else "info", n, "a picture with no caption" + (", and tiny: a logo or a rule" if small else ""))
            continue
        if n.type == "caption":
            parent = next((p for p in tree.walk() if p.node_id == n.parent), None)
            if parent is not None and parent.type not in ("table", "picture"):
                add("orphan-caption", "warn", n, "a caption that belongs to no table or figure")
            continue
        if n.type == "table":
            if not n.table or not n.table.get("cells"):
                add("empty-table", "warn", n, "a table with no cells")
            continue
        if n.type in ("formula", "code"):
            if not text:
                add("unread-formula", "warn", n, f"a {n.type} with no text: Docling saw one on the page but did not read it (formula enrichment is off)")
            elif text.startswith("[equation as image"):
                add("image-formula", "warn", n, "an equation the file carries only as an image: its place is marked, its text is not there")
            elif len(text) <= 1:
                add("fragment", "error", n, f"a {n.type} of one character")
            continue
        if not text:
            add("empty-node", "error", n, f"a {n.type} with no text")
            continue
        words = re.findall(r"[^\W_]+", text)
        if n.type == "paragraph" and (len(text) <= 2 or not words):
            add("fragment", "error", n, "a paragraph that is a fragment: a run of a split sentence")
            continue
        if n.type == "paragraph" and len(words) <= 2 and text[-1] not in ".!?":
            add("stray-words", "warn", n, "a paragraph of a word or two with no full stop: a label or a run cut loose")
            continue
        if _SYMBOL_START.match(text) and n.type != "footnote":
            add("symbol-start", "error", n, f"a {n.type} starting with {text[0]!r}: the rest of a sentence cut in front of it")
            continue
        # Layout text from a PDF is full of doubled spaces; only XML text is tidy enough for this to mean anything.
        if n.role != "references" and ("( )" in text or "()" in text or (not tree.pages and ("  " in text or " ," in text))):
            add("skipped-inline", "warn", n, "a double space or empty brackets: an inline formula or term was skipped")
        if n.type == "paragraph" and text[0].islower() and not (prev is not None and prev.type == "formula"):
            if prev is not None and prev.type == "paragraph" and prev.text.strip() and prev.text.strip()[-1] not in _TERMINAL:
                add("split-paragraph", "warn", n, f"starts lowercase after an unterminated paragraph {prev.node_id}: one paragraph split in two")
            else:
                add("lowercase-start", "warn", n, "a paragraph starting lowercase: a continuation filed on its own")
        if n.type == "paragraph" and text.endswith(":"):
            if nxt is None or nxt.type == "paragraph":
                lead = bool(_COLON_LEADS.search(text[:-1].strip()[-40:]))
                where = nxt is not None and nxt.text.strip().lower().startswith(("where ", "in which "))
                satisfied = nxt is not None and nxt.type == "paragraph" and bool(_EQUATION_LINE.match(nxt.text.strip()))  # the equation as prose, or a list
                if not satisfied and (lead or where):
                    add("missing-equation", "error", n, "ends with a colon and no equation follows: the equation was dropped")
                elif not satisfied and nxt is None:
                    add("dangling-colon", "warn", n, "ends with a colon and nothing follows")
        if prev is not None and prev.type == n.type and len(text) > 40 and prev.parent == n.parent and _norm(text) == _norm(prev.text or ""):
            add("echoed-node", "error", n, f"the same text as {prev.node_id}: a duplicate")  # "Not applicable." under two headings is not one
    out.sort(key=lambda f: SEVERITY.get(f.severity, 9))
    return out


def audit_doc(doc: dict[str, Any], key: str) -> tuple[Tree, list[Finding]]:
    tree = build_tree(doc, key)
    return tree, audit_tree(tree)


def summarize(findings: Iterable[Finding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.kind] = counts.get(f.kind, 0) + 1
    return dict(sorted(counts.items()))


def report(key: str, tree: Tree, findings: list[Finding], *, min_severity: str = "info") -> str:
    keep = [f for f in findings if SEVERITY[f.severity] <= SEVERITY[min_severity]]
    lines = [f"{key} — {tree.title[:80]}", f"  {sum(1 for _ in tree.walk())} nodes · " + (", ".join(f"{k} {v}" for k, v in summarize(keep).items()) or "nothing to report")]
    for f in keep:
        page = f" p.{f.page}" if f.page else ""
        lines.append(f"  [{f.severity}] {f.kind:<20} {f.node_id}{page}\n      {f.message}\n      ‹{f.text}›")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    min_severity = "info"
    for level in ("--errors", "--warnings"):
        if level in args:
            min_severity = "error" if level == "--errors" else "warn"
            args.remove(level)
    if as_json:
        args.remove("--json")
    paths: list[Path] = []
    while args:
        a = args.pop(0)
        if a == "--lib":
            paths.extend(sorted((Path(args.pop(0)) / "parsed").glob("*.docling.json")))
        else:
            paths.extend(sorted(Path(m) for m in glob.glob(a)) or [Path(a)])
    if not paths:
        print(__doc__)
        return 2
    results = []
    for p in paths:
        key = p.name.replace(".docling.json", "")
        tree, findings = audit_doc(json.loads(p.read_text("utf-8")), key)
        results.append((key, tree, findings))
    if as_json:
        json.dump([{"key": k, "title": t.title, "nodes": sum(1 for _ in t.walk()), "counts": summarize(f), "findings": [x.to_dict() for x in f]} for k, t, f in results], sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        for k, t, f in results:
            print(report(k, t, f, min_severity=min_severity))
            print()
    return 1 if any(x.severity == "error" for _, _, f in results for x in f) else 0


if __name__ == "__main__":
    sys.exit(main())
