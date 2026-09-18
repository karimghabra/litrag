"""Gather every measurement run in a directory into the one file the pages read.

`harness`, `paper_type`, `headings`, `pairs` and `confidence` each write their own JSON, per library or
per pairing. `summary.py` and `report.py` want the corpus: all the PDFs measured the same way against all
the XML, the three pairings pooled into one, each source of a type decision beside the others. This does
that pooling, once, and writes it down — so the pages read a file instead of computing over the runs, and
the file can be checked against the runs it came from.

    uv run --project parser python -m litrag_parser.numbers --measure DIR [--root NAME] [--embedder TEXT]

Nothing is invented here. Every figure is a sum, a share or a copy of something a run already wrote; where
a run is missing its section is missing, not zero.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

COMMANDS = {
    "harness": "uv run --project parser python -m litrag_parser.harness --lib <root>/<LIB> --json harness-<LIB>.json",
    "paper_type": "uv run --project parser python -m litrag_parser.paper_type --measure --lib ... --json type.json",
    "headings": "uv run --project parser python -m litrag_parser.headings --measure --lib ... --json headings.json",
    "pairs": "uv run --project parser python -m litrag_parser.pairs --pdf-lib <root>/<PDF> --xml-lib <root>/<XML> --json pairs-<name>.json",
    "confidence": "uv run --project parser python -m litrag_parser.confidence --calibrate pairs-*.json --json confidence.json",
}


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return None


def _share(n: int, of: int) -> dict[str, Any]:
    return {"n": n, "of": of, "rate": round(n / of, 4) if of else None}


def harness_corpus(measure: Path) -> dict[str, Any]:
    """Every paper the harness read, pooled by format — the PDF path beside the XML path."""
    by_library: dict[str, Any] = {}
    papers: list[dict[str, Any]] = []
    for path in sorted(measure.glob("harness-*.json")):
        run = _load(path) or {}
        lib = path.stem[len("harness-"):]
        by_library[lib] = run.get("summary") or {}
        for paper in run.get("papers") or []:
            papers.append({**paper, "library": paper.get("library") or lib})
    if not papers:
        return {}

    wide: dict[str, Any] = {}
    for fmt in ("pdf", "jats"):
        side = [p for p in papers if p.get("format") == fmt]
        if not side:
            continue
        scored = [p for p in side if isinstance(p.get("confidence"), (int, float))]
        reasons: Counter[str] = Counter()
        for paper in side:
            for reason in paper.get("confidence_reasons") or []:
                reasons[str(reason)[:60]] += 1
        wide[fmt] = {
            "papers": len(side),
            "title_ok": _share(sum(1 for p in side if p.get("title_ok")), len(side)),
            "has_methods": _share(sum(1 for p in side if p.get("has_methods")), len(side)),
            "methods_or_review": _share(sum(1 for p in side if p.get("has_methods") or p.get("review_like")), len(side)),
            "clean": _share(sum(1 for p in side if not p.get("errors")), len(side)),
            "errors": sum(int(p.get("errors") or 0) for p in side),
            "citations_linked": sum(int(p.get("citations") or 0) for p in side),
            "findings": sum(int((p.get("edges") or {}).get("findings") or 0) for p in side),
            "findings_linked_to_method": sum(int(p.get("findings_linked") or 0) for p in side),
            "dropped_sentences": sum(int(p.get("dropped_lines") or 0) for p in side),
            "confidence_bands": {
                "papers": len(scored),
                "ge_0.9": sum(1 for p in scored if p["confidence"] >= 0.9),
                "0.5_to_0.9": sum(1 for p in scored if 0.5 <= p["confidence"] < 0.9),
                "lt_0.5": sum(1 for p in scored if p["confidence"] < 0.5),
            },
            "top_confidence_reasons": dict(reasons.most_common(6)),
        }
    for fmt, side in wide.items():
        found = side["findings"]
        side["findings_link_rate"] = round(side["findings_linked_to_method"] / found, 4) if found else None
    return {"by_library": by_library, "by_format_corpus_wide": wide, "corpus_papers": len(papers)}


def pairs_pooled(measure: Path) -> dict[str, Any]:
    """Each pairing as its run wrote it, plus the three pooled into one `all`.

    A pairing's mean is a mean over papers, so pooling means re-averaging over every paper of every
    pairing rather than averaging the averages — 135 papers and 31 papers do not weigh the same.
    """
    out: dict[str, Any] = {}
    everything: list[dict[str, Any]] = []
    for path in sorted(measure.glob("pairs-*.json")):
        run = _load(path) or {}
        name = path.stem[len("pairs-"):]
        summary, records = run.get("summary") or {}, run.get("pairs") or []
        if not records:
            continue
        everything.extend(records)
        out[name] = {**summary, **_over(records)}
        out[name]["pdf_lib"] = records[0].get("pdf_library")
        out[name]["xml_lib"] = records[0].get("xml_library")
    if len(out) > 1 and everything:
        out["all"] = {
            "pairs": len(everything),
            "titles_same": sum(1 for r in everything if r.get("title_same")),
            "mean": _mean_of(everything),
            "faithful_at_least": {
                str(cut): sum(1 for r in everything if (r.get("faithful") or 0) >= cut)
                for cut in (0.95, 0.9, 0.8, 0.7, 0.5)
            },
            **_over(everything),
        }
    return out


_METRICS = ("recall", "exact", "faithful", "precision")
_NESTED = {
    "paragraphs": ("intact", "split", "merged", "missing"),
    "headings": ("recall", "precision", "level_agree", "lane_agree"),
    "references": ("ratio", "recall"),
    "citations": ("ratio",),
    "captions": ("as_caption", "as_prose"),
}


def _mean_of(records: list[dict[str, Any]]) -> dict[str, float]:
    """The plain mean over papers, the way `pairs.py` reports a single pairing."""
    mean: dict[str, float] = {}
    for key in _METRICS:
        values = [r[key] for r in records if isinstance(r.get(key), (int, float))]
        if values:
            mean[key] = round(sum(values) / len(values), 4)
    for group, keys in _NESTED.items():
        for key in keys:
            values = [r[group][key] for r in records
                      if isinstance(r.get(group), dict) and isinstance(r[group].get(key), (int, float))]
            if values:
                mean[f"{group}.{key}"] = round(sum(values) / len(values), 4)
    return mean


def _over(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Word-weighted figures: a long paper's misreading costs a corpus more than a short one's."""
    words = sum(int(r.get("xml_words") or 0) for r in records)
    if not words:
        return {}
    landing: dict[str, float] = defaultdict(float)
    for record in records:
        weight = int(record.get("xml_words") or 0)
        for where, share in (record.get("landed") or {}).items():
            landing[where] += share * weight
    return {
        "xml_words": words,
        "word_weighted_landing": {k: round(v / words, 4) for k, v in
                                  sorted(landing.items(), key=lambda kv: -kv[1])},
        "word_weighted_landing_words": {k: int(round(v)) for k, v in
                                        sorted(landing.items(), key=lambda kv: -kv[1])},
    }


def build(measure: Path, root: str = "<libroot>", embedder: str = "") -> dict[str, Any]:
    numbers: dict[str, Any] = {
        "run": {"date": date.today().isoformat(), "repo": "litrag", "root": root,
                "embedder": embedder or "nomic-embed-text via Ollama", "commands": COMMANDS},
    }
    harness = harness_corpus(measure)
    if harness:
        numbers["harness"] = harness
    for key, name in (("paper_type", "type.json"), ("headings", "headings.json"), ("confidence", "confidence.json")):
        loaded = _load(measure / name)
        if loaded:
            numbers[key] = loaded
    pairs = pairs_pooled(measure)
    if pairs:
        numbers["pairs"] = pairs
    return numbers


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m litrag_parser.numbers", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--measure", required=True, type=Path, help="the directory the measurement runs wrote")
    ap.add_argument("--root", default="<libroot>", help="the library root they were run against")
    ap.add_argument("--embedder", default="", help="what answered the meaning questions")
    ap.add_argument("--out", type=Path, help="where to write it (default: <measure>/numbers.json)")
    args = ap.parse_args(argv)

    numbers = build(args.measure, args.root, args.embedder)
    out = args.out or (args.measure / "numbers.json")
    out.write_text(json.dumps(numbers, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"numbers": str(out), "sections": [k for k in numbers if k != "run"]}, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
