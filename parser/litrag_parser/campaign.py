"""Score a split of the campaign corpus, and write the line that lets it be checked.

`evaluate.py` holds the measures; this is what runs them over a corpus and obeys the protocol
around them. Three things it does that a plain loop would not:

- **It knows which publishers the reader was built on.** T3 — does precision hold up on a
  publisher nobody has tested? — is the property that makes any other number mean something, and
  it cannot be computed without that list. `--corpus legacy` scores the old pair libraries, every
  one of whose publishers is fitted, and `--corpus new` scores the campaign's, none of whose are.
- **It redacts.** SEALED prints aggregates and nothing per paper, EXAM the same, RESERVE is not
  scored at all. Written down in `evaluate.redact` rather than left to the person running it.
- **It writes a ledger line every time.** A number that is not in `campaign/LEDGER.jsonl` does
  not go in a report, so writing one has to be cheaper than skipping it.

    python -m litrag_parser.campaign --split DEV
    python -m litrag_parser.campaign --corpus legacy --split FITTED
    python -m litrag_parser.campaign --split VAL --json out.json --no-ledger
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import lanes
from .evaluate import (
    Landing, accounting, bootstrap, by_lane, check_no_leak, confusion, coverage_of, landings,
    precision_and_coverage, precision_of, precision_on_named_of, redact,
)
from .harness import read_paper
from .library import library_root
from .pairs import pairs_of

#: the campaign's own libraries: one pair, every split in them, the manifest saying which is which
NEW = ("corpus-pdf", "corpus-xml")
#: the sets the reader was built and tuned on — every publisher in them is fitted by definition
LEGACY = [("looped-ligament-pairs", "looped-ligament"), ("held-out-pdf", "held-out-xml"),
          ("held-out-2-pdf", "held-out-2-xml"), ("held-out-3-pdf", "held-out-3-xml"),
          ("held-out-4-pdf", "held-out-4-xml")]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def manifest() -> dict[str, Any]:
    return json.loads((repo_root() / "campaign" / "corpus-manifest.json").read_text("utf-8"))


def _prefix_of(key: str, doi: str | None) -> str:
    """The publisher, from a DOI.

    A key that carries none — a paper filed by content hash because no DOI could be read from it
    — becomes its own cluster rather than joining one called `?`. Collapsing them would let the
    bootstrap treat a dozen unrelated papers as a single publisher, and narrow every interval
    that touched them."""
    import re

    m = re.search(r"\b(10\.\d{4,9})/", f"{doi or ''} {key or ''}".lower().replace("doi:", ""))
    return m.group(1) if m else f"?{key}"


def collect(pdf_lib: Path, xml_lib: Path, want: dict[str, dict] | None, fitted: set[str],
            split: str) -> tuple[list[Landing], list[dict], list[str]]:
    """Every pair of the library, as landings, plus a conservation record per PDF."""
    rows: list[Landing] = []
    per_paper: list[dict] = []
    skipped: list[str] = []
    wanted = set(want or ())
    for p_row, x_row in pairs_of(pdf_lib, xml_lib):
        key = p_row["key"]
        meta = (want or {}).get(key.replace("doi:", "").lower())
        if want is not None and meta is None:
            continue
        if meta is not None:
            wanted.discard(key.replace("doi:", "").lower())
        prefix = (meta or {}).get("prefix") or _prefix_of(key, None)
        pdf, kind, _ = read_paper(pdf_lib, p_row)
        xml, _, _ = read_paper(xml_lib, x_row)
        got = landings(pdf, xml, paper=key, prefix=prefix, split=split,
                       familiar=prefix in fitted, paper_type=(kind or {}).get("type", "?"))
        rows.extend(got)
        record: dict[str, Any] = {"key": key, "prefix": prefix, "paragraphs": len(got),
                                  "type": (kind or {}).get("type", "?"),
                                  **precision_and_coverage(got)}
        src = p_row.get("source")
        if src and Path(src).exists() and str(src).lower().endswith(".pdf"):
            record["accounting"] = accounting(pdf, Path(src))
        per_paper.append(record)
    # Say what is missing rather than quietly scoring a smaller split: a corpus line that shrinks
    # by one with nothing said is the failure this campaign fixed in the harness, and it would be
    # worse here — a split scored on the papers that happened to ingest is not that split.
    skipped = sorted(wanted)
    return rows, per_paper, skipped


def conservation(per_paper: list[dict]) -> dict[str, Any]:
    """T1 over the corpus: the share of text-layer words accounted for, and per paper."""
    have = [p["accounting"] for p in per_paper if p.get("accounting", {}).get("layer_words")]
    if not have:
        return {"papers": 0}
    total = sum(a["layer_words"] for a in have)
    held = sum(a["in_a_node"] for a in have)
    said = sum(a["in_a_dropped_record"] for a in have)
    per = sorted(a["accounted"] for a in have)
    return {
        "papers": len(have), "layer_words": total,
        "accounted_micro": round((held + said) / total, 5),
        "in_a_node_micro": round(held / total, 5),
        "in_a_dropped_record_micro": round(said / total, 5),
        "papers_at_or_above_0.999": sum(1 for x in per if x >= 0.999),
        "papers_at_or_above_0.99": sum(1 for x in per if x >= 0.99),
        "worst_paper": per[0], "median_paper": per[len(per) // 2],
    }


def commit() -> str:
    try:
        root = str(repo_root())
        head = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", root, "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
        return head + ("+dirty" if dirty else "")
    except Exception:  # noqa: BLE001
        return "?"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m litrag_parser.campaign", description=__doc__.split("\n\n")[0])
    ap.add_argument("--split", default="DEV", help="DEV | VAL | SEALED | EXAM | FITTED")
    ap.add_argument("--corpus", default="new", choices=["new", "legacy"])
    ap.add_argument("--json", help="save the full record here (outside the repository)")
    ap.add_argument("--no-ledger", action="store_true", help="do not append to campaign/LEDGER.jsonl")
    ap.add_argument("--draws", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)

    root = library_root()
    lanes.configure_from_env(root)
    man = manifest()
    fitted = set(man["fitted_prefixes"])

    leaks = check_no_leak(man)
    if leaks:
        print("SPLIT LEAK — refusing to score:", file=sys.stderr)
        for line in leaks:
            print(f"  {line}", file=sys.stderr)
        return 3

    if a.corpus == "legacy":
        want, split = None, a.split
        pairs = [(root / p, root / x) for p, x in LEGACY if (root / p).exists() and (root / x).exists()]
    else:
        want = {r["doi"]: r for r in man["papers"] if r["split"] == a.split}
        if not want:
            print(f"no papers in {a.split}", file=sys.stderr)
            return 1
        split = a.split
        pairs = [(root / NEW[0], root / NEW[1])]

    rows: list[Landing] = []
    per_paper: list[dict] = []
    missing: list[str] = []
    for pdf_lib, xml_lib in pairs:
        got, papers, skipped = collect(pdf_lib, xml_lib, want, fitted, split)
        rows.extend(got)
        per_paper.extend(papers)
        missing = skipped
    if not rows:
        print("no pairs found — are both libraries ingested?", file=sys.stderr)
        return 1

    overall = precision_and_coverage(rows)
    report: dict[str, Any] = {
        "split": split, "corpus": a.corpus, "commit": commit(),
        "manifest_sha256": hashlib.sha256((repo_root() / "campaign" / "corpus-manifest.json").read_bytes()).hexdigest()[:16],
        "flags": {k: v for k, v in sorted(os.environ.items()) if k.startswith("LITRAG_")},
        "overall": overall,
        "precision_ci": bootstrap(rows, precision_of, draws=a.draws, seed=a.seed),
        "precision_on_named_ci": bootstrap(rows, precision_on_named_of, draws=a.draws, seed=a.seed),
        "coverage_ci": bootstrap(rows, coverage_of, draws=a.draws, seed=a.seed),
        "by_lane": by_lane(rows),
        "by_type": {t: precision_and_coverage([r for r in rows if r.paper_type == t])
                    for t in sorted({r.paper_type for r in rows})},
        "familiar": precision_and_coverage([r for r in rows if r.familiar]),
        "novel": precision_and_coverage([r for r in rows if not r.familiar]),
        "confusion": confusion(rows),
        "conservation": conservation(per_paper),
        "in_the_manifest_but_not_read": missing,
        "papers_detail": sorted(per_paper, key=lambda p: (p["precision"] is None, p["precision"] or 0)),
    }
    shown = redact(split, report)

    o = shown["overall"]
    print(f"{split} ({a.corpus}): {o['papers']} papers · {o['publishers']} publishers · {o['paragraphs']} witness paragraphs")
    if missing:
        print(f"  !! {len(missing)} papers are in the manifest and not in the libraries: "
              f"{', '.join(missing[:6])}{' …' if len(missing) > 6 else ''}")
    print(f"  asserted {o['asserted']} ({o['coverage']:.1%} coverage) · right {o['correct']} · wrong {o['wrong']}")
    ci = shown["precision_ci"]
    print(f"  precision  micro {o['precision']}  macro {o['precision_macro']}  "
          f"95% CI [{ci['lo']}, {ci['hi']}] over {ci['publishers']} publishers")
    ni = shown["precision_on_named_ci"]
    print(f"  precision where the witness itself names a lane: {o['precision_on_named']} "
          f"95% CI [{ni['lo']}, {ni['hi']}] · coverage {o['coverage_on_named']} "
          f"· {o['witness_other']} of {o['paragraphs']} witness paragraphs are `other`, "
          f"{o['wrong_where_witness_said_other']} of them asserted")
    cc = shown["coverage_ci"]
    print(f"  coverage   {o['coverage']}  95% CI [{cc['lo']}, {cc['hi']}]")
    print("  not asserted, by where it went instead: "
          + (", ".join(f"{k} {n}" for k, n in o["not_asserted"].items()) or "nothing"))
    c = shown["conservation"]
    if c.get("papers"):
        print(f"  conservation (T1): {c['accounted_micro']} of {c['layer_words']:,} text-layer words "
              f"over {c['papers']} PDFs · {c['papers_at_or_above_0.999']} papers at 0.999+ · worst {c['worst_paper']}")
    print(f"  familiar publishers {shown['familiar']['precision']} · novel {shown['novel']['precision']}")
    print("  lanes: " + " · ".join(
        f"{k} p={v['precision']} c={v['coverage']:.2f} n={v['paragraphs']}"
        for k, v in shown["by_lane"].items()))
    if shown["confusion"]:
        print("  wrong assertions: " + ", ".join(f"{k} {n}" for k, n in shown["confusion"][:6]))
    if "papers_detail" in shown:
        print("  hardest papers:")
        for p in shown["papers_detail"][:8]:
            print(f"    {p['key'][:42]:44s} {p['type']:10s} p={p['precision']} c={p['coverage']} n={p['paragraphs']}")
    else:
        print(f"  [{shown['redacted']}]")

    if a.json:
        # `shown`, never `report`: a sealed split that prints aggregates and then writes every
        # paper's number to a file beside it has not been sealed at all
        Path(a.json).write_text(json.dumps(shown, indent=1, default=str), encoding="utf-8")
        print(f"\nsaved {a.json}")

    if not a.no_ledger:
        line = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "phase": "2+", "name": f"score-{split}", "split": split, "commit": report["commit"],
            "libs": [str(p.name) for pair in pairs for p in pair],
            "manifest": report["manifest_sha256"], "flags": report["flags"],
            "seed": a.seed, "command": " ".join(sys.argv),
            "metrics": {k: shown[k] for k in ("overall", "precision_ci", "precision_on_named_ci",
                                              "coverage_ci", "familiar", "novel", "conservation")
                        if k in shown},
            "note": "",
        }
        ledger = repo_root() / "campaign" / "LEDGER.jsonl"
        with ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, sort_keys=True, default=str) + "\n")
        n = sum(1 for _ in ledger.open(encoding="utf-8"))
        print(f"ledger line {n} written")
        if split in ("SEALED", "EXAM"):
            scored = sum(1 for ln in ledger.open(encoding="utf-8") if json.loads(ln).get("split") == split)
            print(f"  !! {split} has now been scored {scored} time(s)"
                  + ("  — the budget is six" if split == "SEALED" else "  — the budget is one"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
