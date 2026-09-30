"""The reader judged in the unit retrieval hands back: a paragraph node.

A paper held twice — as a PDF and as its publisher's JATS — is its own witness. For every prose
paragraph the XML has, this asks how it arrives in the PDF's reading:

- **one chunk, right lane** — one paragraph node, under the lane the XML's reading gives it.
  This is "ingested perfectly", and the gate's number;
- **cut over chunks, right lane** — its text is there and in the right lane, over several nodes;
- **one chunk, wrong lane** / **cut over chunks, some lane wrong** — the text is there, filed
  under another lane;
- **out of the prose** — its words are in a caption, a heading, the front matter or a table;
- **missing** — fewer than half its words are held anywhere.

And on the PDF's side, the chunks the paper does not have (a running head, a sidebar read as
prose). Every tree is rebuilt from its saved Docling document with today's code — no Docling,
no model asked — so this measures the reader as it stands.

    python -m litrag_parser.chunks --pairs held-out-3-pdf:held-out-3-xml [--pairs A:B ...]
    python -m litrag_parser.chunks --corpus VAL           # the campaign corpus, one split
    python -m litrag_parser.chunks --corpus VAL --min 0.95 --json out.json   # a gate: exit 1 below

The libraries are found under the root (`LITRAG_ROOT`); nothing is written there.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from multiprocessing import Pool
from pathlib import Path
from typing import Any

ORDER = ["one chunk, right lane", "cut over chunks, right lane", "one chunk, wrong lane", "cut over chunks, some lane wrong", "out of the prose", "missing"]
_ready = False


#: A PDF chunk far from the one holding most of a paragraph, and holding under this share of the
#: paragraph's shingles, is an echo — a phrase the paper says twice ("the collagen threads were
#: crosslinked") — not a piece of the paragraph. Read by hand on held-out 3 (2026-09-23): a
#: paragraph whose main chunk held 214 of its 217 shingles was "cut" because a chunk twelve units
#: away shared three. A small piece *next to* the main one is a real cut, and still counts.
ECHO_SHARE, NEAR = 0.05, 2


def compare_pair(pdf_tree: Any, xml_tree: Any, echoes: bool = False) -> dict[str, Any]:
    """The chunk verdicts of one pair of trees. `echoes=True` is the first version of this
    measure, which counted every chunk sharing two shingles as a piece of the paragraph."""
    from .pairs import _held, _index, _land, units_of

    pu, xu = units_of(pdf_tree), units_of(xml_tree)
    p_index, x_index = _index(pu), _index(xu)
    verdict: Counter = Counter()
    holds: dict[int, list[int]] = {}  # a PDF chunk → the XML paragraphs inside it
    cut_into = 0
    xml_chunks = [(i, u) for i, u in enumerate(xu) if u.prose and u.shingles]
    for i, u in xml_chunks:
        landed = _land(u, p_index)
        held, _top_share = _held(u, landed, pu)
        if not landed or held < 0.5:
            verdict["missing"] += 1
            continue
        total = sum(landed.values())
        top = landed.most_common(1)[0][0]
        where = [j for j, c in landed.items() if c >= 2 or c / total >= 0.2 or j == top]
        if not echoes:
            where = [j for j in where if j == top or abs(j - top) <= NEAR or landed[j] / total >= ECHO_SHARE]
        prose_where = [j for j in where if pu[j].prose]
        holds.setdefault(top, []).append(i)
        if not prose_where:
            verdict["out of the prose"] += 1
            continue
        same = [j for j in prose_where if pu[j].role == u.role]
        if len(prose_where) == 1:
            verdict["one chunk, right lane" if same else "one chunk, wrong lane"] += 1
        else:
            verdict["cut over chunks, right lane" if len(same) == len(prose_where) else "cut over chunks, some lane wrong"] += 1
            cut_into += len(prose_where)
    merged_chunks = sum(1 for j, inside in holds.items() if len(inside) >= 2 and pu[j].prose)
    merged_paras = sum(len(inside) for j, inside in holds.items() if len(inside) >= 2 and pu[j].prose)
    pdf_chunks = [v for v in pu if v.prose and v.shingles]
    junk = part = 0
    for v in pdf_chunks:
        held, _ = _held(v, _land(v, x_index), xu)
        if held < 0.3:
            junk += 1
        elif held < 0.8:
            part += 1
    return {"xml_chunks": len(xml_chunks), "pdf_chunks": len(pdf_chunks), "verdict": dict(verdict), "merged_chunks": merged_chunks,
            "merged_paragraphs": merged_paras, "junk_chunks": junk, "part_chunks": part, "cut_into": cut_into}


def _one(job: tuple[str, str, str, dict, dict]) -> dict[str, Any]:
    global _ready
    from . import lanes
    from .harness import read_paper
    from .library import library_root

    root = library_root()
    if not _ready:
        lanes.configure_from_env(root)
        _ready = True
    label, pdf_lib, xml_lib, p_row, x_row, echoes = job
    try:
        pdf, kind, _ = read_paper(root / pdf_lib, p_row)
        xml, _, _ = read_paper(root / xml_lib, x_row)
    except Exception as e:  # noqa: BLE001 — a paper the reader cannot rebuild is counted, not dropped
        return {"key": p_row["key"], "set": label, "error": f"{type(e).__name__}: {e}"}
    return {"key": p_row["key"], "set": label, "type": kind.get("type"), **compare_pair(pdf, xml, echoes=echoes)}


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in results if "verdict" in r]
    v: Counter = Counter()
    for r in ok:
        v.update(r["verdict"])
    n = sum(v.values())
    right = v.get("one chunk, right lane", 0)
    lane_ok = right + v.get("cut over chunks, right lane", 0)
    largest = max((r["xml_chunks"] for r in ok), default=0)
    return {
        "papers": len(ok), "errors": len(results) - len(ok), "xml_chunks": n, "pdf_chunks": sum(r["pdf_chunks"] for r in ok),
        "verdict": {k: v.get(k, 0) for k in ORDER},
        "one_chunk_right_lane": round(right / n, 5) if n else None,
        "right_lane_at_all": round(lane_ok / n, 5) if n else None,
        "merged_paragraphs": sum(r["merged_paragraphs"] for r in ok), "merged_chunks": sum(r["merged_chunks"] for r in ok),
        "junk_chunks": sum(r["junk_chunks"] for r in ok),
        "largest_paper_share": round(largest / n, 4) if n else None,
    }


def show(label: str, s: dict[str, Any]) -> None:
    if not s["xml_chunks"]:
        print(f"\n{label}: nothing to compare")
        return
    n = s["xml_chunks"]
    print(f"\n{label}: {s['papers']} papers · {n:,} chunks in the XML · {s['pdf_chunks']:,} in the PDF reading" + (f" · {s['errors']} not rebuilt" if s["errors"] else ""))
    for k in ORDER:
        c = s["verdict"][k]
        if c:
            print(f"    {c:6,}  {c / n:6.1%}  {k}")
    print(f"    ---- {s['one_chunk_right_lane']:.1%} arrive as one chunk in the right lane; {s['right_lane_at_all']:.1%} in the right lane at all")
    print(f"    merged: {s['merged_paragraphs']:,} paragraphs into {s['merged_chunks']:,} chunks · {s['junk_chunks']:,} chunks carry text the paper has not · largest paper {s['largest_paper_share']:.1%}")


def jobs_for(pairs: list[str], corpus: str | None, echoes: bool = False) -> list[tuple[str, str, str, dict, dict, bool]]:
    from .library import library_root
    from .pairs import pairs_of

    root = library_root()
    jobs: list[tuple[str, str, str, dict, dict, bool]] = []
    for spec in pairs:
        pdf_lib, _, xml_lib = spec.partition(":")
        jobs += [(spec, pdf_lib, xml_lib, p, x, echoes) for p, x in pairs_of(root / pdf_lib, root / xml_lib)]
    if corpus:
        from .campaign import NEW, manifest, pairs_by_identity

        want = {str(r["doi"]).lower() for r in manifest()["papers"] if r["split"] == corpus}
        pdf_lib, xml_lib = NEW
        for p, x in pairs_by_identity(root / pdf_lib, root / xml_lib):
            if x["key"].lower().removeprefix("doi:") in want or p["key"].lower().removeprefix("doi:") in want:
                jobs.append((corpus, pdf_lib, xml_lib, p, x, echoes))
    return jobs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m litrag_parser.chunks", description=__doc__.split("\n\n")[0])
    ap.add_argument("--pairs", action="append", default=[], help="PDF-LIB:XML-LIB under the root; repeatable")
    ap.add_argument("--corpus", help="a split of the campaign corpus (corpus-pdf / corpus-xml): DEV, VAL, …")
    ap.add_argument("--min", type=float, help="exit 1 when 'one chunk, right lane' over everything is below this")
    ap.add_argument("--json", help="write every paper's verdicts and the summaries here")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--echoes", action="store_true", help="the first version of the measure: a phrase said twice elsewhere counts as a cut")
    a = ap.parse_args(argv)
    jobs = jobs_for(a.pairs, a.corpus, echoes=a.echoes)
    if not jobs:
        print("no pairs to compare", file=sys.stderr)
        return 2
    with Pool(max(1, min(a.workers, len(jobs)))) as pool:
        res = pool.map(_one, jobs, chunksize=1)
    labels = list(dict.fromkeys(j[0] for j in jobs))
    report = {"sets": {lab: summarize([r for r in res if r["set"] == lab]) for lab in labels}, "all": summarize(res)}
    for lab in labels:
        show(lab, report["sets"][lab])
    if len(labels) > 1:
        show("all of them", report["all"])
    if a.json:
        Path(a.json).write_text(json.dumps({**report, "papers": res}, indent=1, ensure_ascii=False), "utf-8")
    if a.min is not None:
        got = report["all"]["one_chunk_right_lane"] or 0.0
        print(f"\ngate: {got:.2%} {'>=' if got >= a.min else '<'} {a.min:.0%}")
        return 0 if got >= a.min else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
