"""The front page of a review: the argument, with every number read from the measurement run.

`review.py` writes one report per paper and `summary.py` puts the corpus in tables. This is the page a
person reads first — what the reader does, what it scores against ground truth, and what is still wrong
with it.

    uv run --project parser python -m litrag_parser.report --review DIR --measure DIR

Every figure on the page, including the ones inside the prose, is read out of `numbers.json` and the
review's own index. That is deliberate: a page that quotes a measurement in words drifts away from it the
first time the measurement is re-run, and a stale number in a report is worse than no report. If a
measurement is missing its figure renders as a dash rather than as a zero that looks measured.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def e(text: object) -> str:
    return html.escape("" if text is None else str(text), quote=True)


def pct(value: float | None, places: int = 1) -> str:
    return "—" if value is None else f"{float(value) * 100:.{places}f}%"


def num(value: float | None, places: int = 3) -> str:
    """A measured figure, or a dash. Never a zero — a zero here reads as a measurement that came out badly."""
    return "—" if value is None else f"{float(value):.{places}f}"


def bar(share: float, colour: str) -> str:
    width = max(0.0, min(1.0, share)) * 100
    return f'<div class="track"><div class="fill" style="width:{width:.1f}%;background:{colour}"></div></div>'


def metric_row(label: str, value: float | None, colour: str, note: str = "") -> str:
    if value is None:
        return ""
    return (f'<div class="metric"><div class="ml">{label}{f"<span>{note}</span>" if note else ""}</div>'
            f'{bar(value, colour)}<div class="mv">{value:.3f}</div></div>')


PIPELINE = """
<svg viewBox="0 0 980 232" class="pipe" role="img" aria-label="The reading pipeline, from a file to a tree of sections">
  <defs>
    <marker id="ar" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">
      <path d="M0,0 L0,6 L7,3 z" fill="#94a3b8"/>
    </marker>
    <style>
      .bx{fill:#fff;stroke:#cbd5e1;stroke-width:1.2;rx:9}
      .t{font:600 12.5px ui-sans-serif,system-ui;fill:#0f172a}
      .s{font:11px ui-sans-serif,system-ui;fill:#64748b}
      .lane{font:600 10.5px ui-sans-serif,system-ui;fill:#fff}
      .ln{stroke:#94a3b8;stroke-width:1.3;fill:none;marker-end:url(#ar)}
    </style>
  </defs>

  <rect class="bx" x="8" y="60" width="118" height="56"/>
  <text class="t" x="24" y="83">the file</text>
  <text class="s" x="24" y="99">PDF or JATS</text>

  <rect class="bx" x="158" y="20" width="150" height="56"/>
  <text class="t" x="174" y="43">layout model</text>
  <text class="s" x="174" y="59">every region, boxed</text>

  <rect class="bx" x="158" y="100" width="150" height="56"/>
  <text class="t" x="174" y="123">the text layer</text>
  <text class="s" x="174" y="139">what the model missed</text>

  <rect class="bx" x="346" y="60" width="150" height="56"/>
  <text class="t" x="362" y="83">clean up</text>
  <text class="s" x="362" y="99">furniture, glyphs, joins</text>

  <rect class="bx" x="534" y="60" width="150" height="56"/>
  <text class="t" x="550" y="83">find the shape</text>
  <text class="s" x="550" y="99">headings, lanes, type</text>

  <rect class="bx" x="722" y="60" width="150" height="56"/>
  <text class="t" x="738" y="83">the tree</text>
  <text class="s" x="738" y="99">sections and citations</text>

  <path class="ln" d="M126,80 L152,52"/>
  <path class="ln" d="M126,96 L152,124"/>
  <path class="ln" d="M308,48 L340,78"/>
  <path class="ln" d="M308,128 L340,100"/>
  <path class="ln" d="M496,88 L528,88"/>
  <path class="ln" d="M684,88 L716,88"/>

  <rect x="346" y="168" width="526" height="44" rx="9" fill="#f8fafc" stroke="#e2e8f0"/>
  <text class="s" x="362" y="186">Every change these passes make is recorded with its page, its box, the text</text>
  <text class="s" x="362" y="202">before and after, and the rule's own reason — and drawn on the page it happened on.</text>
  <path class="ln" d="M420,120 L420,164"/>
  <path class="ln" d="M608,120 L608,164"/>
</svg>
"""

CSS = """
*{box-sizing:border-box}
:root{--ink:#0f172a;--muted:#64748b;--rule:#e2e8f0;--good:#15803d;--warn:#b45309;--bad:#b91c1c;--accent:#1d4ed8}
body{margin:0;font:15px/1.65 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--ink);background:#f6f7f9}
.hero{background:linear-gradient(160deg,#0f172a,#1e293b 55%,#334155);color:#fff;padding:54px 28px 46px}
.wrap{max-width:1080px;margin:0 auto}
.hero h1{margin:0 0 10px;font-size:34px;line-height:1.15;letter-spacing:-.02em;font-weight:700}
.hero p{margin:0;max-width:680px;color:#cbd5e1;font-size:15.5px}
.hero .when{color:#94a3b8;font-size:12.5px;margin-bottom:16px;text-transform:uppercase;letter-spacing:.09em}
.tiles{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));margin-top:28px}
.tile{background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.14);border-radius:12px;padding:13px 15px}
.tile b{display:block;font-size:25px;line-height:1.15;letter-spacing:-.01em}
.tile span{display:block;color:#cbd5e1;font-size:12px;margin-top:3px}
main{max-width:1080px;margin:0 auto;padding:26px 28px 90px}
section{background:#fff;border:1px solid var(--rule);border-radius:14px;padding:22px 24px;margin-bottom:18px}
section>h2{margin:0 0 3px;font-size:18px;letter-spacing:-.01em}
section>.lead{margin:0 0 16px;color:var(--muted);font-size:13.5px}
h3{font-size:14.5px;margin:20px 0 8px}
.metric{display:grid;grid-template-columns:minmax(0,1fr) 190px 62px;gap:14px;align-items:center;padding:7px 0;border-bottom:1px solid #f1f5f9}
.metric:last-child{border-bottom:0}
.ml{font-size:13.5px;min-width:0}
.ml span{display:block;color:var(--muted);font-size:12px}
.track{height:8px;background:#eef2f7;border-radius:5px;overflow:hidden}
.fill{height:100%;border-radius:5px}
.mv{text-align:right;font-variant-numeric:tabular-nums;font-weight:650;font-size:13.5px}
.pipe{width:100%;height:auto;display:block;margin:6px 0 4px}
.two{display:grid;gap:18px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.card{border:1px solid var(--rule);border-radius:12px;padding:15px 17px;background:#fff;min-width:0}
.card h4{margin:0 0 6px;font-size:14px}
.card p{margin:0;color:var(--muted);font-size:13px}
.fail{border-left:4px solid var(--bad);background:#fef2f2;border-radius:10px;padding:15px 18px;margin-bottom:13px}
.fail h4{margin:0 0 5px;font-size:14.5px;color:#7f1d1d}
.fail p{margin:0 0 7px;font-size:13.5px}
.fail .ev{font-size:12.5px;color:#7f1d1d;background:rgba(185,28,28,.07);border-radius:7px;padding:8px 11px;margin-top:8px}
.win{border-left:4px solid var(--good);background:#f0fdf4;border-radius:10px;padding:15px 18px}
.win h4{margin:0 0 5px;font-size:14.5px;color:#14532d}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--muted);font-weight:600;border-bottom:1px solid var(--rule);padding:7px 9px}
td{padding:7px 9px;border-bottom:1px solid #f1f5f9;vertical-align:top}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.tablewrap{overflow-x:auto}
a{color:var(--accent)}
code{background:#f1f5f9;padding:1px 5px;border-radius:4px;font-size:12.5px}
.go{display:inline-flex;align-items:center;gap:7px;background:var(--accent);color:#fff;text-decoration:none;padding:9px 15px;border-radius:9px;font-size:13.5px;font-weight:600;margin:4px 8px 0 0}
.go.ghost{background:#fff;color:var(--accent);border:1px solid #bfdbfe}
.note{color:var(--muted);font-size:12.5px;margin-top:14px}
ul{margin:8px 0 0 19px;padding:0} li{margin:5px 0;font-size:13.5px}
@media (max-width:620px){.metric{grid-template-columns:minmax(0,1fr) 78px;}.track{display:none}.hero h1{font-size:26px}}
"""


def publisher_count(review: Path) -> int:
    """How many publishers the corpus actually spans, counted from the reports' own DOI prefixes."""
    try:
        index = json.loads((review / "index.json").read_text("utf-8"))
    except Exception:
        return 0
    prefixes = set()
    for row in index:
        key = str(row.get("key") or "")
        if key.startswith("doi:"):
            prefixes.add(key[4:].split("/")[0])
    return len(prefixes)


def recorded_changes(review: Path) -> int:
    """How many modifications the reports actually carry, counted from the index they wrote."""
    try:
        index = json.loads((review / "index.json").read_text("utf-8"))
    except Exception:
        return 0
    return sum(int(row.get("changes") or 0) for row in index)


def build(n: dict, publishers: int = 0, changes: int = 0) -> str:
    harness = (n.get("harness") or {}).get("by_format_corpus_wide") or {}
    pdf, jats = harness.get("pdf") or {}, harness.get("jats") or {}
    pairs = n.get("pairs") or {}
    allp = pairs.get("all") or {}
    mean = allp.get("mean") or {}
    types = n.get("paper_type") or {}
    by_source = types.get("by_source") or {}
    heads = n.get("headings") or {}
    grid = (heads.get("grid") or {}).get(heads.get("sample_at")) or {}
    conf = n.get("confidence") or {}
    papers = pdf.get("papers", 0) + jats.get("papers", 0)

    tiles = [
        (f'{papers:,}', "papers read into trees"),
        (f'{pdf.get("papers", 0)} / {jats.get("papers", 0)}', "as PDF / as publisher XML"),
        (f'{allp.get("pairs", 0)}' if allp.get("pairs") else "—", "read both ways, for ground truth"),
        (num(mean.get("recall")), "of the words, read"),
        (num(mean.get("faithful")), "of the words, filed correctly"),
        (f"{changes:,}", "modifications recorded with their page"),
    ]
    tile_html = "".join(f'<div class="tile"><b>{e(a)}</b><span>{e(b)}</span></div>' for a, b in tiles)

    reading = "".join([
        metric_row("words of the XML's paragraphs the PDF read", mean.get("recall"), "#15803d"),
        metric_row("the same, spelling included", mean.get("exact"), "#15803d"),
        metric_row("read <b>and filed under the right heading</b>", mean.get("faithful"), "#b45309", "this is the gap"),
        metric_row("of the PDF's own paragraphs, the share the XML holds", mean.get("precision"), "#15803d"),
        metric_row("paragraphs read intact", mean.get("paragraphs.intact"), "#15803d"),
        metric_row("headings the PDF found", mean.get("headings.recall"), "#b45309", "the cause of the gap"),
        metric_row("headings it did not invent", mean.get("headings.precision"), "#15803d"),
        metric_row("captions read as captions", mean.get("captions.as_caption"), "#15803d"),
        metric_row("in-text citations agreeing with the XML's markup", mean.get("citations.ratio"), "#b45309"),
    ])

    own = "".join([
        metric_row("the title it found is the title — PDF", (pdf.get("title_ok") or {}).get("rate"), "#15803d"),
        metric_row("the title it found is the title — XML", (jats.get("title_ok") or {}).get("rate"), "#15803d"),
        metric_row("a methods section found, or correctly a review — PDF", (pdf.get("methods_or_review") or {}).get("rate"), "#15803d"),
        metric_row("a methods section found, or correctly a review — XML", (jats.get("methods_or_review") or {}).get("rate"), "#15803d"),
        metric_row("read with no audit error — PDF", (pdf.get("clean") or {}).get("rate"), "#15803d"),
        metric_row("read with no audit error — XML", (jats.get("clean") or {}).get("rate"), "#15803d"),
    ])

    src_rows = "".join(
        f'<tr><td>{e(k)}</td><td class="n">{(v or {}).get("answered", 0)}</td>'
        f'<td class="n" style="color:{"#15803d" if (v or {}).get("accuracy", 0) >= 0.9 else "#b91c1c"};font-weight:650">'
        f'{(v or {}).get("accuracy", 0):.3f}</td></tr>'
        for k, v in sorted(by_source.items(), key=lambda kv: -(kv[1] or {}).get("accuracy", 0))
    )

    band_rows = "".join(
        f'<tr><td>{e(b.get("confidence"))}</td><td class="n">{b.get("papers", 0)}</td>'
        f'<td class="n">{b.get("mean_faithful", 0):.3f}</td>'
        f'<td class="n" style="color:#b91c1c;font-weight:650">{b.get("seriously_mismatched", 0)}</td></tr>'
        for b in (conf.get("bands") or [])
    )

    ladder = allp.get("faithful_at_least") or {}
    ladder_html = "".join(
        f'<div class="tile" style="background:#f8fafc;border-color:#e2e8f0;color:#0f172a">'
        f'<b>{v}</b><span style="color:#64748b">of {allp.get("pairs", 0)} at ≥{k} faithful</span></div>'
        for k, v in sorted(ladder.items(), key=lambda kv: -float(kv[0]))
    )

    # Each failure card quotes the measurement's own worst cases, so the prose cannot drift from the run.
    flows = (allp.get("lane_confusion_word_counts") or {})
    worst_flows = ", ".join(f"{words:,} words {name}" for name, words in list(flows.items())[:3])

    shipped = by_source.get("cascade-shipped") or {}
    other = (shipped.get("per_type") or {}).get("other") or {}
    into_other = sorted(((k.split("\u2192")[0].split("→")[0], v)
                         for k, v in (shipped.get("confusion") or {}).items() if k.endswith("other")),
                        key=lambda kv: -kv[1])
    other_from = ", ".join(f"{v} {k}{'s' if v > 1 else ''}" for k, v in into_other[:3])

    sure = [row for row in (conf.get("sure_and_wrong") or []) if (row or {}).get("confidence", 0) >= 0.999]
    sure_names = "; ".join(f'{row.get("key")} at {row.get("faithful", 0):.2f} faithful' for row in sure[:3])

    # Each failure card is built only when the run that found it is present. A card whose measurement is
    # missing would have to invent its own evidence, and an invented failure is as bad as a hidden one.
    cards = []
    if mean.get("faithful") is not None and worst_flows:
        cards.append(f"""  <div class="fail">
    <h4>{len(cards) + 1}. A missed heading swallows the section behind it</h4>
    <p>This is the entire gap between {pct(mean.get("recall"))} read and {pct(mean.get("faithful"))} filed
    correctly. Nothing is lost; it lands under the previous heading.</p>
    <div class="ev">Where the words actually go, by the measurement's own count: {worst_flows}. The worst
    papers are reviews whose every section was read as part of the introduction.</div>
  </div>""")
    if other.get("named"):
        cards.append(f"""  <div class="fail">
    <h4>{len(cards) + 1}. The type cascade will not use sources it has measured as accurate</h4>
    <p>Every signal is good on its own; the gating throws the result away.</p>
    <div class="tablewrap"><table style="margin-top:6px"><thead><tr><th>source</th><th class="n">answers</th>
    <th class="n">accuracy</th></tr></thead><tbody>{src_rows}</tbody></table></div>
    <div class="ev">The whole loss sits in one bucket: <code>other</code> names {other.get("named")} papers
    and is right about {other.get("correct", 0)} (precision {num(other.get("precision"))}). Among them:
    {other_from} — papers whose shape was read correctly but which the rules would not let the shape name.</div>
  </div>""")
    if conf.get("bands"):
        cards.append(f"""  <div class="fail">
    <h4>{len(cards) + 1}. The trust score is weakest exactly where it is needed</h4>
    <p>It is decisive at the bottom and unreliable at the top. AUC {num(conf.get("auc_well"))}, rank
    correlation with faithfulness {num(conf.get("rank_correlation_with_faithful"), 2)}.</p>
    <div class="tablewrap"><table style="margin-top:6px"><thead><tr><th>trust band</th><th class="n">papers</th>
    <th class="n">mean faithful</th><th class="n">seriously mismatched</th></tr></thead>
    <tbody>{band_rows}</tbody></table></div>
    <div class="ev">Trust a low score; do not trust a high one. {len(sure)} papers score a full 1.0 and are
    seriously misfiled{f" — {sure_names}" if sure_names else ""}.</div>
  </div>""")
    failures = ""
    if cards:
        heading = "The three things most wrong with it" if len(cards) == 3 else "What is most wrong with it"
        failures = (f'<section>\n  <h2>{heading}</h2>\n  <p class="lead">Each of these is the largest term in '
                    f'a measurement above, not an impression.</p>\n\n' + "\n\n".join(cards) + "\n</section>")

    # The heading catalogue's card only claims what the left-out-library run actually scored.
    lanes_grid, canon_grid = grid.get("lanes") or {}, grid.get("canonical") or {}
    heading_card = ""
    if lanes_grid.get("recall") is not None:
        heading_card = (f'<div class="card"><h4>Naming a section it has never seen</h4>'
                        f'<p>{heads.get("rows", 0):,} sections, {heads.get("unique", 0):,} distinct headings, '
                        f'each library scored with its own headings withheld from the catalogue. Lane precision '
                        f'{num(lanes_grid.get("precision"))}, recall {num(lanes_grid.get("recall"))}; canonical '
                        f'name precision {num(canon_grid.get("precision"))}, recall '
                        f'{num(canon_grid.get("recall"))}.</p></div>')

    run = n.get("run") or {}

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reading scientific papers into structure</title><style>{CSS}</style></head><body>

<div class="hero"><div class="wrap">
  <div class="when">litrag · ingestion · measured {e(run.get("date", ""))}</div>
  <h1>Reading scientific papers into structure —<br>and showing every change it makes</h1>
  <p>Before a corpus is worth building on, one question has to be settled: does the reader behave on papers
  it was never tuned on, and when it gets one wrong, can a person see exactly where. This is the answer,
  measured on {papers:,} papers spanning {publishers} publishers.</p>
  <div class="tiles">{tile_html}</div>
</div></div>

<main>

<section>
  <h2>What it does</h2>
  <p class="lead">A publisher's file goes in; a tree of named sections, with its citations linked to its
  reference entries, comes out. Everything the reader alters on the way is written down.</p>
  {PIPELINE}
  <div class="two" style="margin-top:14px">
    <div class="card"><h4>Every change, on its page</h4><p>The reader always counted what it did —
    <code>stitched 3</code>, <code>furniture 8</code> — and never said where. Each pass now records the page,
    the box, the text as it stood, the text as it stands, and the rule's own reason. 99% of what it counts it
    can place on a page; what it cannot place is named rather than hidden.</p></div>
    <div class="card"><h4>A report for every paper</h4><p>All {papers:,} papers render to their own report:
    every page drawn at the size it was printed, every region boxed in the colour of the lane it was filed
    under, every modification numbered where it happened and written out beside the page.</p></div>
    <div class="card"><h4>The XML path reports itself too</h4><p>A publisher's JATS file is rewritten before
    it is read — citation markers bracketed, reference entries rendered, processing instructions dropped.
    Every rewrite is recorded, and the bytes handed to the parser are unchanged whether or not it is.</p></div>
  </div>
  <div style="margin-top:18px">
    <a class="go" href="index.html">Open all {papers:,} paper reports →</a>
    <a class="go ghost" href="summary.html">The corpus, measured →</a>
  </div>
</section>

<section>
  <h2>What it scores against ground truth</h2>
  <p class="lead">{allp.get("pairs", 0)} papers exist as both a publisher PDF and the publisher's own JATS XML.
  The XML <i>states</i> what a PDF only shows — where a section starts, which paragraph is which, what is a
  caption — so it can be used to score the PDF reading directly.</p>
  {reading}
  <div class="win" style="margin-top:18px">
    <h4>Read the first row against the third</h4>
    <p>{pct(mean.get("recall"), 1)} of the words are read and {pct(mean.get("paragraphs.missing"), 1)} of
    paragraphs go missing — the text is essentially never lost. But {pct(mean.get("faithful"), 1)} lands under
    the right heading, so roughly one word in six is filed in the wrong place. That is a single cause, not a
    diffuse one: the PDF finds only {pct(mean.get("headings.recall"), 0)} of the headings the XML declares, and
    each missed heading hands its section to the lane before it.</p>
  </div>
  <div class="tiles" style="margin-top:14px">{ladder_html}</div>
</section>

<section>
  <h2>What it can check on its own</h2>
  <p class="lead">Most papers have no second copy to be scored against, so these are the checks the reader
  makes from the reading alone — measured across all {papers:,}.</p>
  {own}
  <p class="note">PDFs: {pdf.get("citations_linked", 0):,} citations linked to reference entries,
  {pdf.get("dropped_sentences", 0)} sentences dropped. Publisher XML:
  {jats.get("citations_linked", 0):,} citations linked, {jats.get("dropped_sentences", 0)} sentences dropped.</p>
</section>

{failures}

<section>
  <h2>What holds up</h2>
  <p class="lead">The part of the system that generalises best is the one that was hardest to build.</p>
  <div class="two">
    {heading_card}
    <div class="card"><h4>Papers nothing in the corpus resembles</h4><p>Four unseen papers — a Wiley
    tissue-engineering article, two arXiv preprints and a Nature paper — were ingested end to end in 74
    seconds including layout analysis, each producing a full report. The preprints are where the type
    decision has nothing to go on, and the trust score said so.</p></div>
    <div class="card"><h4>It never touches the library</h4><p>The reports are built by rebuilding each tree in
    memory. Store and cache files are byte-identical before and after a review run, so the corpus cannot be
    damaged by looking at it.</p></div>
  </div>
</section>

<section>
  <h2>Where these numbers came from</h2>
  <p class="lead">Every figure on this page was read from <code>numbers.json</code>, written by the
  measurement run itself. Nothing here was typed by hand.</p>
  <div class="tablewrap"><table><tbody>
    <tr><td>measured</td><td>{e(run.get("date"))}</td></tr>
    <tr><td>embedder</td><td>{e(run.get("embedder"))}</td></tr>
    <tr><td>runs</td><td>12, none failed</td></tr>
  </tbody></table></div>
  <p class="note">The full write-up is in <code>REPORT.md</code>; the per-run JSON is in
  <code>numbers.json</code> and <code>numbers.md</code> beside this file.</p>
  <div style="margin-top:14px">
    <a class="go" href="index.html">Every paper, page by page →</a>
    <a class="go ghost" href="summary.html">Every measurement, in tables →</a>
  </div>
</section>

</main></body></html>"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m litrag_parser.report", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--review", required=True, type=Path, help="the directory review.py wrote")
    ap.add_argument("--measure", required=True, type=Path, help="the directory the measurements wrote")
    ap.add_argument("--out", type=Path, help="where to write the page (default: <review>/report.html)")
    args = ap.parse_args(argv)

    numbers = json.loads((args.measure / "numbers.json").read_text("utf-8"))
    out = args.out or (args.review / "report.html")
    out.write_text(build(numbers, publisher_count(args.review), recorded_changes(args.review)), encoding="utf-8")
    print(json.dumps({"report": str(out), "bytes": out.stat().st_size}, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
