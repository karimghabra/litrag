"""The reader's work, page by page, for a person to check.

The harness says a corpus is 0.93 of something; the audit lists what looks wrong. Neither shows what the
reader actually *did* to a paper. This writes one page per paper — every page of it, drawn, with every node
the reader kept boxed in its lane's colour and every modification it made marked where it happened, the text
as it stood beside the text as it stands — and an index over a set of papers so that a reader's behaviour can
be checked across formats and publishers rather than on the one paper that was to hand.

A verdict on its own cannot be checked, so each verdict is shown with what it was read from: the type's table
lists every source's label beside the type it implies and says which one won; the citations are listed one by
one, the marker as printed inside the sentence it sits in and the entry it points at; and a JATS paper, which
has no pages to draw, gets the section outline, the changes the XML preparation made to the file before
Docling saw it, and the changes the reader made under each section.

    uv run --project parser python -m litrag_parser.review --lib DIR [--lib DIR] --out DIR
                                                           [--limit N] [--key KEY] [--format pdf|jats]
                                                           [--changed-only] [--scale 1.6]

Nothing is written to a library: the trees are rebuilt in memory exactly as `rebuild` would
(`harness.read_paper`), and the pages are rendered from the paper's own file. A paper that fails is caught,
named in the index with its error, and the run goes on.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from . import citations as citations_module
from . import lanes
from .audit import audit_tree
from .changes import stage_of
from .citations import Citation, Ref, link_citations, summarize as summarize_citations
from .confidence import assess as assess_confidence
from .harness import read_paper
from .library import parsed_papers, safe_key
from .paper_type import evidence_of, shape_of
from .paper_type import _summary as shape_sentence  # the same sentence `decide` puts in `detail`
from .tree import Node, Tree

#: a lane's colour, for the boxes of what was kept
LANE_COLOURS: dict[str, str] = {
    "abstract": "#7c3aed",
    "introduction": "#2563eb",
    "methods": "#0891b2",
    "results": "#16a34a",
    "results-discussion": "#65a30d",
    "discussion": "#ca8a04",
    "references": "#94a3b8",
    "back": "#a1a1aa",
    "other": "#d4d4d8",
}

#: a change's colour, by the pass that made it
STAGE_COLOURS: dict[str, str] = {
    "dropped": "#dc2626",
    "recovered": "#16a34a",
    "text": "#d97706",
    "joined": "#2563eb",
    "split": "#7c3aed",
    "moved": "#db2777",
    "structure": "#0891b2",
    "xml": "#0d9488",
    "other": "#525252",
}

STAGE_WORDS: dict[str, str] = {
    "dropped": "left out of the body",
    "recovered": "read back from the text layer",
    "text": "the text itself rewritten",
    "joined": "put back together",
    "split": "cut apart",
    "moved": "put in its place",
    "structure": "the paper's shape",
    "xml": "the publisher's file, before Docling read it",
    "other": "other",
}

#: who published a paper, read from its DOI prefix — a reader behaves differently on Elsevier's
#: files than on MDPI's, and the index is the place that difference shows. A prefix not listed
#: stands for itself: naming it wrongly would be worse than not naming it.
PUBLISHERS: dict[str, str] = {
    "10.1002": "Wiley",
    "10.1016": "Elsevier",
    "10.3390": "MDPI",
    "10.1021": "ACS",
    "10.1039": "RSC",
    "10.1038": "Nature/Springer-Nature",
    "10.1371": "PLOS",
    "10.3389": "Frontiers",
    "10.1093": "OUP",
    "10.1186": "BMC",
    "10.1109": "IEEE",
    "10.1007": "Springer",
    "10.1155": "Hindawi",
    "10.1088": "IOP",
    "10.1177": "SAGE",
    "10.1073": "PNAS",
    "10.1126": "Science",
}

_DOI_PREFIX = re.compile(r"(10\.\d{4,9})/")


def publisher_of(key: str | None) -> str:
    """The publisher behind a paper key, by its DOI prefix; the prefix itself when unlisted,
    and `no DOI` for a key that carries none (a PMID, a file name)."""
    m = _DOI_PREFIX.search(key or "")
    if not m:
        return "no DOI"
    return PUBLISHERS.get(m.group(1), m.group(1))


def trust_band(confidence: float | None) -> str:
    """The three bands the index filters on, named as the colours say them."""
    if confidence is None:
        return "unscored"
    return "high" if confidence >= 0.9 else "fair" if confidence >= 0.5 else "low"


# ---------------------------------------------------------------------------------------------- pages
def page_images(source: Path | None, out_dir: Path, scale: float) -> dict[int, dict[str, Any]]:
    """Every page of the PDF as a JPEG beside the report, with the size it was drawn at."""
    if source is None or not source.exists() or source.suffix.lower() != ".pdf":
        return {}
    try:
        import pypdfium2 as pdfium
    except Exception:  # pragma: no cover - the review needs the renderer only for PDFs
        return {}
    out_dir.mkdir(parents=True, exist_ok=True)
    images: dict[int, dict[str, Any]] = {}
    doc = pdfium.PdfDocument(str(source))
    try:
        for index in range(len(doc)):
            page = doc[index]
            width, height = page.get_size()
            image = page.render(scale=scale).to_pil().convert("RGB")
            name = f"page-{index + 1:03d}.jpg"
            image.save(out_dir / name, quality=78, optimize=True)
            images[index + 1] = {"file": f"pages/{name}", "w": image.width, "h": image.height,
                                 "pw": float(width), "ph": float(height)}
    finally:
        doc.close()
    return images


def boxes_of(tree: Tree) -> list[dict[str, Any]]:
    """Every node the reader kept that knows where it came from."""
    out: list[dict[str, Any]] = []
    for node in tree.walk():
        if node.page and node.bbox:
            out.append(
                {
                    "page": node.page,
                    "box": node.bbox,
                    "role": node.role,
                    "type": node.type,
                    "heading": node.heading,
                    "canonical": node.canonical,
                    "built": node.label == "built",
                    "text": (node.text or "")[:400],
                    "node_id": node.node_id,
                }
            )
    return out


# ---------------------------------------------------------------------------------------------- html
def _e(text: Any) -> str:
    return html.escape("" if text is None else str(text), quote=True)


def _pct(box: list[float], page: dict[str, Any]) -> tuple[float, float, float, float]:
    """A box in PDF points as a percentage of the page, so the drawing scales with the image."""
    pw, ph = page["pw"] or 1.0, page["ph"] or 1.0
    l, t, r, b = box
    return (100 * l / pw, 100 * t / ph, 100 * max(0.4, r - l) / pw, 100 * max(0.4, b - t) / ph)


def _change_row(change: dict[str, Any], index: int, prefix: str = "c") -> str:
    """One modification as a row. `prefix` keeps the anchors of two lists on one page apart —
    the reader's changes are `c1`, the XML preparation's `x1`."""
    colour = STAGE_COLOURS.get(change.get("stage") or stage_of(change["kind"]), "#525252")
    before, after = change.get("before"), change.get("after")
    body = []
    if before:
        body.append(f'<div class="before"><span class="tag">before</span>{_e(before)}</div>')
    if after:
        body.append(f'<div class="after"><span class="tag">after</span>{_e(after)}</div>')
    if not body and change.get("ref"):
        body.append(f'<div class="muted">{_e(change["ref"])}</div>')
    return (
        f'<li class="change" id="{prefix}{index}" data-page="{_e(change.get("page") or "")}">'
        f'<div class="head"><span class="dot" style="background:{colour}"></span>'
        f'<span class="kind">{_e(change["kind"])}</span>'
        f'<span class="muted">{_e(change.get("why") or "")}</span></div>'
        f'{"".join(body)}</li>'
    )


#: a kind with more rows than this on one list is read as a habit, not as a decision, and is
#: gathered under one heading — the XML preparation brackets a thousand cross-references in a
#: paper and no one reads a thousand rows
_GROUP_AT = 20
#: how many of a gathered kind are shown as examples
_EXAMPLES = 8


def _change_list(changes: list[dict[str, Any]], numbers: dict[int, int] | None = None, prefix: str = "c",
                 group_all: bool = False, cap: int | None = None) -> str:
    """Changes as list items, the kinds that repeat gathered under one heading.

    A kind that appears a handful of times is read one row at a time; a kind that appears hundreds of
    times is one decision applied hundreds of times, and what a reviewer wants of it is the count, the
    rule's own reason once, and a few examples. `cap` drops the rest with a line saying how many were
    dropped (a list with no page to check them against); without it the rest is folded but kept, so
    that a number drawn on a page image still has its row."""
    def number(change: dict[str, Any], fallback: int) -> int:
        return numbers.get(id(change), fallback) if numbers else fallback

    grouped: dict[str, list[dict[str, Any]]] = {}
    for change in changes:
        grouped.setdefault(str(change.get("kind") or "other"), []).append(change)
    if not group_all and all(len(rows) <= _GROUP_AT for rows in grouped.values()):
        # nothing repeats enough to gather: the list stays in the order the passes ran, as it was
        return "".join(_change_row(c, number(c, i + 1), prefix) for i, c in enumerate(changes))
    items: list[str] = []
    seq = 0
    for kind, rows in grouped.items():
        indexed = []
        for change in rows:
            seq += 1
            indexed.append((change, number(change, seq)))
        if not group_all and len(rows) <= _GROUP_AT:
            items += [_change_row(c, n, prefix) for c, n in indexed]
            continue
        shown = indexed[: (cap if cap is not None else _GROUP_AT)]
        rest = indexed[len(shown) :]
        colour = STAGE_COLOURS.get(rows[0].get("stage") or stage_of(kind), "#525252")
        counted = Counter(r.get("why") for r in rows if r.get("why")).most_common(1)
        why = counted[0][0] if counted else None
        shared = why if counted and counted[0][1] == len(rows) else None  # the rule's reason, said once
        head = (f'<div class="head"><span class="dot" style="background:{colour}"></span>'
                f'<span class="kind">{_e(kind)}</span>'
                f'<span class="muted">{len(rows)} of them · showing {len(shown)}'
                + (f' · {_e(why)}' if why else "") + "</span></div>")

        def row(change: dict[str, Any], index: int) -> str:
            if shared and change.get("why") == shared:
                change = {**change, "why": None}
            return _change_row(change, index, prefix)

        body = "".join(row(c, n) for c, n in shown)
        if not rest:
            tail = ""
        elif cap is not None:
            tail = f'<div class="muted small">and {len(rest)} more of this kind, not listed</div>'
        else:
            tail = (f'<details><summary class="muted">the other {len(rest)}</summary>'
                    f'<ol class="changes flat">{"".join(row(c, n) for c, n in rest)}</ol></details>')
        items.append(f'<li class="group">{head}<ol class="changes flat">{body}</ol>{tail}</li>')
    return "".join(items)


def _page_block(page_no: int, page: dict[str, Any], kept: list[dict[str, Any]], changes: list[dict[str, Any]], numbers: dict[int, int]) -> str:
    marks = []
    for box in kept:
        if not box["box"]:
            continue
        l, t, w, h = _pct(box["box"], page)
        colour = LANE_COLOURS.get(box["role"], "#d4d4d8")
        title = f'{box["type"]} · {box["role"]}' + (f' · {box["heading"]}' if box["heading"] else "")
        marks.append(
            f'<div class="kept" style="left:{l:.2f}%;top:{t:.2f}%;width:{w:.2f}%;height:{h:.2f}%;'
            f'border-color:{colour}" title="{_e(title)}"></div>'
        )
    for change in changes:
        if not change.get("box"):
            continue
        l, t, w, h = _pct(change["box"], page)
        colour = STAGE_COLOURS.get(change.get("stage") or stage_of(change["kind"]), "#525252")
        n = numbers.get(id(change))
        marks.append(
            f'<a class="chg" href="#c{n}" style="left:{l:.2f}%;top:{t:.2f}%;width:{w:.2f}%;height:{h:.2f}%;'
            f'border-color:{colour};background:{colour}22" title="{_e(change["kind"])}: {_e(change.get("why") or "")}">'
            f'<span style="background:{colour}">{n}</span></a>'
        )
    listed = _change_list(changes, numbers)
    counts = Counter(c.get("stage") or stage_of(c["kind"]) for c in changes)
    summary = " · ".join(f'<span style="color:{STAGE_COLOURS.get(s, "#525252")}">{n} {_e(s)}</span>' for s, n in counts.most_common()) or '<span class="muted">nothing changed on this page</span>'
    return (
        f'<section class="page" id="page-{page_no}">'
        f'<div class="pagehead"><h3>Page {page_no}</h3><div class="summary">{summary}</div></div>'
        f'<div class="split"><div class="sheet"><img src="{_e(page["file"])}" loading="lazy" alt="page {page_no}">{"".join(marks)}</div>'
        f'<ol class="changes">{listed or "<li class=\'muted\'>no modification on this page</li>"}</ol></div>'
        f"</section>"
    )


# ------------------------------------------------------------------------------------- the sections
def _sections_index(tree: Tree) -> tuple[dict[str, Node], dict[str, str]]:
    """Every node by its id, and for each node the section it sits in — a change names a node,
    and a person checking a paper without pages asks which section it happened under."""
    by_id = {n.node_id: n for n in tree.walk()}
    owner: dict[str, str] = {}
    for node in tree.walk():
        here: Node | None = node
        seen: set[str] = set()
        while here is not None and here.type != "section" and here.node_id not in seen:
            seen.add(here.node_id)
            here = by_id.get(here.parent) if here.parent else None
        if here is not None and here.type == "section":
            owner[node.node_id] = here.node_id
    return by_id, owner


def _changes_by_section(changes: list[dict[str, Any]], owner: dict[str, str]) -> dict[str | None, list[dict[str, Any]]]:
    """The reader's changes filed under the section each happened in; `None` for a change that
    names no node (the passes that run before the tree exists say only where on the page)."""
    out: dict[str | None, list[dict[str, Any]]] = {}
    for change in changes:
        node_id = change.get("node_id")
        section = owner.get(node_id) if node_id else None
        if section is None and node_id:
            section = node_id  # a change on a section itself
        out.setdefault(section, []).append(change)
    return out


#: the same lane colour, darkened where the box's colour is too pale to read as text
LANE_INK: dict[str, str] = {"other": "#71717a", "back": "#71717a", "references": "#64748b"}


def _lane_ink(role: str) -> str:
    return LANE_INK.get(role, LANE_COLOURS.get(role, "#525252"))


def _heading_label(node: Node) -> str:
    """A heading with a dot per level of depth, so the outline reads as an outline."""
    return f"{'·' * max(0, (node.level or 1) - 1)} {node.heading or '(untitled)'}".strip()


def _headings_table(tree: Tree, per_section: dict[str | None, list[dict[str, Any]]] | None = None) -> str:
    per_section = per_section or {}
    rows = []
    for node in tree.walk():
        if node.type != "section" or not node.heading:
            continue
        n_changes = len(per_section.get(node.node_id, []))
        rows.append(
            f"<tr><td><a href='#sec-{_e(safe_key(node.node_id))}'>{_e(_heading_label(node))}</a></td>"
            f"<td>{_e(node.canonical or '—')}</td><td><span class='lane' style='background:{LANE_COLOURS.get(node.role, '#d4d4d8')}22;"
            f"color:{_lane_ink(node.role)}'>{_e(node.role)}</span></td>"
            f"<td>{'built' if node.label == 'built' else ''}</td>"
            f"<td class='num'>{n_changes or ''}</td>"
            f"<td class='muted'>{_e(node.page or '')}</td></tr>"
        )
    return ("<table class='grid'><thead><tr><th>heading, as printed</th><th>the catalogue's name</th><th>lane</th><th></th>"
            "<th class='num'>changes</th><th>page</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def _section_changes_block(tree: Tree, per_section: dict[str | None, list[dict[str, Any]]], numbers: dict[int, int]) -> str:
    """What the reader did under each section, for a paper with no pages to mark it on."""
    blocks = []
    for node in tree.walk():
        if node.type != "section" or not node.heading:
            continue
        here = per_section.get(node.node_id, [])
        counts = Counter(c.get("stage") or stage_of(c["kind"]) for c in here)
        summary = " · ".join(
            f'<span style="color:{STAGE_COLOURS.get(s, "#525252")}">{n} {_e(s)}</span>' for s, n in counts.most_common()
        ) or '<span class="muted">nothing changed here</span>'
        listed = _change_list(here, numbers, cap=_EXAMPLES)
        body = f'<ol class="changes flat">{listed}</ol>' if listed else ""
        open_attr = " open" if here else ""
        blocks.append(
            f'<details class="sec" id="sec-{_e(safe_key(node.node_id))}"{open_attr}><summary>'
            f'<span class="lane" style="background:{LANE_COLOURS.get(node.role, "#d4d4d8")}22;'
            f'color:{_lane_ink(node.role)}">{_e(node.role)}</span> '
            f'<b>{_e(_heading_label(node))}</b> <span class="summary">{summary}</span></summary>{body}</details>'
        )
    orphans = per_section.get(None, [])
    if orphans:
        listed = _change_list(orphans, numbers, cap=_EXAMPLES)
        blocks.append(
            '<details class="sec" open><summary><b>Changes that name no node</b> '
            f'<span class="summary">{len(orphans)} of them — the pass that made them ran before the tree existed</span>'
            f'</summary><ol class="changes flat">{listed}</ol></details>'
        )
    return "".join(blocks)


# ----------------------------------------------------------------------------------- what kind of paper
#: what each source is, in one line, so the table does not need a key
_TRUST_WORDS: dict[str, str] = {
    "record": "the indexer's publication types",
    "jats": "the file's own article-type",
    "subject": "the publisher's subject line",
    "title": "the authors' own title",
    "printed": "a notice printed above the title",
    "shape": "the reader's own reading of the paper",
}


def _evidence_rows(tree: Tree, kind: dict[str, Any], xml: bytes | None, pub_types: Any) -> tuple[str, str]:
    """Every source's word on the paper's type, and the shape's own reading beneath it.

    `decide` returns only the verdict and a one-line detail; a person checking it needs the table it
    read from — so the same `evidence_of` is asked again here, and the winner is picked the same way
    `decide` picks it (the most trusted specific label, else the shape). The evidence is read from the
    tree that is already in hand, so nothing is parsed twice and nothing is asked of a model."""
    try:
        evidence = evidence_of(tree, jats_xml=xml, pub_types=pub_types, oracle=lanes.active())
    except Exception:  # the type table is a report, never a reason to lose the paper
        evidence = []
    specific = [e for e in evidence if e.specific]
    winner = specific[0] if specific else None
    source = kind.get("source")
    rows = []
    for e in evidence:
        won = winner is not None and e is winner and source == e.source
        rows.append(
            f'<tr class="{"won" if won else ""}">'
            f'<td><b>{_e(e.source)}</b><div class="muted">{_e(_TRUST_WORDS.get(e.source, ""))}</div></td>'
            f'<td>{_e(e.label)}</td>'
            f'<td>{_e(e.type)}{f"<div class=muted>{_e(e.subtype)}</div>" if e.subtype else ""}</td>'
            f'<td>{"specific" if e.specific else "<span class=muted>a default bucket</span>"}</td>'
            f'<td>{"← this one" if won else ""}</td></tr>'
        )
    if not rows:
        rows.append('<tr><td colspan="5" class="muted">no source named a type: nothing in the record, the file, the title or the page</td></tr>')
    try:
        features, verdict = shape_of(tree)
        sentence = shape_sentence(features)
    except Exception:
        features, verdict, sentence = {}, None, ""
    shape_won = source in ("shape", "default", "none")
    rows.append(
        f'<tr class="{"won" if shape_won else ""}"><td><b>shape</b><div class="muted">{_e(_TRUST_WORDS["shape"])}</div></td>'
        f'<td class="muted">{_e(sentence)}</td>'
        f'<td>{_e(verdict or "—")}</td>'
        f'<td class="muted">{"may decide" if verdict else "reads as nothing"}</td>'
        f'<td>{"← this one" if shape_won else ""}</td></tr>'
    )
    table = ("<table class='grid'><thead><tr><th>source</th><th>the label it read</th><th>the type that implies</th>"
             "<th>specific?</th><th></th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")
    flags = [k for k in ("methods", "results", "discussion", "abstract", "case_heading", "letter_opening", "review_methods", "data_headings", "methods_last") if features.get(k)]
    facts = [
        ("lanes present", ", ".join(k for k in ("methods", "results", "discussion", "abstract") if features.get(k)) or "none"),
        ("topical sections", f'{features.get("topical", 0)} of {features.get("sections", 0)}'),
        ("paragraphs", str(features.get("paragraphs", 0))),
        ("words of prose", str(features.get("words", 0))),
        ("body paragraphs reporting statistics", f'{round(100 * float(features.get("stats") or 0))} %'),
        ("the methods after the discussion", "yes" if features.get("methods_last") else "no"),
        ("a case report's own heading", "yes" if features.get("case_heading") else "no"),
        ("opens to the editor", "yes" if features.get("letter_opening") else "no"),
        ("a systematic review's methods", "yes" if features.get("review_methods") else "no"),
        ("a data descriptor's headings", "yes" if features.get("data_headings") else "no"),
        ("future-tense verbs in the body", str(features.get("future", 0))),
        ("the abstract's own labels", ", ".join(features.get("abstract_labels") or []) or "none"),
    ] if features else []
    facts_html = "".join(f"<tr><td>{_e(name)}</td><td>{_e(value)}</td></tr>" for name, value in facts)
    shape_block = (
        f'<details class="more"><summary>What the shape measured ({len(flags)} features present)</summary>'
        f"<table class='grid'><tbody>{facts_html}</tbody></table></details>"
    ) if facts_html else ""
    return table, shape_block


def _type_block(kind: dict[str, Any], tree: Tree, xml: bytes | None, pub_types: Any) -> str:
    notes = [n for n in tree.notes if n.get("kind") == "type-disagreement"]
    sub = f'<span class="sub"> · {_e(kind.get("subtype"))}</span>' if kind.get("subtype") else ""
    detail = _e(kind.get("detail") or "")
    note_html = "".join(f'<div class="note">{_e(n.get("message"))}</div>' for n in notes)
    table, shape_block = _evidence_rows(tree, kind, xml, pub_types)
    return (
        f'<div class="card wide" id="type"><h2>What kind of paper, and why</h2>'
        f'<div class="big">{_e(kind.get("type", "other"))}{sub}</div>'
        f'<div class="muted">decided by <b>{_e(kind.get("source", "none"))}</b>{" · " + detail if detail else ""}</div>'
        f"{note_html}"
        f'<div class="muted small">The sources are listed most trusted first; the most trusted <i>specific</i> label wins, '
        f"and a default bucket decides nothing on its own.</div>"
        f"{table}{shape_block}</div>"
    )


def _confidence_block(conf: dict[str, Any]) -> str:
    score = conf.get("confidence")
    reasons = conf.get("reasons") or []
    bar = int(round(100 * float(score or 0)))
    colour = "#16a34a" if bar >= 90 else "#d97706" if bar >= 50 else "#dc2626"
    items = "".join(f"<li>{_e(r)}</li>" for r in reasons) or "<li class='muted'>nothing the checks object to</li>"
    return (
        f'<div class="card"><h2>How far this reading can be trusted</h2>'
        f'<div class="big" style="color:{colour}">{bar}%</div>'
        f'<div class="bar"><span style="width:{bar}%;background:{colour}"></span></div>'
        f"<ul class='reasons'>{items}</ul></div>"
    )


# --------------------------------------------------------------------------------------- citations
def _quote_around(text: str, marker: str, width: int = 90) -> str:
    """The sentence around a marker, with the marker itself marked: a citation is only checkable
    against what it was printed in."""
    flat = " ".join((text or "").split())
    at = flat.find(marker) if marker else -1
    if at < 0:
        return _e(flat[: 2 * width] + ("…" if len(flat) > 2 * width else ""))
    start, end = max(0, at - width), min(len(flat), at + len(marker) + width)
    return (("…" if start else "") + _e(flat[start:at]) + f"<mark>{_e(marker)}</mark>"
            + _e(flat[at + len(marker) : end]) + ("…" if end < len(flat) else ""))


def unlinked_markers(tree: Tree, refs: list[Ref]) -> list[tuple[Node, str, list[int]]]:
    """Bracketed numbers a node prints that name no entry — "[47]" in a paper with 31 references.
    Only the bracketed style can be checked this way: a superscript or an author–year the linker
    declined to read leaves nothing behind that says it was ever a marker, so it is not guessed at."""
    pattern = getattr(citations_module, "_NUMERIC", None)
    expand = getattr(citations_module, "_expand_numeric", None)
    types = getattr(citations_module, "_CITING_TYPES", {"paragraph", "list_item", "caption", "footnote"})
    if pattern is None or expand is None or not refs:
        return []
    known = {r.ref_no for r in refs}
    ref_nodes = {r.node_id for r in refs}
    out: list[tuple[Node, str, list[int]]] = []
    for node in tree.walk():
        if node.type not in types or node.role == "references" or node.node_id in ref_nodes or not node.text:
            continue
        for m in pattern.finditer(node.text):
            missing = [n for n in expand(m.group(1)) if n not in known]
            if missing:
                out.append((node, m.group(0), missing))
    return out


_CITE_ROWS = 400


def _citations_block(tree: Tree, refs: list[Ref], cites: list[Citation], counts: dict[str, int],
                     loose: list[tuple[Node, str, list[int]]]) -> str:
    """Every link between a sentence and an entry, both ways round, and what would not link."""
    by_id = {n.node_id: n for n in tree.walk()}
    _, owner = _sections_index(tree)
    by_ref = {r.ref_no: r for r in refs}
    times = Counter(c.ref_no for c in cites)
    rows = []
    for c in cites[:_CITE_ROWS]:
        node = by_id.get(c.node_id)
        section = by_id.get(owner.get(c.node_id, "")) if node else None
        ref = by_ref.get(c.ref_no)
        entry = (ref.text if ref else "")[:200]
        rows.append(
            f'<tr><td class="marker">{_e(c.marker)}</td>'
            f'<td>{_e(section.heading if section and section.heading else "—")}'
            f'<div class="muted tiny">{_e(c.node_id)}</div></td>'
            f'<td class="quote">{_quote_around(node.text if node else "", c.marker)}</td>'
            f'<td><b>{c.ref_no}</b> {_e(entry)}{"…" if ref and len(ref.text) > 200 else ""}'
            + (f'<div class="muted tiny">{_e(ref.doi)}</div>' if ref and ref.doi else "")
            + "</td></tr>"
        )
    more = (f'<tr><td colspan="4" class="muted">and {len(cites) - _CITE_ROWS} more, not listed</td></tr>'
            if len(cites) > _CITE_ROWS else "")
    linked_table = (
        '<table class="grid cites"><thead><tr><th>the marker, as printed</th><th>the section it sits in</th>'
        '<th>around it</th><th>the entry it points at</th></tr></thead><tbody>'
        + ("".join(rows) + more or '<tr><td colspan="4" class="muted">no in-text citation was linked</td></tr>')
        + "</tbody></table>"
    )
    ref_rows = "".join(
        f'<tr><td class="num">{r.ref_no}</td><td>{_e(r.text[:300])}{"…" if len(r.text) > 300 else ""}'
        + (f'<div class="muted tiny">{_e(r.doi or "")}{" · PMID " + _e(r.pmid) if r.pmid else ""}</div>' if (r.doi or r.pmid) else "")
        + f'</td><td class="num">{times.get(r.ref_no, 0) or "<span class=muted>0</span>"}</td></tr>'
        for r in refs
    ) or '<tr><td colspan="3" class="muted">no reference list was found</td></tr>'
    ref_table = ('<table class="grid"><thead><tr><th class="num">no.</th><th>entry</th><th class="num">cited</th></tr></thead><tbody>'
                 + ref_rows + "</tbody></table>")
    loose_rows = "".join(
        f'<tr><td class="marker">{_e(marker)}</td><td class="num">{_e(", ".join(str(n) for n in missing))}</td>'
        f'<td class="quote">{_quote_around(node.text, marker)}</td></tr>'
        for node, marker, missing in loose[:200]
    )
    loose_table = (
        '<table class="grid"><thead><tr><th>the marker</th><th>names no entry</th><th>around it</th></tr></thead><tbody>'
        + loose_rows + "</tbody></table>"
    ) if loose_rows else '<div class="muted">every bracketed number in the text names an entry</div>'
    uncited = sum(1 for r in refs if not times.get(r.ref_no))
    return (
        f'<details class="card wide" id="citations"><summary><h2>Every citation, and what it points at</h2>'
        f'<span class="muted">{counts.get("citations", 0)} markers linked from {counts.get("citing_nodes", 0)} nodes to '
        f'{counts.get("cited_refs", 0)} of {counts.get("refs", 0)} entries · {uncited} entries never cited · '
        f'{len(loose)} bracketed markers name no entry</span></summary>'
        f'<h3>The links, one by one</h3>{linked_table}'
        f'<h3>The reference list, with how often each entry is cited</h3>{ref_table}'
        f'<h3>What did not link</h3>{loose_table}'
        f"</details>"
    )


# ------------------------------------------------------------------------------------- the XML path
def prepared_jats_records(raw: bytes) -> dict[str, Any]:
    """What `jats_prep` changed in the publisher's file before Docling read it.

    The tree in hand was built from the saved Docling document, so the preparation's own work is not
    in `tree.changes`: the file is prepared again here, for the record, and the pass is asked to log
    what it does. `prepare_jats(raw, log=...)` is the signature that keeps that log; where it is not
    there yet the call falls back to the plain one and the report says only whether the bytes moved."""
    try:
        from .jats_prep import prepare_jats
    except Exception:
        return {"available": False, "records": [], "changed": False, "logged": False}
    records: list[Any] = []
    logged = True
    try:
        try:
            prepared = prepare_jats(raw, log=records)
        except TypeError:
            logged, records = False, []
            prepared = prepare_jats(raw)
    except Exception as exc:
        return {"available": False, "records": [], "changed": False, "logged": False, "error": f"{type(exc).__name__}: {exc}"}
    out: list[dict[str, Any]] = []
    for r in records:
        if not isinstance(r, dict):
            continue
        row = dict(r)
        row["kind"] = str(row.get("kind") or "other")
        row.setdefault("stage", stage_of(row["kind"]))
        out.append(row)
    return {"available": True, "records": out, "changed": prepared != raw, "logged": logged,
            "before": len(raw), "after": len(prepared or b"")}


def _jats_block(prep: dict[str, Any]) -> str:
    if not prep.get("available"):
        why = prep.get("error") or "the preparation could not be run again here"
        return f'<div class="card wide"><h2>What the XML preparation changed</h2><div class="muted">{_e(why)}</div></div>'
    records = prep.get("records") or []
    counts = Counter(r.get("kind") for r in records)
    head = f"{len(records)} changes in {len(counts)} kinds: " + " · ".join(f"{n} {_e(str(k))}" for k, n in counts.most_common())
    if not records:
        head = ("the file was rewritten before Docling read it, but the pass does not say what it changed yet"
                if prep.get("changed") else "the file was handed to Docling as it came")
        if prep.get("changed"):
            head += f' ({prep.get("before", 0)} bytes in, {prep.get("after", 0)} out)'
    listed = _change_list(records, prefix="x", group_all=True, cap=_EXAMPLES)
    body = f'<ol class="changes flat">{listed}</ol>' if listed else ""
    return (
        f'<div class="card wide" id="jats"><h2>What the XML preparation changed</h2>'
        f'<div class="muted">{head}</div>'
        f'<div class="muted small">Formulas rendered as text, numeric cross-references bracketed, titles flattened, '
        f"an untitled body wrapped: the publisher's file as Docling was given it, not as it was downloaded.</div>"
        f"{body}</div>"
    )


def _counts_block(tree: Tree, cites: dict[str, Any], findings: list[Any]) -> str:
    stages = Counter(c.get("stage") or stage_of(c["kind"]) for c in tree.changes)
    kinds = Counter(c["kind"] for c in tree.changes)
    stage_html = "".join(
        f'<tr><td><span class="dot" style="background:{STAGE_COLOURS.get(s, "#525252")}"></span>{_e(STAGE_WORDS.get(s, s))}</td>'
        f"<td class='num'>{n}</td><td class='muted'>{_e(', '.join(f'{k} {v}' for k, v in kinds.items() if stage_of(k) == s))}</td></tr>"
        for s, n in stages.most_common()
    ) or "<tr><td colspan=3 class='muted'>the reader changed nothing</td></tr>"
    severities = Counter(f.severity for f in findings)
    # every counter the reader raised, against what the log could place: the difference is shown, not hidden
    counted = Counter(tree.repairs) + Counter(tree.dropped)
    unplaced = {k: counted[k] - kinds.get(k, 0) for k in counted if counted[k] - kinds.get(k, 0) > 0}
    unplaced_html = (
        '<div class="note">counted by the reader but not placed on a page: '
        + _e(", ".join(f"{k} {n}" for k, n in sorted(unplaced.items(), key=lambda kv: -kv[1])))
        + " — those passes do not record where they acted yet</div>"
    ) if unplaced else ""
    return (
        f'<div class="card wide"><h2>What the reader did</h2>'
        f"<table class='grid'><tbody>{stage_html}</tbody></table>"
        f'<div class="muted small">references {cites.get("refs", 0)} · in-text citations {cites.get("citations", 0)} '
        f'linked to {cites.get("cited_refs", 0)} of them · audit: '
        + (" · ".join(f"{n} {s}" for s, n in severities.most_common()) or "nothing")
        + "</div>" + unplaced_html + "</div>"
    )


def _nav_strip(images: dict[int, dict[str, Any]], changed_pages: set[int], tree: Tree,
               per_section: dict[str | None, list[dict[str, Any]]]) -> str:
    """The strip under the title that jumps to any page, the pages the reader touched marked.

    A reviewer works page by page and a long paper is thirty of them, so the strip rides with the
    header rather than sitting at the top of the document. A paper read from XML has no pages: its
    sections are the thing to jump to, and they are listed instead."""
    links = []
    if images:
        for page_no in sorted(images):
            cls = "changed" if page_no in changed_pages else ""
            links.append(f'<a class="{cls}" href="#page-{page_no}" title="page {page_no}">{page_no}</a>')
        what = "pages"
    else:
        for node in tree.walk():
            if node.type != "section" or not node.heading:
                continue
            cls = "changed" if per_section.get(node.node_id) else ""
            links.append(f'<a class="{cls} wide" href="#sec-{_e(safe_key(node.node_id))}">{_e(node.heading[:28])}</a>')
        what = "sections"
    if not links:
        return ""
    return (f'<nav class="pagenav" aria-label="{what}"><a class="home" href="#top">top</a>'
            + "".join(links) + f'<a class="home" href="#citations">citations</a></nav>')


PAGE_CSS = """
:root { --ink:#1f2430; --muted:#6b7280; --rule:#e5e7eb; --bg:#f8fafc; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }
a { color:#1d4ed8; }
header.top { background:#fff; border-bottom:1px solid var(--rule); padding:14px 20px; position:sticky; top:0; z-index:10; }
header.top h1 { margin:0 0 2px; font-size:17px; }
.meta { color:var(--muted); font-size:12.5px; }
main { max-width:1500px; margin:0 auto; padding:18px 20px 60px; }
.cards { display:grid; gap:14px; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); margin-bottom:22px; }
.card { background:#fff; border:1px solid var(--rule); border-radius:12px; padding:14px 16px; }
.card.wide { grid-column:1/-1; }
.card h2 { margin:0 0 8px; font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }
.big { font-size:26px; font-weight:700; }
.big .sub { font-size:14px; font-weight:500; color:var(--muted); }
.bar { height:6px; background:#eef2f7; border-radius:4px; overflow:hidden; margin:8px 0; }
.bar span { display:block; height:100%; }
.reasons { margin:6px 0 0; padding-left:18px; font-size:12.5px; color:var(--muted); }
.note { margin-top:6px; font-size:12.5px; color:#b45309; background:#fffbeb; border:1px solid #fde68a; border-radius:8px; padding:6px 8px; }
table.grid { width:100%; border-collapse:collapse; font-size:12.5px; }
table.grid th { text-align:left; font-weight:600; color:var(--muted); border-bottom:1px solid var(--rule); padding:5px 6px; }
table.grid td { padding:5px 6px; border-bottom:1px solid #f1f5f9; vertical-align:top; }
td.num { text-align:right; font-variant-numeric:tabular-nums; width:60px; }
.lane { border-radius:5px; padding:1px 6px; font-size:11.5px; }
.muted { color:var(--muted); }
.small { font-size:12px; margin-top:8px; }
.page { background:#fff; border:1px solid var(--rule); border-radius:12px; margin:0 0 18px; overflow:hidden; }
.pagehead { display:flex; align-items:baseline; gap:14px; padding:10px 14px; border-bottom:1px solid var(--rule); background:#fcfdff; }
.pagehead h3 { margin:0; font-size:14px; }
.summary { font-size:12.5px; }
.split { display:grid; grid-template-columns:minmax(0,1.35fr) minmax(0,1fr); gap:16px; padding:14px; }
@media (max-width:1100px) { .split { grid-template-columns:1fr; } }
.sheet { position:relative; align-self:start; border:1px solid var(--rule); border-radius:8px; overflow:hidden; }
.sheet img { display:block; width:100%; }
.kept { position:absolute; border:1.5px solid; border-radius:2px; pointer-events:none; opacity:.85; }
.chg { position:absolute; border:2px solid; border-radius:3px; text-decoration:none; }
.chg span { position:absolute; left:-9px; top:-9px; min-width:18px; height:18px; border-radius:9px; color:#fff;
            font-size:11px; line-height:18px; text-align:center; font-weight:700; }
ol.changes { list-style:none; margin:0; padding:0; max-height:760px; overflow:auto; }
li.change { border:1px solid var(--rule); border-radius:9px; padding:8px 10px; margin-bottom:8px; background:#fff; }
li.change:target { outline:2px solid #2563eb; }
li.change .head { display:flex; gap:7px; align-items:baseline; flex-wrap:wrap; font-size:12.5px; }
.kind { font-weight:700; }
.dot { width:9px; height:9px; border-radius:50%; display:inline-block; }
.before, .after { font-size:12.5px; margin-top:5px; padding:5px 7px; border-radius:6px; white-space:pre-wrap; }
.before { background:#fef2f2; border-left:3px solid #dc2626; }
.after { background:#f0fdf4; border-left:3px solid #16a34a; }
.tag { font-size:10.5px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); margin-right:6px; }
.legend { display:flex; flex-wrap:wrap; gap:10px; font-size:12px; color:var(--muted); margin:2px 0 16px; }
.legend b { color:var(--ink); font-weight:600; }
.pagenav { display:flex; flex-wrap:wrap; gap:4px; margin-top:8px; max-height:74px; overflow:auto; }
.pagenav a { min-width:25px; text-align:center; padding:1px 6px; border:1px solid var(--rule); border-radius:6px;
             font-size:11.5px; color:var(--muted); text-decoration:none; background:#fff; }
.pagenav a:hover { border-color:#94a3b8; color:var(--ink); }
.pagenav a.changed { border-color:#d97706; color:#b45309; background:#fffbeb; font-weight:600; }
.pagenav a.wide { min-width:0; max-width:190px; text-align:left; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pagenav a.home { background:#eef2f7; }
.page, .card, details.card, details.sec { scroll-margin-top:150px; }
details.card > summary, details.sec > summary { cursor:pointer; list-style-position:outside; }
details.card > summary h2 { display:inline-block; margin:0 10px 0 0; }
details.card > summary > span { font-size:12.5px; }
details.card h3 { font-size:12.5px; margin:16px 0 4px; color:var(--muted); font-weight:600; }
details.more { margin-top:10px; font-size:12.5px; }
details.more > summary { cursor:pointer; color:var(--muted); }
details.sec { background:#fff; border:1px solid var(--rule); border-radius:10px; padding:8px 12px; margin-bottom:8px; }
details.sec > summary { font-size:12.5px; }
details.sec > summary .summary { margin-left:8px; }
ol.changes.flat { max-height:none; margin-top:8px; }
li.group { border:1px solid var(--rule); border-radius:9px; padding:8px 10px; margin-bottom:8px; background:#fcfdff; }
li.group > .head { display:flex; gap:7px; align-items:baseline; flex-wrap:wrap; font-size:12.5px; margin-bottom:4px; }
li.group details > summary { cursor:pointer; font-size:12px; }
tr.won td { background:#f0fdf4; }
tr.won td:last-child { color:#16a34a; font-weight:600; white-space:nowrap; }
td.marker { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; white-space:nowrap; }
td.quote { max-width:520px; }
td.quote mark { background:#fef08a; padding:0 1px; }
.tiny { font-size:11px; word-break:break-all; }
table.cites td { vertical-align:top; }
"""


def _card(name: str, build: Any, *args: Any) -> str:
    """One card, or a card saying why there is none.

    The page-by-page view is the report; the tables beside it are additions. A paper whose reference
    list defeats the citation table still deserves its pages drawn, so a block that raises is written
    down as a block that raised rather than taking the paper with it."""
    try:
        return build(*args)
    except Exception as exc:
        return (f'<div class="card wide"><h2>{_e(name)}</h2>'
                f'<div class="note">this block could not be built: {_e(f"{type(exc).__name__}: {exc}")}</div></div>')


def render_paper(lib: Path, row: dict[str, Any], out_root: Path, scale: float) -> dict[str, Any]:
    """One paper's report. Returns the row the index is built from."""
    tree, kind, xml = read_paper(lib, row)
    refs, cites = link_citations(tree, xml)
    cite_counts = summarize_citations(refs, cites)
    try:
        loose_markers = unlinked_markers(tree, refs)
    except Exception:  # a report of what did not link is never a reason to lose the paper
        loose_markers = []
    findings = audit_tree(tree, kind.get("type"))
    conf = assess_confidence(tree, kind)
    key = row["key"]
    folder = out_root / f"{lib.name}--{safe_key(key)}"
    folder.mkdir(parents=True, exist_ok=True)
    images = page_images(row.get("source"), folder / "pages", scale)

    kept = boxes_of(tree)
    changes = list(tree.changes)
    numbers = {id(c): i + 1 for i, c in enumerate(changes)}
    _, owner = _sections_index(tree)
    per_section = _changes_by_section(changes, owner)
    by_page_kept: dict[int, list[dict[str, Any]]] = {}
    for box in kept:
        by_page_kept.setdefault(box["page"], []).append(box)
    by_page_changes: dict[int | None, list[dict[str, Any]]] = {}
    for change in changes:
        by_page_changes.setdefault(change.get("page"), []).append(change)

    blocks = []
    for page_no in sorted(images) if images else sorted(p for p in by_page_kept if p):
        page = images.get(page_no)
        if page is None:
            continue
        blocks.append(_page_block(page_no, page, by_page_kept.get(page_no, []), by_page_changes.get(page_no, []), numbers))
    loose = by_page_changes.get(None, [])
    if images and loose:
        listed = _change_list(loose, numbers, cap=_EXAMPLES)
        blocks.append(
            '<section class="page"><div class="pagehead"><h3>Changes with no page</h3></div>'
            f'<div class="split" style="grid-template-columns:1fr"><ol class="changes flat">{listed}</ol></div></section>'
        )
    if not images:
        # a paper read from XML has nothing to draw: the sections are what the changes are read against
        blocks.append(
            '<section class="page"><div class="pagehead"><h3>Every change, under the section it happened in</h3>'
            f'<div class="summary muted">{len(changes)} in all — this paper has no pages: it was read from XML</div></div>'
            f'<div style="padding:14px">{_section_changes_block(tree, per_section, numbers)}</div></section>'
        )

    legend = " ".join(
        f'<span><span class="dot" style="background:{c}"></span> <b>{_e(STAGE_WORDS.get(s, s))}</b></span>'
        for s, c in STAGE_COLOURS.items() if s not in ("other", "xml")
    )
    lane_legend = " ".join(f'<span><span class="dot" style="background:{c}"></span> {_e(l)}</span>' for l, c in LANE_COLOURS.items())
    changed_pages = {p for p in by_page_changes if p}
    prep_card = _card("What the XML preparation changed", lambda: _jats_block(prepared_jats_records(xml))) if xml else ""
    html_out = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(tree.title or key)} — what the reader did</title><style>{PAGE_CSS}</style></head><body>
<header class="top" id="top"><h1>{_e(tree.title or key)}</h1>
<div class="meta">{_e(lib.name)} · {_e(row.get('format') or '?')} · {_e(publisher_of(key))} · {_e(key)} · {len(tree.pages)} pages ·
<a href="../index.html">every paper</a></div>
{_nav_strip(images, changed_pages, tree, per_section)}</header>
<main>
<div class="cards">
  {_card('What kind of paper, and why', _type_block, kind, tree, xml, row.get('pub_types'))}
  {_confidence_block(conf)}
  {_counts_block(tree, cite_counts, findings)}
  <div class="card wide" id="sections"><h2>The sections it found</h2>{_card('The sections it found', _headings_table, tree, per_section)}</div>
  {prep_card}
  {_card('Every citation, and what it points at', _citations_block, tree, refs, cites, cite_counts, loose_markers)}
</div>
<div class="legend"><b>changes:</b> {legend}</div>
<div class="legend"><b>lanes:</b> {lane_legend}</div>
{"".join(blocks)}
</main></body></html>"""
    (folder / "index.html").write_text(html_out, encoding="utf-8")

    stages = Counter(c.get("stage") or stage_of(c["kind"]) for c in changes)
    return {
        "library": lib.name,
        "key": key,
        "title": tree.title,
        "format": row.get("format"),
        "publisher": publisher_of(key),
        "pages": len(tree.pages),
        "type": kind.get("type"),
        "subtype": kind.get("subtype"),
        "type_source": kind.get("source"),
        "confidence": conf.get("confidence"),
        "band": trust_band(conf.get("confidence")),
        "changes": len(changes),
        "by_stage": dict(stages),
        "refs": cite_counts.get("refs", 0),
        "citations": cite_counts.get("citations", 0),
        "unlinked": len(loose_markers),
        "errors": sum(1 for f in findings if f.severity == "error"),
        "warnings": sum(1 for f in findings if f.severity == "warn"),
        "failed": False,
        "error": None,
        "report": f"{folder.name}/index.html",
    }


def failed_row(lib: Path, row: dict[str, Any], exc: BaseException) -> dict[str, Any]:
    """A paper the reader could not read at all. It belongs in the index more than a good one does:
    a review that quietly lists the papers that worked says nothing about the ones that did not."""
    return {
        "library": lib.name,
        "key": row["key"],
        "title": None,
        "format": row.get("format"),
        "publisher": publisher_of(row["key"]),
        "pages": 0,
        "type": None,
        "subtype": None,
        "type_source": None,
        "confidence": None,
        "band": "failed",
        "changes": 0,
        "by_stage": {},
        "refs": 0,
        "citations": 0,
        "unlinked": 0,
        "errors": 0,
        "warnings": 0,
        "failed": True,
        "error": f"{type(exc).__name__}: {exc}",
        "report": None,
    }


INDEX_CSS = PAGE_CSS + """
table.papers { width:100%; border-collapse:collapse; background:#fff; font-size:13px; }
table.papers th { position:sticky; top:0; background:#fff; z-index:2; cursor:pointer; user-select:none; }
table.papers th:hover { color:var(--ink); }
table.papers th .arrow { font-size:9px; color:#94a3b8; }
table.papers td.title { max-width:420px; }
table.papers tr.failed td { background:#fef2f2; }
.pill { border-radius:5px; padding:1px 7px; font-size:11.5px; background:#eef2f7; }
.controls { display:flex; flex-wrap:wrap; gap:8px 14px; align-items:end; background:#fff; border:1px solid var(--rule);
            border-radius:12px; padding:12px 14px; margin-bottom:14px; }
.controls label { display:flex; flex-direction:column; gap:3px; font-size:11px; text-transform:uppercase;
                  letter-spacing:.05em; color:var(--muted); }
.controls select, .controls input { font:13px/1.4 inherit; padding:4px 6px; border:1px solid var(--rule);
                                    border-radius:7px; background:#fff; color:var(--ink); min-width:130px; }
.controls input[type=number] { min-width:80px; }
.controls button { font:13px/1.4 inherit; padding:5px 10px; border:1px solid var(--rule); border-radius:7px;
                   background:#f8fafc; color:var(--ink); cursor:pointer; }
.controls .count { margin-left:auto; font-size:12.5px; color:var(--muted); }
.side { display:grid; grid-template-columns:minmax(0,2.3fr) minmax(260px,1fr); gap:14px; align-items:start; }
@media (max-width:1100px) { .side { grid-template-columns:1fr; } }
.fail { color:#b91c1c; font-size:12px; }
.scroll { max-height:640px; overflow:auto; }
table.grid thead th { position:sticky; top:0; background:#fff; }
"""

#: the index's filtering and sorting, kept as one plain script so the report is a single file that
#: opens from a disk with nothing fetched
INDEX_JS = """
(function () {
  var table = document.getElementById('papers');
  var body = table.querySelector('tbody');
  var heads = Array.prototype.slice.call(table.querySelectorAll('thead th'));
  var rows = Array.prototype.slice.call(body.querySelectorAll('tr'));
  var count = document.getElementById('count');
  var controls = ['library', 'format', 'type', 'band', 'publisher', 'state'];
  function value(id) { var el = document.getElementById('f-' + id); return el ? el.value : ''; }
  function apply() {
    var q = value('q').toLowerCase();
    var min = parseInt(value('min'), 10);
    if (isNaN(min)) min = 0;
    var shown = 0;
    rows.forEach(function (row) {
      var ok = true;
      controls.forEach(function (id) {
        var want = value(id);
        if (want && row.getAttribute('data-' + id) !== want) ok = false;
      });
      if (ok && q) ok = (row.getAttribute('data-search') || '').indexOf(q) >= 0;
      if (ok && min > 0) ok = parseInt(row.getAttribute('data-changes'), 10) >= min;
      row.hidden = !ok;
      row.style.display = ok ? '' : 'none';
      if (ok) shown++;
    });
    count.textContent = shown + ' of ' + rows.length + ' papers shown';
  }
  var direction = {};
  function sort(key, numeric) {
    direction[key] = direction[key] === 'asc' ? 'desc' : 'asc';
    var sign = direction[key] === 'asc' ? 1 : -1;
    rows.sort(function (a, b) {
      var x = a.getAttribute('data-' + key) || '', y = b.getAttribute('data-' + key) || '';
      if (numeric) {
        x = parseFloat(x); y = parseFloat(y);
        if (isNaN(x)) x = -1;
        if (isNaN(y)) y = -1;
        return sign * (x - y);
      }
      return sign * String(x).localeCompare(String(y));
    });
    rows.forEach(function (row) { body.appendChild(row); });
    heads.forEach(function (th) {
      var mark = th.querySelector('.arrow');
      if (mark) mark.textContent = th.getAttribute('data-key') === key ? (direction[key] === 'asc' ? '\u25b2' : '\u25bc') : '';
    });
  }
  heads.forEach(function (th) {
    var key = th.getAttribute('data-key');
    if (!key) return;
    th.addEventListener('click', function () { sort(key, th.getAttribute('data-numeric') === '1'); });
  });
  controls.concat(['q', 'min']).forEach(function (id) {
    var el = document.getElementById('f-' + id);
    if (el) { el.addEventListener('input', apply); el.addEventListener('change', apply); }
  });
  document.getElementById('f-clear').addEventListener('click', function () {
    controls.concat(['q', 'min']).forEach(function (id) {
      var el = document.getElementById('f-' + id);
      if (el) el.value = '';
    });
    apply();
  });
  apply();
})();
"""


def _options(name: str, values: Iterable[str], label: str) -> str:
    choices = "".join(f'<option value="{_e(v)}">{_e(v)}</option>' for v in sorted({v for v in values if v}))
    return f'<label>{_e(label)}<select id="f-{name}"><option value="">any</option>{choices}</select></label>'


def _publisher_totals(rows: list[dict[str, Any]]) -> str:
    """One line per publisher: a reader that stumbles does it on one publisher's files first, and
    this is where that shows before anyone opens a paper."""
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r.get("publisher") or publisher_of(r.get("key")), []).append(r)
    lines = []
    for name, group in sorted(by.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        ok = [r for r in group if not r.get("failed")]
        scored = [r["confidence"] for r in ok if r.get("confidence") is not None]
        trust = f"{round(100 * sum(scored) / len(scored))}%" if scored else "—"
        failed = sum(1 for r in group if r.get("failed"))
        lines.append(
            f'<tr><td>{_e(name)}</td><td class="num">{len(group)}</td>'
            f'<td class="num">{sum(r.get("pages") or 0 for r in ok)}</td>'
            f'<td class="num">{sum(r.get("changes") or 0 for r in ok)}</td>'
            f'<td class="num">{trust}</td>'
            f'<td class="num">{f"<span class=fail>{failed}</span>" if failed else ""}</td></tr>'
        )
    return ('<div class="card"><h2>By publisher</h2><div class="scroll"><table class="grid"><thead><tr><th>publisher</th>'
            '<th class="num">papers</th><th class="num">pages</th><th class="num">changes</th>'
            '<th class="num">trust</th><th class="num">failed</th></tr></thead><tbody>'
            + "".join(lines) + "</tbody></table></div>"
            '<div class="muted small">The publisher is the DOI prefix; an unlisted prefix stands for itself.</div></div>')


def _index_row(r: dict[str, Any]) -> str:
    conf = r.get("confidence")
    band = r.get("band") or trust_band(conf)
    publisher = r.get("publisher") or publisher_of(r.get("key"))
    search = " ".join(str(x) for x in (r.get("title") or "", r.get("key") or "", r.get("type") or "", publisher)).lower()
    data = (
        f'data-library="{_e(r["library"])}" data-format="{_e(r.get("format") or "")}" '
        f'data-type="{_e(r.get("type") or "")}" data-band="{_e("failed" if r.get("failed") else band)}" '
        f'data-publisher="{_e(publisher)}" data-state="{"failed" if r.get("failed") else "read"}" '
        f'data-changes="{r.get("changes") or 0}" data-pages="{r.get("pages") or 0}" '
        f'data-trust="{"" if conf is None else round(100 * conf)}" data-cites="{r.get("citations") or 0}" '
        f'data-errors="{r.get("errors") or 0}" data-title="{_e(r.get("title") or r["key"])}" '
        f'data-search="{_e(search)}"'
    )
    if r.get("failed"):
        return (
            f'<tr class="failed" {data}><td class="title"><b>{_e(r.get("title") or r["key"])}</b>'
            f'<div class="muted">{_e(publisher)}</div>'
            f'<div class="fail">{_e(r.get("error") or "failed")}</div></td>'
            f'<td>{_e(r["library"])}</td><td>{_e(r.get("format") or "")}</td><td class="num">—</td>'
            f'<td><span class="fail">failed</span></td><td class="num">—</td><td class="num">—</td>'
            f'<td class="muted">the paper could not be read; nothing below it was measured</td>'
            f'<td class="num">—</td><td class="num">—</td></tr>'
        )
    colour = "#16a34a" if (conf or 0) >= 0.9 else "#d97706" if (conf or 0) >= 0.5 else "#dc2626"
    stages = " ".join(
        f'<span class="pill" style="background:{STAGE_COLOURS.get(s, "#525252")}1a;color:{STAGE_COLOURS.get(s, "#525252")}">{_e(s)} {n}</span>'
        for s, n in sorted((r.get("by_stage") or {}).items(), key=lambda kv: -kv[1])
    )
    subtype = f'<div class="muted">{_e(r["subtype"])}</div>' if r.get("subtype") else ""
    return (
        f'<tr {data}><td class="title"><a href="{_e(r["report"])}">{_e(r["title"] or r["key"])}</a>'
        f'<div class="muted">{_e(r["key"])} · {_e(publisher)}</div></td>'
        f'<td>{_e(r["library"])}</td><td>{_e(r["format"])}</td><td class="num">{r["pages"]}</td>'
        f'<td>{_e(r["type"])}{subtype}<div class="muted">{_e(r["type_source"])}</div></td>'
        f'<td class="num" style="color:{colour}">{"" if conf is None else f"{round(100 * conf)}%"}</td>'
        f'<td class="num">{r["changes"]}</td><td>{stages}</td>'
        f'<td class="num">{r["citations"]}/{r["refs"]}</td>'
        f'<td class="num">{r["errors"]}/{r["warnings"]}</td></tr>'
    )


def render_index(rows: list[dict[str, Any]], out_root: Path) -> Path:
    rows = sorted(rows, key=lambda r: (r["library"], bool(r.get("failed")), -(r.get("changes") or 0)))
    body = "".join(_index_row(r) for r in rows)
    read = [r for r in rows if not r.get("failed")]
    failed = [r for r in rows if r.get("failed")]
    totals: Counter = Counter()
    for r in read:
        totals.update(r.get("by_stage") or {})
    summary = " · ".join(f"{n} {s}" for s, n in totals.most_common())
    controls = (
        '<div class="controls">'
        + '<label>search<input id="f-q" type="search" placeholder="title, key, publisher"></label>'
        + _options("library", (r["library"] for r in rows), "library")
        + _options("format", (r.get("format") for r in rows), "format")
        + _options("type", (r.get("type") for r in rows), "type")
        + _options("band", (("failed" if r.get("failed") else (r.get("band") or trust_band(r.get("confidence")))) for r in rows), "trust band")
        + _options("publisher", ((r.get("publisher") or publisher_of(r.get("key"))) for r in rows), "publisher")
        + _options("state", (("failed" if r.get("failed") else "read") for r in rows), "state")
        + '<label>changes at least<input id="f-min" type="number" min="0" step="1" placeholder="0"></label>'
        + '<button type="button" id="f-clear">clear</button>'
        + '<span class="count" id="count"></span></div>'
    )
    heads = [
        ("paper", "title", False), ("library", "library", False), ("format", "format", False),
        ("pages", "pages", True), ("type", "type", False), ("trust", "trust", True),
        ("changes", "changes", True), ("by pass", "", False), ("cites/refs", "cites", True),
        ("err/warn", "errors", True),
    ]
    head = "".join(
        f'<th{f" data-key=\"{key}\" data-numeric=\"{int(numeric)}\"" if key else ""}'
        f'{" class=\"num\"" if numeric else ""}>{_e(label)}'
        f'{" <span class=\"arrow\"></span>" if key else ""}</th>'
        for label, key, numeric in heads
    )
    html_out = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>What the reader did — {len(rows)} papers</title><style>{INDEX_CSS}</style></head><body>
<header class="top"><h1>What the reader did</h1>
<div class="meta">{len(read)} papers read · {sum(r["pages"] for r in read)} pages · {sum(r["changes"] for r in read)} modifications ({_e(summary)})
{f" · {len(failed)} failed" if failed else ""}</div></header>
<main>
{controls}
<div class="side">
<table class="papers grid" id="papers"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>
{_publisher_totals(rows)}
</div>
</main><script>{INDEX_JS}</script></body></html>"""
    path = out_root / "index.html"
    path.write_text(html_out, encoding="utf-8")
    (out_root / "index.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m litrag_parser.review", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lib", action="append", required=True, type=Path, help="a library directory (repeatable)")
    ap.add_argument("--out", required=True, type=Path, help="where the reports are written")
    ap.add_argument("--limit", type=int, default=0, help="at most this many papers per library")
    ap.add_argument("--key", action="append", default=[], help="only these papers")
    ap.add_argument("--format", choices=["pdf", "jats"], help="only papers of this format")
    ap.add_argument("--changed-only", action="store_true", help="skip papers the reader did not modify")
    ap.add_argument("--scale", type=float, default=1.6, help="page image scale (1.6 ≈ 115 dpi)")
    ap.add_argument("--fresh", action="store_true", help="empty the output directory first")
    args = ap.parse_args(argv)

    root = args.lib[0].resolve().parent
    lanes.configure_from_env(root)
    out_root: Path = args.out
    if args.fresh and out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for lib in args.lib:
        papers = parsed_papers(lib, args.key or None)
        if args.format:
            papers = [p for p in papers if p.get("format") == args.format]
        if args.limit:
            papers = papers[: args.limit]
        for i, row in enumerate(papers, 1):
            try:
                record = render_paper(lib, row, out_root, args.scale)
            except Exception as exc:  # one bad paper must not stop the review — but it is named
                print(f"  x {lib.name}/{row['key']}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                rows.append(failed_row(lib, row, exc))
                continue
            if args.changed_only and not record["changes"]:
                continue
            rows.append(record)
            print(f"  {lib.name} {i}/{len(papers)} · {record['format']} · {record['pages']}pp · "
                  f"{record['changes']} changes · {record['type']} · {record['report']}", file=sys.stderr, flush=True)
    index = render_index(rows, out_root)
    print(json.dumps({"papers": len(rows), "index": str(index)}, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
