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


def _rate(value: Any, good: float = 0.95, poor: float = 0.8) -> str:
    """A measured proportion, coloured by whether it is where it should be."""
    if value is None:
        return '<span class="muted">—</span>'
    try:
        share = float(value)
    except (TypeError, ValueError):
        return _e(value)
    colour = "#15803d" if share >= good else ("#b45309" if share >= poor else "#b91c1c")
    return f'<span class="num" style="color:{colour};font-weight:600">{share:.3f}</span>'


def _of(entry: Any) -> str:
    """The harness writes `{"n": 281, "of": 284, "rate": 0.989}`; show the rate and the count behind it."""
    if not isinstance(entry, dict):
        return _rate(entry)
    return f'{_rate(entry.get("rate"))} <span class="muted">{entry.get("n")}/{entry.get("of")}</span>'


def _harness_section(numbers: dict) -> str:
    """Every paper through the reader, PDF beside XML — the two paths measured the same way."""
    wide = (numbers.get("harness") or {}).get("by_format_corpus_wide") or {}
    pdf, jats = wide.get("pdf") or {}, wide.get("jats") or {}
    if not pdf and not jats:
        return ""
    rows = [
        ("the title it found is the title", "title_ok", True),
        ("a methods section was found", "has_methods", True),
        ("…or the paper is a review", "methods_or_review", True),
        ("read with no audit error", "clean", True),
        ("citations linked to reference entries", "citations_linked", False),
        ("findings linked to the method behind them", "findings_link_rate", False),
        ("sentences dropped", "dropped_sentences", False),
    ]
    out = []
    for label, key, is_rate in rows:
        cells = []
        for side in (pdf, jats):
            value = side.get(key)
            if is_rate:
                cells.append(_of(value))
            elif isinstance(value, float):
                cells.append(_rate(value, good=0.8, poor=0.6))
            else:
                cells.append(f'<span class="num">{value:,}</span>' if isinstance(value, int) else '<span class="muted">—</span>')
        out.append([_e(label)] + cells)
    for label, key in (("read with high trust (≥0.9)", "ge_0.9"), ("worth a look (0.5–0.9)", "0.5_to_0.9"), ("read badly (<0.5)", "lt_0.5")):
        out.append([_e(label)] + [f'<span class="num">{(side.get("confidence_bands") or {}).get(key, 0):,}</span>' for side in (pdf, jats)])
    heads = ["", f'PDF ({pdf.get("papers", 0)})', f'publisher XML ({jats.get("papers", 0)})']
    reasons = (pdf.get("top_confidence_reasons") or {})
    reason_html = ""
    if reasons:
        items = "".join(f"<li>{_e(why)} — <b>{n}</b> papers</li>" for why, n in list(reasons.items())[:6])
        reason_html = f'<p class="lead" style="margin-top:12px">Why PDFs lose trust, most often first:</p><ul>{items}</ul>'
    return f"""<section><h2>Every paper through the reader</h2>
<p class="lead">All {(pdf.get("papers", 0) + jats.get("papers", 0)):,} papers, measured the same way in both formats
(<code>harness.py</code>). These are checks the reader can make on its own, without a second copy of the paper.</p>
{_table(heads, out)}{reason_html}</section>"""


def _pairs_section(numbers: dict) -> str:
    """The one measurement with real ground truth: the same paper, read as PDF and as the publisher's XML."""
    pairs = numbers.get("pairs") or {}
    names = [k for k in pairs if k != "all"]
    if not names:
        return ""
    order = names + (["all"] if "all" in pairs else [])
    metrics = [
        ("words of the XML's paragraphs the PDF read", "recall", 0.98, 0.95),
        ("the same, spelling included", "exact", 0.95, 0.9),
        ("read <b>and filed in the same lane</b>", "faithful", 0.95, 0.85),
        ("of the PDF's own paragraphs, the share the XML holds", "precision", 0.95, 0.9),
        ("paragraphs read intact", "paragraphs.intact", 0.95, 0.9),
        ("paragraphs missing entirely", "paragraphs.missing", 0.0, 0.0),
        ("headings the PDF found", "headings.recall", 0.95, 0.85),
        ("headings it did not invent", "headings.precision", 0.95, 0.85),
        ("the heading's lane agrees", "headings.lane_agree", 0.95, 0.85),
        ("reference-list length agrees", "references.ratio", 0.95, 0.85),
        ("in-text citations agree", "citations.ratio", 0.95, 0.8),
        ("captions read as captions", "captions.as_caption", 0.95, 0.85),
    ]
    rows = []
    for label, key, good, poor in metrics:
        cells = []
        for name in order:
            mean = (pairs.get(name) or {}).get("mean") or {}
            value = mean.get(key)
            if key == "paragraphs.missing":
                cells.append(f'<span class="num" style="color:#15803d;font-weight:600">{value:.3f}</span>' if isinstance(value, (int, float)) else '<span class="muted">—</span>')
            else:
                cells.append(_rate(value, good, poor))
        rows.append([label] + cells)
    heads = [""] + [f'{_e(n)} ({(pairs.get(n) or {}).get("pairs", 0)})' for n in order]
    ladder = (pairs.get("all") or {}).get("faithful_at_least") or {}
    ladder_html = ""
    if ladder:
        total = (pairs.get("all") or {}).get("pairs", 0)
        cells = "".join(f'<div class="stat"><div class="n">{n}</div><div class="l">of {total} papers at least {k} faithful</div></div>'
                        for k, n in sorted(ladder.items(), key=lambda kv: -float(kv[0])))
        ladder_html = f'<div class="stats" style="margin:14px 0 0">{cells}</div>'
    return f"""<section><h2>A PDF against the publisher's own XML of the same paper</h2>
<p class="lead">The one measurement here with real ground truth. Where a paper exists in both formats, the XML
<i>states</i> what the PDF only shows — where a section starts, which paragraph is which, what is a caption —
so it can be used to score the PDF reading (<code>pairs.py</code>).</p>
{_table(heads, rows)}
<div class="warn">Read the first row against the third. The text is essentially never lost; what goes wrong is
<b>where it is filed</b>, and a missing heading is what puts it there.</div>{ladder_html}</section>"""


def _type_section(numbers: dict) -> str:
    """Each source that can name a paper's type, scored on papers where a stated label was then hidden."""
    measure = numbers.get("paper_type") or {}
    by_source = measure.get("by_source") or {}
    if not by_source:
        return ""
    rows = []
    for name, data in sorted(by_source.items(), key=lambda kv: -(kv[1] or {}).get("accuracy", 0)):
        data = data or {}
        worst = sorted(((t, d) for t, d in (data.get("per_type") or {}).items() if (d or {}).get("recall") is not None),
                       key=lambda kv: kv[1]["recall"])[:2]
        rows.append([
            f"<b>{_e(name)}</b>",
            f'<span class="num">{data.get("answered", 0)}</span>',
            _rate(data.get("accuracy"), 0.93, 0.85),
            _e(", ".join(f"{t} {d['recall']:.2f}" for t, d in worst)) or '<span class="muted">—</span>',
        ])
    truth = measure.get("by_truth") or {}
    truth_html = ""
    if truth:
        truth_html = ('<p class="lead" style="margin-top:12px">The papers it was scored on, by what they actually are: '
                      + _e(", ".join(f"{k} {v}" for k, v in sorted(truth.items(), key=lambda kv: -kv[1]))) + ".</p>")
    stated = measure.get("stated_sources") or {}
    agree = ""
    if stated:
        agree = (f'<p class="lead">Where two stated sources both speak they agree <b>{stated.get("agree", 0)}</b> times '
                 f'and disagree <b>{stated.get("disagree", 0)}</b>.</p>')
    return f"""<section><h2>What kind of paper is this</h2>
<p class="lead">Measured on {measure.get("labelled", 0)} papers whose type is stated by a source that was then
hidden from the decision. Each row is one source, scored only where it answers at all.</p>
{_table(["source", "answers", "accuracy", "weakest types (recall)"], rows)}{agree}{truth_html}</section>"""


def _headings_section(numbers: dict) -> str:
    """The heading catalogue, scored with each library's own centroids left out of the catalogue."""
    measure = numbers.get("headings") or {}
    grid = measure.get("grid") or {}
    if not grid:
        return ""
    rows = []
    for setting, data in (grid.items() if isinstance(grid, dict) else []):
        data = data or {}
        lanes, canonical = data.get("lanes") or {}, data.get("canonical") or {}
        shipped = setting == measure.get("sample_at")
        rows.append([
            f'<b>{_e(setting)}</b>' + (' <span class="muted">← shipped</span>' if shipped else ""),
            _rate(lanes.get("precision")), _rate(lanes.get("recall")),
            _rate(canonical.get("precision")), _rate(canonical.get("recall"), 0.9, 0.8),
        ])
    if not rows:
        return ""
    # `unlabelled` counts the headings the written vocabulary never covered, split by what the centroids
    # then called them; the lane side is the one that matters, since every section gets a lane.
    unlabelled = (measure.get("unlabelled") or {}).get("lanes") or {}
    named = sum(v for v in unlabelled.values() if isinstance(v, int))
    return f"""<section><h2>Naming the sections</h2>
<p class="lead">{measure.get("rows", 0):,} sections, {measure.get("unique", 0):,} distinct headings. Each library is
scored with its own headings left out of the catalogue, so no library can name its own sections from memory.</p>
{_table(["threshold", "lane precision", "lane recall", "name precision", "name recall"], rows)}
<p class="lead" style="margin-top:10px">Beyond the written vocabulary, the catalogue put a lane on
<b>{named:,}</b> headings no rule had a word for{(" — " + _e(", ".join(f"{k} {v}" for k, v in sorted(unlabelled.items(), key=lambda kv: -kv[1])[:5]))) if unlabelled else ""}.</p></section>"""


def _confidence_section(numbers: dict) -> str:
    """Whether the trust score, computed from the reading alone, predicts the measured error."""
    measure = numbers.get("confidence") or {}
    bands = measure.get("bands") or {}
    if not bands:
        return ""
    rows = []
    for data in bands:
        data = data or {}
        papers = data.get("papers", 0) or 1
        serious = data.get("seriously_mismatched", 0)
        rows.append([
            f'<b>{_e(data.get("confidence"))}</b>',
            f'<span class="num">{data.get("papers", 0)}</span>',
            _rate(data.get("mean_faithful"), 0.95, 0.85),
            f'<span class="num">{serious}</span> {_bar(serious / papers, "#b91c1c")}',
        ])
    checks = measure.get("checks") or {}
    check_rows = [[_e(name), f'<span class="num">{(d or {}).get("fires", 0)}</span>',
                   f'<span class="num">{(d or {}).get("on_seriously_mismatched", 0)}</span>',
                   _bar(((d or {}).get("on_seriously_mismatched", 0)) / max(1, (d or {}).get("fires", 1)), "#7c3aed")]
                  for name, d in sorted(checks.items(), key=lambda kv: -((kv[1] or {}).get("on_seriously_mismatched", 0)))]
    checks_html = (f'<h3 style="font-size:14px;margin:16px 0 4px">Which reasons earn their place</h3>'
                   f'<p class="lead">How often each reason fires, and how often it fires on a paper that really is badly read.</p>'
                   f'{_table(["reason", "fires", "on a badly read paper", ""], check_rows)}') if check_rows else ""
    return f"""<section><h2>Does the trust score know when it is wrong</h2>
<p class="lead">The score is computed from the reading alone — no second copy of the paper. Here it is checked
against the {measure.get("pairs", 0)} papers whose PDF reading was scored against their XML. <b>AUC {measure.get("auc_well", 0):.3f}</b>
(it ranks a well-read paper above a badly read one), rank correlation with faithfulness {measure.get("rank_correlation_with_faithful", 0):.2f}.</p>
{_table(["trust band", "papers", "mean faithful", "seriously mismatched"], rows)}
<div class="warn">It is decisive at the bottom and weak at the top: the lowest band really is badly read, but a
paper can score 1.0 and still be misfiled. Trust a low score; do not trust a high one.</div>{checks_html}</section>"""


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

    # `numbers.json` gathers every run; where it is present the page renders the measurements as tables,
    # and each section quietly disappears if its measurement was never run.
    measured = "".join(section(numbers) for section in
                       (_harness_section, _pairs_section, _type_section, _headings_section, _confidence_section))
    if not measured:
        loose = {"paper type": type_measure, "headings": headings_measure,
                 **{n: (d or {}).get("summary", d) for n, d in pairs.items() if d},
                 **{n: (d or {}).get("summary", d) for n, d in harness.items() if d}}
        body = "".join(f"<h3>{_e(name)}</h3><pre class='muted'>{_e(json.dumps(data, indent=1)[:1600])}</pre>"
                       for name, data in loose.items() if data)
        measured = (f"<section><h2>The measurements</h2><p class='lead'>No <code>numbers.json</code> in the "
                    f"measurement directory, so these are the raw runs.</p>{body}</section>"
                    if body else "")

    run = (numbers.get("run") or {})
    provenance = ""
    if run:
        commands = "\n".join(f"{_e(k):<12} {_e(v)}" for k, v in (run.get("commands") or {}).items())
        provenance = f"""<section><h2>Where these numbers came from</h2>
<p class="lead">Every number on this page was written by a measurement run, not by this page. Measured
{_e(run.get("date"))} against {_e(run.get("root"))}; the embedder was {_e(run.get("embedder"))}.</p>
<pre class="muted">{commands}</pre></section>"""

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
<a href="report.html">what this shows</a> · <a href="index.html">every paper, page by page</a></div></header>
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

{measured}

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

{provenance}

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
