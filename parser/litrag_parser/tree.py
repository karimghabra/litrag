"""A DoclingDocument into litrag's node tree.

Docling gives a body of items — headers, paragraphs, tables, pictures,
captions, lists — each with a label and provenance. Section headers carry a
level but sit beside the paragraphs they head, so the hierarchy is rebuilt
here: a stack by header level, every item filed under the nearest header
above it. The role of a node is the role of the *top-level* section it is
under, inherited all the way down, so a paragraph three levels beneath
"2. Materials and Methods" is methods whatever its own subheading says.

Nothing here talks to Docling's models; it reads a finished document, so it
runs the same over a saved JSON as over a fresh conversion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

from .facets import role_of

# Docling labels that are structure, not prose; kept as nodes with their own type.
_TABLE = {"table", "document_index"}
_PICTURE = {"picture", "chart"}
_CAPTION = {"caption"}
_HEADER = {"section_header", "title"}
_LIST = {"list_item"}
_SKIP = {"page_header", "page_footer"}  # running heads: noise for retrieval, kept out of the tree
_PROSE = {"text", "paragraph", "footnote", "formula", "code", "reference", "checkbox_selected", "checkbox_unselected", "key_value_region", "form"}


@dataclass
class Node:
    node_id: str
    parent: str | None
    ordinal: int
    depth: int
    type: str  # document | section | paragraph | table | picture | caption | list_item | formula | code | footnote
    label: str  # Docling's label, verbatim
    level: int | None  # header level for sections
    role: str
    heading: str | None  # a section's own heading
    ancestry: list[str]  # headings from the top down, this section's own excluded
    text: str
    page: int | None
    bbox: list[float] | None  # [l, t, r, b] with the page's origin top-left, in PDF points
    self_ref: str | None  # Docling's ref, for going back to the raw document
    charspan: list[int] | None = None
    table: dict[str, Any] | None = None  # {"rows": int, "cols": int, "cells": [[text,...],...]} for tables
    children: list["Node"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class Page:
    page_no: int
    width: float
    height: float


@dataclass
class Tree:
    title: str
    pages: list[Page]
    root: Node
    #: how many nodes landed in each role — the parse's own report card
    roles: dict[str, int]
    #: papers with no methods section are legitimate (reviews) or a parse failure; the count says which to look at
    has_methods: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "pages": [asdict(p) for p in self.pages],
            "roles": self.roles,
            "has_methods": self.has_methods,
            "root": self.root.to_dict(),
        }

    def walk(self) -> Iterable[Node]:
        stack = [self.root]
        while stack:
            n = stack.pop()
            yield n
            stack.extend(reversed(n.children))


def _bbox_top_left(prov: dict[str, Any], page: Page | None) -> list[float] | None:
    b = prov.get("bbox")
    if not b:
        return None
    l, t, r, btm = float(b["l"]), float(b["t"]), float(b["r"]), float(b["b"])
    origin = b.get("coord_origin", "BOTTOMLEFT")
    if origin == "BOTTOMLEFT" and page is not None:
        # Docling's default: y grows upward from the page's bottom edge.
        t, btm = page.height - t, page.height - btm
    return [round(l, 2), round(min(t, btm), 2), round(r, 2), round(max(t, btm), 2)]


def _first_prov(item: dict[str, Any]) -> dict[str, Any] | None:
    prov = item.get("prov") or []
    return prov[0] if prov else None


def _table_cells(item: dict[str, Any]) -> dict[str, Any] | None:
    data = item.get("data") or {}
    grid = data.get("grid")
    if not grid:
        return None
    cells = [[(c.get("text") or "").strip() for c in row] for row in grid]
    return {"rows": len(cells), "cols": max((len(r) for r in cells), default=0), "cells": cells}


def _resolve(doc: dict[str, Any], ref: str) -> dict[str, Any] | None:
    # "#/texts/12" → doc["texts"][12]
    parts = ref.lstrip("#/").split("/")
    cur: Any = doc
    for p in parts:
        if isinstance(cur, list):
            try:
                cur = cur[int(p)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return None
    return cur if isinstance(cur, dict) else None


def _body_items(doc: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """The body in reading order, groups flattened, each item once."""
    seen: set[str] = set()

    def visit(ref: str) -> Iterable[dict[str, Any]]:
        item = _resolve(doc, ref)
        if item is None:
            return
        self_ref = item.get("self_ref", ref)
        if self_ref in seen:
            return
        seen.add(self_ref)
        children = [c["$ref"] for c in item.get("children", []) if "$ref" in c]
        if self_ref.startswith("#/groups") or self_ref == "#/body":
            for c in children:
                yield from visit(c)
            return
        yield item
        # Tables and pictures own their captions; a caption is filed under them below,
        # so their children are not walked as body items.
        if item.get("label") in _TABLE | _PICTURE:
            return
        for c in children:
            yield from visit(c)

    yield from visit("#/body")

_NUMBERED = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+\S")


def numbering_depth(heading: str) -> int | None:
    """"2.1.3 Cell culture" → 3; "Results" → None."""
    m = _NUMBERED.match(heading)
    return m.group(1).count(".") + 1 if m else None


def undouble(text: str) -> str:
    """"3.4. Simulation Results 3.4. Simulation Results" → "3.4. Simulation Results"."""
    t = text.strip()
    if len(t) % 2 == 1:
        half = t[: len(t) // 2]
        if half and t == f"{half} {half}":
            return half
    return t


def top_number(heading: str) -> str | None:
    """"3.2 Effect of…" → "3"; unnumbered → None."""
    m = _NUMBERED.match(heading)
    return m.group(1).split(".")[0] if m else None


def infer_level(heading: str, docling_level: int, open_top: bool) -> int:
    """A header's depth when the layout model gives every header the same level.

    Numbering wins when present. A heading that names a lane (Methods, Results,
    Discussion…) is top-level whatever it looked like on the page. Anything else
    beneath an open top-level section is that section's child, one level down,
    unless the layout model already placed it deeper.
    """
    depth = numbering_depth(heading)
    if depth is not None:
        return depth
    if role_of(heading) != "other":
        return 1
    if open_top:
        return max(docling_level, 2)
    return max(docling_level, 1)


_MERGED = re.compile(r"^(?P<head>(?:\d+\.\s+)?[A-Za-z][^.]{2,60}?)\s+(?P<num>\d+)\.(?P<sub>\d+)\.?\s+(?P<rest>\S.*)$")


def rescue_merged_heading(item: dict[str, Any]) -> list[dict[str, Any]] | None:
    """"Experimental Results 3.1. Quantification of …" → two headers, "3. Experimental Results" and "3.1. Quantification of …".

    A failure mode of the layout model: a top-level heading and the subheading
    under it read as one line, labelled a list item or a paragraph. Only a head
    that names a lane is rescued; anything else stays what Docling said it was.
    """
    if item.get("label") not in ("list_item", "text", "paragraph"):
        return None
    text = (item.get("text") or "").strip()
    if len(text) > 240:
        return None
    m = _MERGED.match(text)
    if not m:
        return None
    head = re.sub(r"^\d+\.\s+", "", m.group("head")).strip()
    if role_of(head) == "other":
        return None
    num, sub, rest = m.group("num"), m.group("sub"), m.group("rest").strip()
    # the subheading may itself be echoed: cut at a second "N.M." if one appears
    echo = re.search(rf"\s+{num}\.{sub}\.?\s", rest)
    if echo:
        rest = rest[: echo.start()].strip()
    base = {k: v for k, v in item.items() if k not in ("text", "label", "children")}
    top = {**base, "label": "section_header", "level": 1, "text": f"{num}. {head}", "self_ref": f"{item.get('self_ref', '')}~top"}
    below = {**base, "label": "section_header", "level": 2, "text": f"{num}.{sub}. {rest}", "self_ref": f"{item.get('self_ref', '')}~sub"}
    return [top, below]


def _same_heading(a: str, b: str) -> bool:
    return re.sub(r"\W+", " ", a).strip().lower() == re.sub(r"\W+", " ", b).strip().lower()


def build_tree(doc: dict[str, Any], key: str) -> Tree:
    """The node tree for one document, given Docling's exported dict."""
    pages = {int(k): Page(page_no=int(k), width=float(v["size"]["width"]), height=float(v["size"]["height"])) for k, v in (doc.get("pages") or {}).items()}
    title = (doc.get("name") or key).strip()

    root = Node(node_id=key, parent=None, ordinal=0, depth=0, type="document", label="document", level=None, role="other", heading=None, ancestry=[], text="", page=None, bbox=None, self_ref="#/body")
    # stack of (level, node); the root sits at level 0
    stack: list[tuple[int, Node]] = [(0, root)]
    counters: dict[str, int] = {}
    roles: dict[str, int] = {}
    caption_refs: set[str] = set()

    def next_id(parent: Node, kind: str) -> str:
        counters[parent.node_id] = counters.get(parent.node_id, 0) + 1
        return f"{parent.node_id}#{kind}-{counters[parent.node_id]}"

    def current_role(parent: Node) -> str:
        # The role of the top-level section, inherited; the document itself is `other`.
        for level, node in stack:
            if level == 1:
                return node.role
        return parent.role if parent.type == "section" else "other"

    def attach(parent: Node, node: Node) -> None:
        node.ordinal = len(parent.children)
        node.depth = parent.depth + 1
        parent.children.append(node)
        roles[node.role] = roles.get(node.role, 0) + 1

    def make(parent: Node, kind: str, item: dict[str, Any], text: str, role: str, heading: str | None = None, level: int | None = None) -> Node:
        prov = _first_prov(item)
        page = pages.get(int(prov["page_no"])) if prov and "page_no" in prov else None
        ancestry = [n.heading for lvl, n in stack if lvl > 0 and n.heading]
        return Node(
            node_id=next_id(parent, kind),
            parent=parent.node_id,
            ordinal=0,
            depth=0,
            type=kind,
            label=str(item.get("label", kind)),
            level=level,
            role=role,
            heading=heading,
            ancestry=ancestry,
            text=text,
            page=int(prov["page_no"]) if prov and "page_no" in prov else None,
            bbox=_bbox_top_left(prov, page) if prov else None,
            self_ref=item.get("self_ref"),
            charspan=list(prov["charspan"]) if prov and prov.get("charspan") else None,
        )

    first_title_taken = False
    prose_count = 0
    last_heading: str | None = None
    prose_since_heading = False
    has_title_label = any(t.get("label") == "title" for t in doc.get("texts", []))
    items: list[dict[str, Any]] = []
    for item in _body_items(doc):
        rescued = rescue_merged_heading(item)
        items.extend(rescued if rescued else [item])
    for item in items:
        label = item.get("label", "text")
        self_ref = item.get("self_ref", "")
        if label in _SKIP or self_ref in caption_refs:
            continue
        text = (item.get("text") or "").strip()
        if label not in _HEADER and text:
            prose_count += 1
            prose_since_heading = True

        if label in _HEADER:
            if label == "title" and not first_title_taken:
                # A title item is the paper's title; the file name was only ever a stand-in.
                first_title_taken = True
                if text:
                    title = text
                root.text = text
                continue
            if not first_title_taken and label == "section_header" and not has_title_label and not any(n.heading != "Front matter" for lvl, n in stack[1:]) and prose_count <= 4:
                # The layout model labelled the paper's title a section header: the first
                # header before any prose, in a document with no title item, is the title.
                first_title_taken = True
                if text:
                    title = text
                    root.text = text
                continue
            text = undouble(text)
            if last_heading is not None and not prose_since_heading and _same_heading(text, last_heading):
                # The same heading twice with nothing between: a page break echoed it.
                continue
            level = int(item.get("level") or 1)
            if label == "title":
                level = 1
            else:
                level = infer_level(text, level, any(lvl == 1 for lvl, _ in stack))
            number = top_number(text)
            open_top = next((n for lvl, n in stack if lvl == 1), None)
            if level > 1 and number is not None and open_top is not None and open_top.heading and top_number(open_top.heading) not in (None, number):
                # "3.2 …" arrives while "2. Methods" is open and no "3." was seen: the layout
                # model dropped the parent heading. Stand in an untitled section rather than
                # file results under methods; its role is `other`, which is honest.
                while len(stack) > 1:
                    stack.pop()
                ghost = make(root, "section", {"label": "section_header"}, "", "other", heading=f"{number}. (heading not detected)", level=1)
                attach(root, ghost)
                stack.append((1, ghost))
            open_here = next((n for lvl, n in stack if lvl == level), None)
            if open_here is not None and open_here.heading and _same_heading(text, open_here.heading):
                # "4. Discussion" again after a page break: the same section continues.
                while stack[-1][1] is not open_here:
                    stack.pop()
                last_heading = text
                prose_since_heading = False
                continue
            # pop to the nearest ancestor shallower than this header
            while len(stack) > 1 and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1]
            last_heading = text
            prose_since_heading = False
            role = role_of(text) if level == 1 else current_role(parent)
            node = make(parent, "section", item, "", role, heading=text or "(untitled section)", level=level)
            attach(parent, node)
            stack.append((level, node))
            continue

        parent = stack[-1][1]
        if parent is root and text:
            # Prose before the first heading — authors, affiliations, dates, the abstract when
            # its heading was not detected — is front matter: kept, in a section that says so.
            front = make(root, "section", {"label": "section_header"}, "", "other", heading="Front matter", level=1)
            attach(root, front)
            stack.append((1, front))
            parent = front
        role = current_role(parent)
        if label in _TABLE:
            node = make(parent, "table", item, text, role)
            node.table = _table_cells(item)
            attach(parent, node)
            for cap in item.get("captions", []):
                cap_item = _resolve(doc, cap["$ref"]) if "$ref" in cap else None
                if cap_item:
                    caption_refs.add(cap_item.get("self_ref", cap["$ref"]))
                    c = make(node, "caption", cap_item, (cap_item.get("text") or "").strip(), role)
                    attach(node, c)
            continue
        if label in _PICTURE:
            node = make(parent, "picture", item, text, role)
            attach(parent, node)
            for cap in item.get("captions", []):
                cap_item = _resolve(doc, cap["$ref"]) if "$ref" in cap else None
                if cap_item:
                    caption_refs.add(cap_item.get("self_ref", cap["$ref"]))
                    c = make(node, "caption", cap_item, (cap_item.get("text") or "").strip(), role)
                    attach(node, c)
            continue
        if label in _CAPTION:
            attach(parent, make(parent, "caption", item, text, role))
            continue
        if label in _LIST:
            attach(parent, make(parent, "list_item", item, text, role))
            continue
        kind = "paragraph"
        if label in ("formula", "code", "footnote"):
            kind = label
        if not text and kind == "paragraph":
            continue
        attach(parent, make(parent, kind, item, text, role))

    return Tree(
        title=title,
        pages=[pages[k] for k in sorted(pages)],
        root=root,
        roles=dict(sorted(roles.items())),
        has_methods=roles.get("methods", 0) > 0,
    )
