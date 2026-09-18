"""The reader's work, page by page, for a person to check.

The harness says a corpus is 0.93 of something; the audit lists what looks wrong. Neither shows what the
reader actually *did* to a paper. This writes one page per paper — every page of it, drawn, with every node
the reader kept boxed in its lane's colour and every modification it made marked where it happened, the text
as it stood beside the text as it stands — and an index over a set of papers so that a reader's behaviour can
be checked across formats and publishers rather than on the one paper that was to hand.

    uv run --project parser python -m litrag_parser.review --lib DIR [--lib DIR] --out DIR
                                                           [--limit N] [--key KEY] [--format pdf|jats]
                                                           [--changed-only] [--scale 1.6]

Nothing is written to a library: the trees are rebuilt in memory exactly as `rebuild` would
(`harness.read_paper`), and the pages are rendered from the paper's own file.
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from . import lanes
from .audit import audit_tree
from .changes import stage_of
from .citations import link_citations, summarize as summarize_citations
from .confidence import assess as assess_confidence
from .harness import read_paper
from .library import parsed_papers, safe_key
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
    "other": "other",
}


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


def _change_row(change: dict[str, Any], index: int) -> str:
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
        f'<li class="change" id="c{index}" data-page="{_e(change.get("page") or "")}">'
        f'<div class="head"><span class="dot" style="background:{colour}"></span>'
        f'<span class="kind">{_e(change["kind"])}</span>'
        f'<span class="muted">{_e(change.get("why") or "")}</span></div>'
        f'{"".join(body)}</li>'
    )


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
    listed = "".join(_change_row(c, numbers[id(c)]) for c in changes)
    counts = Counter(c.get("stage") or stage_of(c["kind"]) for c in changes)
    summary = " · ".join(f'<span style="color:{STAGE_COLOURS.get(s, "#525252")}">{n} {_e(s)}</span>' for s, n in counts.most_common()) or '<span class="muted">nothing changed on this page</span>'
    return (
        f'<section class="page" id="page-{page_no}">'
        f'<div class="pagehead"><h3>Page {page_no}</h3><div class="summary">{summary}</div></div>'
        f'<div class="split"><div class="sheet"><img src="{_e(page["file"])}" loading="lazy" alt="page {page_no}">{"".join(marks)}</div>'
        f'<ol class="changes">{listed or "<li class=\'muted\'>no modification on this page</li>"}</ol></div>'
        f"</section>"
    )


def _headings_table(tree: Tree) -> str:
    rows = []
    for node in tree.walk():
        if node.type != "section" or not node.heading:
            continue
        rows.append(
            f"<tr><td>{'·' * max(0, (node.level or 1) - 1)} {_e(node.heading)}</td>"
            f"<td>{_e(node.canonical or '—')}</td><td><span class='lane' style='background:{LANE_COLOURS.get(node.role, '#d4d4d8')}22;"
            f"color:{LANE_COLOURS.get(node.role, '#525252')}'>{_e(node.role)}</span></td>"
            f"<td>{'built' if node.label == 'built' else ''}</td>"
            f"<td class='muted'>{_e(node.page or '')}</td></tr>"
        )
    return "<table class='grid'><thead><tr><th>heading, as printed</th><th>the catalogue's name</th><th>lane</th><th></th><th>page</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def _type_block(kind: dict[str, Any], tree: Tree) -> str:
    notes = [n for n in tree.notes if n.get("kind") == "type-disagreement"]
    sub = f'<span class="sub"> · {_e(kind.get("subtype"))}</span>' if kind.get("subtype") else ""
    detail = _e(kind.get("detail") or "")
    note_html = "".join(f'<div class="note">⚠ {_e(n.get("message"))}</div>' for n in notes)
    return (
        f'<div class="card"><h2>What kind of paper</h2>'
        f'<div class="big">{_e(kind.get("type", "other"))}{sub}</div>'
        f'<div class="muted">decided by <b>{_e(kind.get("source", "none"))}</b>{" · " + detail if detail else ""}</div>'
        f"{note_html}</div>"
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
"""


def render_paper(lib: Path, row: dict[str, Any], out_root: Path, scale: float) -> dict[str, Any]:
    """One paper's report. Returns the row the index is built from."""
    tree, kind, xml = read_paper(lib, row)
    refs, cites = link_citations(tree, xml)
    cite_counts = summarize_citations(refs, cites)
    findings = audit_tree(tree, kind.get("type"))
    conf = assess_confidence(tree, kind)
    key = row["key"]
    folder = out_root / f"{lib.name}--{safe_key(key)}"
    folder.mkdir(parents=True, exist_ok=True)
    images = page_images(row.get("source"), folder / "pages", scale)

    kept = boxes_of(tree)
    changes = list(tree.changes)
    numbers = {id(c): i + 1 for i, c in enumerate(changes)}
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
    if loose or not images:
        listed = "".join(_change_row(c, numbers[id(c)]) for c in loose + ([] if images else [c for p, cs in by_page_changes.items() if p for c in cs]))
        blocks.append(
            '<section class="page"><div class="pagehead"><h3>'
            + ("Changes with no page" if images else "Every change (this paper has no pages: it was read from XML)")
            + f'</h3></div><div class="split" style="grid-template-columns:1fr"><ol class="changes">{listed or "<li class=\'muted\'>none</li>"}</ol></div></section>'
        )

    legend = " ".join(
        f'<span><span class="dot" style="background:{c}"></span> <b>{_e(STAGE_WORDS.get(s, s))}</b></span>'
        for s, c in STAGE_COLOURS.items() if s != "other"
    )
    lane_legend = " ".join(f'<span><span class="dot" style="background:{c}"></span> {_e(l)}</span>' for l, c in LANE_COLOURS.items())
    html_out = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(tree.title or key)} — what the reader did</title><style>{PAGE_CSS}</style></head><body>
<header class="top"><h1>{_e(tree.title or key)}</h1>
<div class="meta">{_e(lib.name)} · {_e(row.get('format') or '?')} · {_e(key)} · {len(tree.pages)} pages ·
<a href="../index.html">every paper</a></div></header>
<main>
<div class="cards">
  {_type_block(kind, tree)}
  {_confidence_block(conf)}
  {_counts_block(tree, cite_counts, findings)}
  <div class="card wide"><h2>The sections it found</h2>{_headings_table(tree)}</div>
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
        "pages": len(tree.pages),
        "type": kind.get("type"),
        "subtype": kind.get("subtype"),
        "type_source": kind.get("source"),
        "confidence": conf.get("confidence"),
        "changes": len(changes),
        "by_stage": dict(stages),
        "refs": cite_counts.get("refs", 0),
        "citations": cite_counts.get("citations", 0),
        "errors": sum(1 for f in findings if f.severity == "error"),
        "warnings": sum(1 for f in findings if f.severity == "warn"),
        "report": f"{folder.name}/index.html",
    }


INDEX_CSS = PAGE_CSS + """
table.papers { width:100%; border-collapse:collapse; background:#fff; font-size:13px; }
table.papers th { position:sticky; top:0; background:#fff; z-index:2; }
table.papers td.title { max-width:420px; }
.pill { border-radius:5px; padding:1px 7px; font-size:11.5px; background:#eef2f7; }
"""


def render_index(rows: list[dict[str, Any]], out_root: Path) -> Path:
    rows = sorted(rows, key=lambda r: (r["library"], -(r["changes"] or 0)))
    body = []
    for r in rows:
        conf = r.get("confidence")
        colour = "#16a34a" if (conf or 0) >= 0.9 else "#d97706" if (conf or 0) >= 0.5 else "#dc2626"
        stages = " ".join(
            f'<span class="pill" style="background:{STAGE_COLOURS.get(s, "#525252")}1a;color:{STAGE_COLOURS.get(s, "#525252")}">{_e(s)} {n}</span>'
            for s, n in sorted((r.get("by_stage") or {}).items(), key=lambda kv: -kv[1])
        )
        body.append(
            f'<tr><td class="title"><a href="{_e(r["report"])}">{_e(r["title"] or r["key"])}</a>'
            f'<div class="muted">{_e(r["key"])}</div></td>'
            f'<td>{_e(r["library"])}</td><td>{_e(r["format"])}</td><td class="num">{r["pages"]}</td>'
            f'<td>{_e(r["type"])}{f"<div class=muted>{_e(r['subtype'])}</div>" if r.get("subtype") else ""}'
            f'<div class="muted">{_e(r["type_source"])}</div></td>'
            f'<td class="num" style="color:{colour}">{"" if conf is None else f"{round(100 * conf)}%"}</td>'
            f'<td class="num">{r["changes"]}</td><td>{stages}</td>'
            f'<td class="num">{r["citations"]}/{r["refs"]}</td>'
            f'<td class="num">{r["errors"]}/{r["warnings"]}</td></tr>'
        )
    totals = Counter()
    for r in rows:
        totals.update(r.get("by_stage") or {})
    summary = " · ".join(f"{n} {s}" for s, n in totals.most_common())
    html_out = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>What the reader did — {len(rows)} papers</title><style>{INDEX_CSS}</style></head><body>
<header class="top"><h1>What the reader did</h1>
<div class="meta">{len(rows)} papers · {sum(r["pages"] for r in rows)} pages · {sum(r["changes"] for r in rows)} modifications ({_e(summary)})</div></header>
<main><table class="papers grid"><thead><tr>
<th>paper</th><th>library</th><th>format</th><th class="num">pages</th><th>type</th>
<th class="num">trust</th><th class="num">changes</th><th>by pass</th><th class="num">cites/refs</th><th class="num">err/warn</th>
</tr></thead><tbody>{"".join(body)}</tbody></table></main></body></html>"""
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
            except Exception as exc:  # one bad paper must not stop the review
                print(f"  ✗ {lib.name}/{row['key']}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
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
