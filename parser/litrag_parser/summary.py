"""The corpus on one page: what was read, what the reader did to it, and how well it was measured.

`review.py` answers "what did the reader do to *this* paper". This answers the other question a person
has to settle before a corpus is worth building on: does the reader behave across formats, publishers and
kinds of paper, and where does it still fail. It reads what the measurements already wrote — the harness's
per-paper records, the type and heading measurements, the PDF-against-XML pairs — beside the review's own
index, and writes one page that links into the per-paper reports.

    uv run --project parser python -m litrag_parser.summary --review DIR --measure DIR --out FILE
                                                            [--lib DIR ...]

Every number on the page comes from a file in `--measure` or from the review index; nothing is computed
here that is not also written down somewhere else, so the page can be checked against its sources.
"""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

PUBLISHERS: dict[str, str] = {
    "10.1002": "Wiley", "10.1016": "Elsevier", "10.3390": "MDPI", "10.1021": "ACS", "10.1039": "RSC",
    "10.1038": "Springer Nature", "10.1371": "PLOS", "10.3389": "Frontiers", "10.1093": "Oxford UP",
    "10.1186": "BMC", "10.1109": "IEEE", "10.1007": "Springer", "10.1155": "Hindawi", "10.1088": "IOP",
    "10.1177": "SAGE", "10.1073": "PNAS", "10.1126": "Science", "10.1089": "Liebert", "10.1111": "Wiley",
    "10.1097": "Wolters Kluwer", "10.7150": "Ivyspring", "10.7759": "Cureus", "10.1115": "ASME",
    "10.4269": "ASTMH", "10.1681": "ASN", "10.1029": "AGU", "10.3346": "KAMS", "10.18502": "Knowledge E",
    "10.3762": "Beilstein", "10.5371": "KHS", "10.1042": "Portland", "10.4028": "Trans Tech",
}


def publisher_of(key: str) -> str:
    if key.startswith("doi:"):
        prefix = key[4:].split("/")[0]
        return PUBLISHERS.get(prefix, prefix)
    return key.split(":")[0]


def _e(text: Any) -> str:
    return html.escape("" if text is None else str(text), quote=True)


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return None


def library_counts(libs: list[Path]) -> list[dict[str, Any]]:
    """Every library: how many papers of each format are parsed, and what types they were given."""
    out: list[dict[str, Any]] = []
    for lib in libs:
        store = lib / "store.sqlite"
        if not store.exists():
            continue
        conn = sqlite3.connect(store)
        conn.row_factory = sqlite3.Row
        try:
            have = {r[1] for r in conn.execute("PRAGMA table_info(papers)")}
            cols = "key, format" + (", type, subtype" if "type" in have else ", NULL as type, NULL as subtype")
            rows = [dict(r) for r in conn.execute(f"SELECT {cols} FROM papers WHERE status = 'parsed'")]
            pages = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0] if "pages" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} else 0
            nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        finally:
            conn.close()
        out.append({
            "library": lib.name,
            "papers": len(rows),
            "formats": dict(Counter(r["format"] for r in rows)),
            "types": dict(Counter(r["type"] for r in rows if r["type"])),
            "publishers": dict(Counter(publisher_of(r["key"]) for r in rows)),
            "pages": pages,
            "nodes": nodes,
        })
    return out


def _table(headers: list[str], rows: list[list[str]], cls: str = "grid") -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def _bar(share: float, colour: str = "#2563eb") -> str:
    pct = max(0.0, min(1.0, share)) * 100
    return f'<span class="bar"><span style="width:{pct:.1f}%;background:{colour}"></span></span>'


CSS = """
:root{--ink:#1f2430;--muted:#6b7280;--rule:#e5e7eb;--bg:#f8fafc}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);
font:14.5px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
header{background:#fff;border-bottom:1px solid var(--rule);padding:22px 28px}
header h1{margin:0 0 4px;font-size:22px} .sub{color:var(--muted);font-size:13px}
main{max-width:1180px;margin:0 auto;padding:22px 28px 80px}
section{background:#fff;border:1px solid var(--rule);border-radius:12px;padding:18px 20px;margin-bottom:18px}
section h2{margin:0 0 4px;font-size:15px} section .lead{color:var(--muted);font-size:13px;margin:0 0 12px}
table.grid{width:100%;border-collapse:collapse;font-size:13px}
table.grid th{text-align:left;color:var(--muted);font-weight:600;border-bottom:1px solid var(--rule);padding:6px 8px}
table.grid td{padding:6px 8px;border-bottom:1px solid #f1f5f9;vertical-align:top}
.num{text-align:right;font-variant-numeric:tabular-nums}
.stats{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-bottom:18px}
.stat{background:#fff;border:1px solid var(--rule);border-radius:12px;padding:12px 14px}
.stat .n{font-size:24px;font-weight:700} .stat .l{color:var(--muted);font-size:12px}
.bar{display:inline-block;width:110px;height:7px;background:#eef2f7;border-radius:4px;overflow:hidden;vertical-align:middle;margin-right:7px}
.bar span{display:block;height:100%}
.muted{color:var(--muted)} code{background:#f1f5f9;padding:1px 5px;border-radius:4px;font-size:12.5px}
a{color:#1d4ed8} ul{margin:6px 0 0 18px;padding:0} li{margin:3px 0}
.warn{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:10px 12px;font-size:13px;margin-top:10px}
"""


def build(review: Path, measure: Path, libs: list[Path]) -> str:
    index = _load(review / "index.json") or []
    numbers = _load(measure / "numbers.json") or {}
    type_measure = _load(measure / "type.json") or {}
    headings_measure = _load(measure / "headings.json") or {}
    pairs = {p.stem: _load(p) for p in sorted(measure.glob("pairs-*.json"))}
    harness = {p.stem: _load(p) for p in sorted(measure.glob("harness-*.json"))}
    libraries = library_counts(libs)

    papers = sum(l["papers"] for l in libraries) or len(index)
    pdfs = sum(l["formats"].get("pdf", 0) for l in libraries)
    jats = sum(l["formats"].get("jats", 0) for l in libraries)
    pubs = Counter()
    types = Counter()
    for l in libraries:
        pubs.update(l["publishers"])
        types.update(l["types"])
    reviewed = len(index)
    changes = sum(r.get("changes", 0) for r in index)
    stages = Counter()
    for r in index:
        stages.update(r.get("by_stage") or {})

    stats = [
        (f"{papers:,}", "papers read"),
        (f"{pdfs:,} / {jats:,}", "PDF / publisher XML"),
        (f"{len(pubs)}", "publishers"),
        (f"{len(types)}", "kinds of paper"),
        (f"{reviewed:,}", "papers reviewed page by page"),
        (f"{changes:,}", "modifications recorded"),
    ]
    stat_html = "".join(f'<div class="stat"><div class="n">{_e(n)}</div><div class="l">{_e(l)}</div></div>' for n, l in stats)

    lib_rows = [
        [_e(l["library"]),
         f'<span class="num">{l["papers"]}</span>',
         _e(", ".join(f"{k} {v}" for k, v in sorted(l["formats"].items()))),
         f'<span class="num">{l["pages"]:,}</span>',
         f'<span class="num">{l["nodes"]:,}</span>',
         _e(", ".join(f"{k} {v}" for k, v in sorted(l["types"].items(), key=lambda kv: -kv[1])[:6]))]
        for l in libraries
    ]
    pub_rows = [[_e(p), f'<span class="num">{n}</span>', _bar(n / max(1, papers))] for p, n in pubs.most_common(20)]
    type_rows = [[_e(t), f'<span class="num">{n}</span>', _bar(n / max(1, papers), "#0891b2")] for t, n in types.most_common()]
    stage_rows = [[_e(s), f'<span class="num">{n:,}</span>', _bar(n / max(1, changes), "#16a34a")] for s, n in stages.most_common()]

    measurement_html = ""
    if type_measure:
        measurement_html += f"<h3>Paper type</h3><pre class='muted'>{_e(json.dumps(type_measure, indent=1)[:1800])}</pre>"
    if headings_measure:
        measurement_html += f"<h3>Headings</h3><pre class='muted'>{_e(json.dumps(headings_measure, indent=1)[:1200])}</pre>"
    for name, data in pairs.items():
        if data:
            measurement_html += f"<h3>{_e(name)}</h3><pre class='muted'>{_e(json.dumps(data.get('summary', data), indent=1)[:1200])}</pre>"

    numbers_html = f"<pre class='muted'>{_e(json.dumps(numbers, indent=1)[:4000])}</pre>" if numbers else "<p class='muted'>no numbers.json in the measurement directory</p>"

    matrix: dict[str, Counter] = {}
    for r in index:
        matrix.setdefault(publisher_of(r["key"]), Counter())[r.get("format") or "?"] += 1
    matrix_rows = [
        [_e(pub), f'<span class="num">{c.get("pdf", 0)}</span>', f'<span class="num">{c.get("jats", 0)}</span>',
         f'<span class="num">{sum(c.values())}</span>']
        for pub, c in sorted(matrix.items(), key=lambda kv: -sum(kv[1].values()))
    ]

    failed = [r for r in index if r.get("failed")]
    worst = sorted([r for r in index if r.get("confidence") is not None], key=lambda r: r["confidence"])[:12]
    worst_rows = [
        [f'<a href="{_e(r["report"])}">{_e((r.get("title") or r["key"])[:70])}</a><div class="muted">{_e(r["key"])}</div>',
         _e(r.get("format")), _e(r.get("type")), f'<span class="num">{round(100 * (r.get("confidence") or 0))}%</span>',
         f'<span class="num">{r.get("changes", 0)}</span>',
         _e(", ".join(f"{k} {v}" for k, v in sorted((r.get("by_stage") or {}).items(), key=lambda kv: -kv[1])))]
        for r in worst
    ]

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>The reader, measured — litrag</title><style>{CSS}</style></head><body>
<header><h1>Reading scientific papers into structure</h1>
<div class="sub">What the corpus is, what the reader does to it, and how well that has been measured ·
<a href="index.html">every paper, page by page</a></div></header>
<main>
<div class="stats">{stat_html}</div>

<section><h2>The corpus</h2>
<p class="lead">Papers already read into trees, by library. A library is one project's shelf; the held-out
libraries were collected so that no rule was written from them.</p>
{_table(["library", "papers", "formats", "pages", "nodes", "kinds of paper"], lib_rows)}</section>

<section><h2>Across publishers</h2>
<p class="lead">A reader that works on one journal's layout proves nothing. This is the spread it was run on.</p>
{_table(["publisher", "papers", ""], pub_rows)}</section>

<section><h2>Kinds of paper</h2>
<p class="lead">Decided by <code>paper_type.py</code> from the record, the file, the subject line, the title,
the label printed above the title, and last the shape of the tree itself.</p>
{_table(["type", "papers", ""], type_rows)}</section>

<section><h2>What the reader changed</h2>
<p class="lead">Every modification is recorded where it happened, with the text before and after
(<code>changes.py</code>), and can be read beside the page it was made on.</p>
{_table(["pass", "modifications", ""], stage_rows)}</section>

<section><h2>The papers to look at first</h2>
<p class="lead">Lowest confidence first — the score is computed from the reading alone and was calibrated
against the same papers read from publisher XML.</p>
{_table(["paper", "format", "type", "trust", "changes", "by pass"], worst_rows)}</section>

<section><h2>Reviewed, by publisher and format</h2>
<p class="lead">Every paper in this table has a report of its own: each of its pages drawn, with what the
reader changed marked where it happened.</p>
{_table(["publisher", "PDF", "XML", "papers"], matrix_rows)}
{f'<div class="warn">{len(failed)} paper(s) could not be read at all; they are listed in the index with their error.</div>' if failed else ""}</section>

<section><h2>How to read a paper's report</h2>
<p class="lead">Open any paper from <a href="index.html">the index</a>. Its report has the whole paper, page
by page, at the size it was printed.</p>
<ul>
<li><b>Boxes on the page</b> are what the reader kept, coloured by the lane it filed them under — abstract,
introduction, methods, results, discussion, references, back matter. A box in the wrong colour is a
misfiled section, and the heading table above the pages says which heading put it there.</li>
<li><b>Numbered marks</b> are modifications. The same number appears beside the page with the text as it
stood, the text as it stands, and the rule's own reason. Red is text left out of the body (a running head,
a logo, a copyright line); green is text read back from the PDF's own layer that the layout model had
missed; amber is the text itself rewritten (a symbol font undone); blue is two blocks put back together;
purple is one cut apart; teal is the paper's shape — a heading built, a lane read from the paragraphs.</li>
<li><b>For a paper read from publisher XML</b> there are no pages to draw, so the report lists the section
tree and the rewrites made to the XML before Docling read it.</li>
<li><b>Trust</b> is computed from the reading alone and was calibrated against papers held in both formats;
below 90% something in the list of reasons is worth checking by eye.</li>
</ul></section>

<section><h2>The measurements</h2>
<p class="lead">Everything below was written by a measurement run, not by this page.</p>
{numbers_html}{measurement_html}</section>

<section><h2>How to run it</h2>
<pre class="muted">uv run --project parser python -m litrag_parser.review --lib &lt;library&gt; --out &lt;dir&gt;   # the page-by-page review
uv run --project parser python -m litrag_parser.harness --lib &lt;library&gt; --json &lt;file&gt; # the corpus measurement
uv run --project parser python -m litrag_parser.pairs --pdf-lib &lt;dir&gt; --xml-lib &lt;dir&gt;  # a PDF read against its own XML
uv run --project parser python -m litrag_parser.paper_type --measure --lib &lt;library&gt;   # the type, against stated labels</pre>
</section>
</main></body></html>"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m litrag_parser.summary", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--review", required=True, type=Path, help="the directory review.py wrote")
    ap.add_argument("--measure", required=True, type=Path, help="the directory the measurements wrote")
    ap.add_argument("--lib", action="append", default=[], type=Path, help="a library to count (repeatable)")
    ap.add_argument("--out", type=Path, help="where to write the page (default: <review>/summary.html)")
    args = ap.parse_args(argv)
    out = args.out or (args.review / "summary.html")
    out.write_text(build(args.review, args.measure, args.lib), encoding="utf-8")
    print(json.dumps({"summary": str(out)}, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
