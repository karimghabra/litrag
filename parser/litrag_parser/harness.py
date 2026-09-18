"""The corpus harness: every paper of a library through the reader, measured.

A fixture proves one rule on one paper; a library shows what the reader does to two
hundred. This runs each parsed paper of a library — from its saved Docling document, no
models — through the tree builder, the citation linker and the audit, and reports per
paper what a person checks first: is the title the title, was a methods section found,
how many nodes is the front matter, how many nodes are wrong, how many citations linked.
Then the corpus in one table, the worst papers, and against a saved baseline, what got
better and what got worse — so a change to the reader is judged on the corpus, not on
the paper it was written for.

    uv run --project parser python -m litrag_parser.harness --lib ~/.protracker/library/looped-ligament
    …  --json harness.json                  save the run
    …  --baseline harness.json --gate       compare with a saved run; exit 1 on a regression
    …  --worst 20                           the papers to look at first
    …  --show <key>                         one paper's headings, front matter and findings

Nothing here runs Docling or the generative judge (their verdicts replay from the rows).
The oracle of meaning is consulted for any text no row answers, through Ollama on this
machine, unless `LITRAG_LANES=off`; the report says whether it was reachable, since a
run with it and a run without it read a heading the vocabulary does not know differently.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

from . import lanes
from .audit import audit_tree, summarize as summarize_audit
from .citations import link_citations
from .citations import summarize as summarize_citations
from .confidence import assess as assess_confidence
from .edges import link_edges, summarize as summarize_edges
from .facets import role_of
from .glyphs import glyph_residue
from .judge import Judge
from .library import parsed_papers, safe_key
from .paper_type import decide as decide_type
from .recover import recover_from_pdf
from .tree import _FURNITURE, Tree, build_tree

GENERIC_TITLES = {"original research", "original article", "research article", "article", "review", "review article", "abstract", "abstracts", "introduction", "letter", "communication", "full paper", "full length article", "short communication", "editorial", "case report", "brief report", "untitled", "research", "research paper", "report", "paper", "original paper", "major review", "topical review", "hhs public access", "author manuscript", "supporting information", "supporting information for", "regular article", "open access", "perspective", "commentary", "mini review", "minireview", "technical note"}
_REVIEWISH = re.compile(r"\b(review|progress|advances?|perspectives?|overview|state of the art|roadmap|insights|challenges|opportunities|principles|trends)\b", re.I)


def title_ok(title: str, key: str) -> bool:
    t = (title or "").strip()
    words = t.split()
    if not t or t.lower().strip(" .:") in GENERIC_TITLES or len(words) < 3:
        return False  # "The Halogen Bond" is a title; a word or two is a label
    if t.startswith(("doi_", "sha_", "doi:", "sha:")) or t == key:
        return False
    return not (t.isupper() and len(words) <= 3)


def pdf_title(path: Path) -> str | None:
    """A PDF's own Title metadata, when it is a title and not a file name."""
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        meta = pdf.get_metadata_dict() or {}
        pdf.close()
    except Exception:
        return None
    t = re.sub(r"\s+", " ", str(meta.get("Title") or "")).strip()
    if len(t.split()) < 4 or re.search(r"\.(docx?|pdf|tex|indd)\b|microsoft word|untitled|^doi\b", t, re.I):
        return None
    return t


_WORD = re.compile(r"[a-z0-9]{3,}")


_RUNNING = re.compile(r"downloaded from|^\s*journal of|vol\.? ?\d|issue \d|©|all rights reserved|all rights, including|terms (?:&|and) conditions|https?://doi\.org|wiley online library|creative commons|^\s*\d+\s*$|^[\d\s.]+$", re.I)


def dropped_sentences(tree: Tree, path: Path) -> list[tuple[int, str]]:
    """Sentences on a PDF's pages (pdfium's text layer, lines of eight words or more) that no
    node holds — not figure text, not a running head, not the title, not a table's cells:
    prose the layout model dropped, or filed under a picture."""
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
    except Exception:
        return []
    from collections import Counter

    letters = lambda s: re.sub(r"[^a-z]", "", s.lower())  # noqa: E731  — "signi fi cantly" and "inte- grated" read whole
    blob: dict[int, str] = {}
    for n in tree.walk():
        if not n.page:
            continue
        text = (n.text or "") + " " + (n.heading or "")  # a section's words are its heading
        if n.table:
            text += " " + " ".join(c for row in n.table.get("cells", []) for c in row)
        for page in n.pages or [n.page]:
            blob[page] = blob.get(page, "") + letters(text)
    blob[1] = blob.get(1, "") + letters(tree.title or "")  # the title is the root's, not a node's
    out: list[tuple[int, str]] = []
    try:
        pages_text = [pdf[i].get_textpage().get_text_range() for i in range(len(pdf))]
        # a line on three or more pages is a running head, whatever it says
        seen_on: dict[str, set[int]] = {}
        for i, text in enumerate(pages_text):
            for line in text.splitlines():
                norm = re.sub(r"\d+", "#", " ".join(line.split()).lower())
                if len(norm) > 12:
                    seen_on.setdefault(norm, set()).add(i)
        for i, text in enumerate(pages_text):
            got = blob.get(i + 1, "")
            for line in text.splitlines():
                line = " ".join(line.split())
                words = _WORD.findall(line.lower())
                if len(words) < 8 or sum(1 for w in words if not w.isdigit()) < 6:
                    continue
                if _RUNNING.search(line) or _FURNITURE.search(line) or len(seen_on.get(re.sub(r"\d+", "#", line.lower()), ())) >= 3:
                    continue
                key = letters(line)
                if len(key) < 24 or key[:24] in got or key[-24:] in got or key[len(key) // 2 - 12 : len(key) // 2 + 12] in got:
                    continue
                out.append((i + 1, line))
    finally:
        pdf.close()
    return out


def page_coverage(tree: Tree, path: Path) -> list[tuple[int, float]]:
    """Per page of a PDF, the share of the page's words (pdfium's text layer) that reached a
    node on that page — where the layout model dropped a block, this is where it shows."""
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
    except Exception:
        return []
    from collections import Counter

    have: dict[int, Counter[str]] = {}
    for n in tree.walk():
        if n.page and (n.text or n.heading):
            words = _WORD.findall(((n.text or "") + " " + (n.heading or "")).lower())
            for page in n.pages or [n.page]:  # a paragraph joined across a break counts for every page it spans
                have.setdefault(page, Counter()).update(words)
    out: list[tuple[int, float]] = []
    try:
        for i in range(len(pdf)):
            words = Counter(_WORD.findall(pdf[i].get_textpage().get_text_range().lower()))
            total = sum(words.values())
            if total < 40:
                continue  # a figure page, a blank
            got = have.get(i + 1, Counter())
            out.append((i + 1, round(sum(min(c, got.get(w, 0)) for w, c in words.items()) / total, 3)))
    finally:
        pdf.close()
    return out


def measure(tree: Tree, key: str, fmt: str, source: Path | None, paper_type: dict[str, str] | None = None) -> dict[str, Any]:
    findings = audit_tree(tree, (paper_type or {}).get("type"))
    coverage = page_coverage(tree, source) if fmt == "pdf" and source and source.exists() else []
    dropped = dropped_sentences(tree, source) if fmt == "pdf" and source and source.exists() else []
    errors = [f for f in findings if f.severity == "error"]
    refs, cites = link_citations(tree, source.read_bytes() if fmt == "jats" and source and source.exists() else None)
    edges = summarize_edges(tree, link_edges(tree, key, lanes.active()))
    front = next((n for n in tree.root.children if n.type == "section" and n.heading == "Front matter"), None)
    tops = [n for n in tree.root.children if n.type == "section"]
    paragraphs = sum(1 for n in tree.walk() if n.type == "paragraph")
    # A review has no methods and no results: numbered topical sections between an introduction and a
    # conclusion. Its title usually says so; its skeleton always does.
    topical = [n for n in tree.walk() if n.type == "section" and (n.level or 3) <= 2 and n.heading not in ("Front matter", None) and role_of(n.heading) == "other"]
    has_results = any(n.type == "section" and n.heading and role_of(n.heading) in ("results", "results-discussion") for n in tree.walk())
    review_like = (bool(_REVIEWISH.search(tree.title or "")) or (len(topical) >= 3 and not has_results)) and not tree.has_methods
    sure = assess_confidence(tree, paper_type)
    return {
        "confidence": sure["confidence"],
        "confidence_reasons": sure["reasons"],
        "key": key,
        "format": fmt,
        "title": tree.title,
        "title_ok": title_ok(tree.title, key),
        "type": (paper_type or {}).get("type", "other"),
        "type_source": (paper_type or {}).get("source", "none"),
        "subtype": (paper_type or {}).get("subtype"),
        "type_notes": sum(1 for n in (paper_type or {}).get("notes", []) if n.get("kind") == "type-disagreement"),
        "has_methods": tree.has_methods,
        "review_like": review_like,
        "sections": sum(1 for n in tree.walk() if n.type == "section"),
        "top_headings": [n.heading for n in tops][:14],
        "lanes": [[n.heading, n.role, n.label] for n in tops],  # what each top-level section was taken for, so a lane lost is a named section
        "built_headings": sum(1 for n in tops if n.label == "built"),
        "canonical": sum(1 for n in tops if n.canonical),
        "canonical_meaning": tree.repairs.get("canonical_meaning", 0),
        "notes": len(tree.notes),
        "roles": tree.roles,
        "nodes": sum(1 for _ in tree.walk()) - 1,
        "paragraphs": paragraphs,
        "front_matter_nodes": len(front.children) if front else 0,
        "front_matter_kinds": sorted({(c.label if c.type == "meta" else c.type) for c in front.children}) if front else [],
        "errors": len(errors),
        "error_kinds": summarize_audit(errors),
        "warnings": sum(1 for f in findings if f.severity == "warn"),
        "pages": len(tree.pages),
        "dropped": tree.dropped,
        "repairs": tree.repairs,
        "coverage_min": min((c for _, c in coverage), default=None),
        "coverage_mean": round(sum(c for _, c in coverage) / len(coverage), 3) if coverage else None,
        "thin_pages": [p for p, c in coverage if c < 0.6],
        "dropped_lines": len(dropped),
        "dropped_pages": sorted({p for p, _ in dropped}),
        "dropped_sample": [f"p{p}: {t[:100]}" for p, t in dropped[:3]],
        "glyph_residue": sum(glyph_residue(n.text) for n in tree.walk() if n.text),
        "edges": edges,
        "findings_linked": edges.get("linked", 0),
        **summarize_citations(refs, cites),
    }


def library_papers(lib: Path) -> list[dict[str, Any]]:
    """(key, format, file) of every parsed paper, from the library's store."""
    store = lib / "store.sqlite"
    if not store.exists():
        return []
    conn = sqlite3.connect(store)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT key, format, file FROM papers WHERE status = 'parsed' ORDER BY added_at, key")]
    conn.close()
    return rows


_safe = safe_key


def read_paper(lib: Path, row: dict[str, Any]) -> tuple[Tree, dict[str, Any], bytes | None]:
    """One parsed paper (a row of `parsed_papers`) read again from its saved Docling document
    the way a rebuild reads it — the text layer recovered, the judgments already given reused,
    no model asked: its tree, its type, and the JATS file's bytes when it is one."""
    source = row["source"]
    hint = pdf_title(source) if row["format"] == "pdf" and source and source.exists() else None
    conn = sqlite3.connect(Path(lib) / "store.sqlite")
    conn.row_factory = sqlite3.Row
    judge = Judge(conn, row["key"], ask_model=False)  # the verdicts already given, never the model
    doc = json.loads(row["raw"].read_text("utf-8"))
    recover_from_pdf(doc, source if row["format"] == "pdf" else None)
    tree = build_tree(doc, row["key"], title_hint=hint, judge=judge)
    conn.close()
    xml = source.read_bytes() if row["format"] == "jats" and source and source.exists() else None
    kind = decide_type(tree, jats_xml=xml, pub_types=row.get("pub_types"), oracle=lanes.active())
    tree.notes.extend(kind.get("notes", []))  # a label against another, or against the shape: the audit shows it
    return tree, kind, xml


def run_library(lib: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in parsed_papers(lib):
        source = row["source"]
        tree, kind, _ = read_paper(lib, row)
        rec = measure(tree, row["key"], row["format"] or "?", source, kind)
        rec["library"] = lib.name
        out.append(rec)
    return out


def corpus_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    def rate(rows: list[dict[str, Any]], field: str) -> str:
        return f"{sum(1 for r in rows if r[field])}/{len(rows)}" if rows else "0/0"

    by_format: dict[str, dict[str, Any]] = {}
    for fmt in sorted({r["format"] for r in records}):
        rows = [r for r in records if r["format"] == fmt]
        by_format[fmt] = {
            "papers": len(rows),
            "title_ok": rate(rows, "title_ok"),
            "has_methods": rate(rows, "has_methods"),
            "methods_or_review": f"{sum(1 for r in rows if r['has_methods'] or r['review_like'])}/{len(rows)}",
            "clean": f"{sum(1 for r in rows if r['errors'] == 0)}/{len(rows)}",
            "errors": sum(r["errors"] for r in rows),
            "front_matter_nodes_mean": round(sum(r["front_matter_nodes"] for r in rows) / len(rows), 1) if rows else 0,
            "front_matter_nodes_max": max((r["front_matter_nodes"] for r in rows), default=0),
            "citations": sum(r["citations"] for r in rows),
            "linked": f"{sum(1 for r in rows if r['citations'])}/{sum(1 for r in rows if r['refs'])}",
            "coverage_mean": round(sum(r["coverage_mean"] for r in rows if r["coverage_mean"] is not None) / max(1, sum(1 for r in rows if r["coverage_mean"] is not None)), 3),
            "papers_with_thin_pages": sum(1 for r in rows if r["thin_pages"]),
            "dropped_lines": sum(r["dropped_lines"] for r in rows),
            "papers_with_dropped_lines": sum(1 for r in rows if r["dropped_lines"]),
            "glyph_residue": sum(r["glyph_residue"] for r in rows),
            "judged_joins": sum(r["repairs"].get("judged", 0) for r in rows),
            "built_headings": sum(r.get("built_headings", 0) for r in rows),
            "papers_with_built_headings": sum(1 for r in rows if r.get("built_headings")),
            "laned_by_content": sum(r["repairs"].get("laned", 0) for r in rows),
            "lane_disagreements": sum(r["repairs"].get("lane_disagreement", 0) for r in rows),
            "by_meaning": {k: sum(r["repairs"].get(k, 0) for r in rows) for k in ("front_meaning", "label_veto", "captions_meaning", "rejoined_meaning")},
            "edges": {k: sum(r.get("edges", {}).get(k, 0) for r in rows) for k in ("findings", "linked", "unlinked", "pointer", "terms", "caption", "similarity", "cites_figure")},
        }
    kinds: dict[str, int] = {}
    for r in records:
        for k, v in r["error_kinds"].items():
            kinds[k] = kinds.get(k, 0) + v
    types: dict[str, dict[str, int]] = {}
    subtypes: dict[str, int] = {}
    type_notes = 0
    for r in records:
        t = types.setdefault(r.get("type", "other"), {"papers": 0, "methods": 0, "jats": 0, "record": 0, "subject": 0, "title": 0, "printed": 0, "shape": 0, "default": 0, "meaning": 0, "none": 0})
        t["papers"] += 1
        t["methods"] += int(bool(r["has_methods"]))
        t[r.get("type_source", "none")] = t.get(r.get("type_source", "none"), 0) + 1
        if r.get("subtype"):
            subtypes[r["subtype"]] = subtypes.get(r["subtype"], 0) + 1
        type_notes += r.get("type_notes", 0)
    return {"papers": len(records), "by_format": by_format, "error_kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])), "types": dict(sorted(types.items(), key=lambda kv: -kv[1]["papers"])), "subtypes": dict(sorted(subtypes.items(), key=lambda kv: -kv[1])), "type_notes": type_notes}


def compare(records: list[dict[str, Any]], baseline: list[dict[str, Any]]) -> dict[str, list[str]]:
    """What changed per paper against a saved run: the regressions and the gains."""
    before = {(b["key"], b.get("format")): b for b in baseline}  # a paper's XML and its PDF are two readings, compared apart
    out: dict[str, list[str]] = {"title_lost": [], "title_gained": [], "methods_lost": [], "methods_gained": [], "lane_lost": [], "lane_gained": [], "links_lost": [], "errors_up": [], "errors_down": [], "citations_lost": [], "citations_gained": [], "new": [], "gone": []}
    for r in records:
        b = before.get((r["key"], r.get("format")))
        if b is None:
            out["new"].append(r["key"])
            continue
        # a section named in both runs (the nth of that heading) whose lane went to `other`, or
        # came from it; and headings the reader built that a run no longer builds
        def keyed(lanes: list[list[str]]) -> dict[tuple[str, int], str]:
            seen: dict[str, int] = {}
            out_: dict[tuple[str, int], str] = {}
            for row in lanes:
                h, role = row[0] or "", row[1]
                seen[h] = seen.get(h, 0) + 1
                out_[(h, seen[h])] = role
            return out_

        was, now = keyed(b.get("lanes") or []), keyed(r.get("lanes") or [])
        for k, role in now.items():
            if k in was and was[k] != "other" and role == "other":
                out["lane_lost"].append(f"{r['key']}  {k[0][:50]!r}: {was[k]} → other")
            if k in was and was[k] == "other" and role != "other":
                out["lane_gained"].append(f"{r['key']}  {k[0][:50]!r}: other → {role}")
        if b.get("built_headings", 0) > r.get("built_headings", 0):
            out["lane_lost"].append(f"{r['key']}  built headings {b['built_headings']} → {r['built_headings']}")
        if b.get("findings_linked", 0) and r.get("findings_linked", 0) < 0.8 * b["findings_linked"]:
            out["links_lost"].append(f"{r['key']}  findings linked {b['findings_linked']} → {r['findings_linked']}")
        if b["title_ok"] and not r["title_ok"]:
            out["title_lost"].append(f"{r['key']}  {b['title'][:50]!r} → {r['title'][:50]!r}")
        if not b["title_ok"] and r["title_ok"]:
            out["title_gained"].append(f"{r['key']}  {r['title'][:60]!r}")
        if b["has_methods"] and not r["has_methods"]:
            out["methods_lost"].append(r["key"])
        if not b["has_methods"] and r["has_methods"]:
            out["methods_gained"].append(r["key"])
        if r["errors"] > b["errors"]:
            out["errors_up"].append(f"{r['key']}  {b['errors']} → {r['errors']}  {r['error_kinds']}")
        if r["errors"] < b["errors"]:
            out["errors_down"].append(f"{r['key']}  {b['errors']} → {r['errors']}")
        if b["citations"] and r["citations"] < 0.8 * b["citations"]:
            out["citations_lost"].append(f"{r['key']}  {b['citations']} → {r['citations']}")
        if r["citations"] > b["citations"] * 1.2 + 2:
            out["citations_gained"].append(f"{r['key']}  {b['citations']} → {r['citations']}")
    seen = {(r["key"], r.get("format")) for r in records}
    out["gone"] = [f"{k[0]} ({k[1]})" for k in before if k not in seen]
    return out


def worst(records: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    def badness(r: dict[str, Any]) -> tuple[int, ...]:
        return (r["errors"] + (3 if not r["title_ok"] else 0) + (3 if not (r["has_methods"] or r["review_like"]) else 0) + max(0, r["front_matter_nodes"] - 6) + r["dropped_lines"], r["errors"])

    return sorted(records, key=badness, reverse=True)[:n]


def report(records: list[dict[str, Any]], *, n_worst: int) -> str:
    s = corpus_summary(records)
    lines = [f"{s['papers']} papers"]
    for fmt, m in s["by_format"].items():
        lines.append(f"  {fmt:<5} papers {m['papers']:>3} · title ok {m['title_ok']:>8} · methods {m['has_methods']:>8} (or review {m['methods_or_review']:>8}) · clean {m['clean']:>8} · errors {m['errors']:>4} · front matter {m['front_matter_nodes_mean']} mean/{m['front_matter_nodes_max']} max · citations {m['citations']:>6} in {m['linked']} papers with a reference list")
        if fmt == "pdf":
            lines.append(f"        page coverage {m['coverage_mean']} mean · {m['papers_with_thin_pages']} papers with a page under 60% (figure text counts against them) · dropped sentences {m['dropped_lines']} in {m['papers_with_dropped_lines']} papers · glyph residue {m['glyph_residue']} · judged joins {m['judged_joins']}")
        e = m["edges"]
        lines.append(f"        edges: findings {e['findings']} · linked to a method {e['linked']} · unlinked {e['unlinked']} · edges by pointer {e['pointer']}, by terms {e['terms']}, through captions {e['caption']}, by similarity {e['similarity']} · figure mentions {e['cites_figure']}")
        lines.append(f"        by content: built headings {m['built_headings']} in {m['papers_with_built_headings']} papers · sections laned {m['laned_by_content']} · disagreements noted {m['lane_disagreements']} · front {m['by_meaning']['front_meaning']} · label vetoes {m['by_meaning']['label_veto']} · captions {m['by_meaning']['captions_meaning']} · tails {m['by_meaning']['rejoined_meaning']}")
    if lanes.active():
        o = lanes.active().summary()
        lines.append("  by meaning: " + (", ".join(f"{k} named {v['named']} (asked {v['asked']})" for k, v in o["kinds"].items()) or "nothing asked") + (f" · EMBEDDER UNREACHABLE ({o['error']}): unknown texts read as `other` and were not stored" if o["down"] else ""))
    else:
        lines.append("  by meaning: no oracle (LITRAG_LANES=off): every text the vocabulary does not know is `other`")
    lines.append("  errors by kind: " + (", ".join(f"{k} {v}" for k, v in s["error_kinds"].items()) or "none"))
    lines.append("  types: " + ", ".join(f"{t} {v['papers']} (methods {v['methods']}; by record {v['record']}, file {v['jats']}, subject {v.get('subject', 0)}, title {v.get('title', 0)}, page {v['printed']}, shape {v.get('shape', 0)}, default {v.get('default', 0)}, none {v['none']})" for t, v in s["types"].items()))
    lines.append(f"  subtypes: {', '.join(f'{k} {v}' for k, v in s.get('subtypes', {}).items()) or 'none'} · type disagreements noted {s.get('type_notes', 0)}")
    for fmt in sorted({r["format"] for r in records}):
        conf = [r.get("confidence") for r in records if r["format"] == fmt and r.get("confidence") is not None]
        if conf:
            why: dict[str, int] = {}
            for r in records:
                if r["format"] == fmt:
                    for reason in (r.get("confidence_reasons") or [])[:1]:
                        key = reason.split(":")[0].lstrip("0123456789% ")[:48]
                        why[key] = why.get(key, 0) + 1
            top = ", ".join(f"{k} {v}" for k, v in sorted(why.items(), key=lambda kv: -kv[1])[:4])
            lines.append(f"  confidence ({fmt}): at least 0.9 in {sum(1 for c in conf if c >= 0.9)} · 0.5 to 0.9 in {sum(1 for c in conf if 0.5 <= c < 0.9)} · under 0.5 in {sum(1 for c in conf if c < 0.5)}" + (f" · first reasons: {top}" if top else ""))
    lines.append(f"\nworst {n_worst}:")
    for r in worst(records, n_worst):
        flags = [] if r["title_ok"] else ["NO TITLE"]
        if not (r["has_methods"] or r["review_like"]):
            flags.append("NO METHODS")
        if r["front_matter_nodes"] > 6:
            flags.append(f"FRONT {r['front_matter_nodes']}")
        if r["dropped_lines"]:
            flags.append(f"DROPPED {r['dropped_lines']}")
        lines.append(f"  {r['key'][:36]:<36} {r['format']:<4} errors {r['errors']:>2} {' '.join(flags):<24} {r['title'][:60]!r}")
        if r["error_kinds"]:
            lines.append(f"      {r['error_kinds']}  headings: {r['top_headings'][:8]}")
        if r["dropped_lines"]:
            lines.append(f"      sentences no node holds, on pages {r['dropped_pages'][:10]}: " + " | ".join(r["dropped_sample"]))
    return "\n".join(lines)


def show(records: list[dict[str, Any]], key: str, libs: list[Path]) -> str:
    rec = next((r for r in records if r["key"] == key), None)
    if rec is None:
        return f"no paper {key!r}"
    lib = next(l for l in libs if l.name == rec["library"])
    raw = lib / "parsed" / f"{_safe(key)}.docling.json"
    tree = build_tree(json.loads(raw.read_text("utf-8")), key)
    lines = [json.dumps({k: v for k, v in rec.items() if k not in ("top_headings",)}, ensure_ascii=False, indent=1), "sections:"]
    for n in tree.walk():
        if n.type == "section":
            lines.append(f"  {'  ' * (n.depth - 1)}{n.heading!r} [{n.role}] {sum(1 for c in n.children if c.type != 'section')} items")
    front = next((n for n in tree.root.children if n.heading == "Front matter"), None)
    if front:
        lines.append("front matter:")
        for c in front.children:
            lines.append(f"  [{c.type}:{c.label}] {c.text[:110]!r}")
    for f in audit_tree(tree):
        if f.severity != "info":
            lines.append(f"  [{f.severity}] {f.kind} {f.node_id} ‹{f.text[:90]}›")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="litrag_parser.harness", description=__doc__.split("\n\n")[0])
    ap.add_argument("--lib", action="append", default=[], help="a library directory (repeatable)")
    ap.add_argument("--json", help="save the run here")
    ap.add_argument("--baseline", help="a saved run to compare with")
    ap.add_argument("--gate", action="store_true", help="exit 1 when a title, a methods section or a section's lane is lost, citations fall, or errors rise against the baseline")
    ap.add_argument("--worst", type=int, default=15)
    ap.add_argument("--show", help="one paper's headings, front matter and findings")
    args = ap.parse_args(argv)
    if args.lib:
        lanes.configure_from_env(Path(args.lib[0]).expanduser().resolve().parent)
    libs = [Path(l).expanduser() for l in args.lib]
    if not libs:
        ap.print_help()
        return 2
    records: list[dict[str, Any]] = []
    for lib in libs:
        records.extend(run_library(lib))
    if args.show:
        print(show(records, args.show, libs))
        return 0
    print(report(records, n_worst=args.worst))
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": corpus_summary(records), "papers": records}, ensure_ascii=False, indent=1), "utf-8")
        print(f"\nsaved {args.json}")
    code = 0
    if args.baseline:
        base = json.loads(Path(args.baseline).read_text("utf-8"))
        diff = compare(records, base["papers"] if isinstance(base, dict) else base)
        print("\nagainst the baseline:")
        for kind, items in diff.items():
            if items:
                print(f"  {kind} ({len(items)}):")
                for it in items[:25]:
                    print(f"    {it}")
        regressions = diff["title_lost"] + diff["methods_lost"] + diff["lane_lost"] + diff["links_lost"] + diff["errors_up"] + diff["citations_lost"]
        if args.gate and regressions:
            print(f"\nGATE: {len(regressions)} regressions")
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
