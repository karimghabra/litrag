"""Retrieval judged on questions with a known answer: does the passage that holds it come back?

A bench question names its library, the paper that answers it (by DOI), and a few words the
answering passage must contain. For each question and each pipeline:

- **passage@k** — the rank of the first returned passage from that paper holding every anchor
  word, or none within k;
- **context@k** (the tree only) — the same, counting what the passage is hydrated with: the
  paragraphs either side and the methods it was measured by. This is what chunkless retrieval
  buys: the answer can sit one paragraph away from the passage that matched.

The two pipelines are the tree (`retrieve.query` over `store.sqlite`) and the deprecated `lit`
CLI (`lit query --json` over `lit.sqlite`), run on the same questions, so a port shows a before
and an after. Questions `by: "proxy"` were written by an assistant from passages it had read;
the bench is meant to hold Karim's own, and says which is which.

    python -m litrag_parser.bench campaign/retrieval/bench.jsonl [--k 8] [--lit] [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any


def norm(s: str) -> str:
    """Case, spacing and the glyphs a PDF substitutes (°, –, µ) folded, so an anchor matches
    the words however the page set them."""
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = s.replace("�", "").replace("?", "")
    return re.sub(r"\s+", " ", s)


def holds(text: str, anchors: list[str]) -> bool:
    t = norm(text)
    return all(norm(a) in t for a in anchors)


def same_doi(a: str | None, b: str | None) -> bool:
    return bool(a and b and a.strip().lower() == b.strip().lower())


def judge_tree(answer: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    passage = context = None
    for h in hits:
        if not same_doi(h["paper"].get("doi"), answer["doi"]):
            continue
        if passage is None and holds(h["hit"]["text"], answer["contains"]):
            passage = h["rank"]
        around = " ".join([h["hit"]["text"], *(b["text"] for b in h.get("before") or []), *(a["text"] for a in h.get("after") or []),
                           *(m.get("text") or "" for m in h.get("methods") or [])])
        if context is None and holds(around, answer["contains"]):
            context = h["rank"]
    return {"passage": passage, "context": context, "papers": [h["paper"].get("doi") for h in hits[:3]]}


def judge_lit(answer: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    passage = None
    for i, h in enumerate(hits, 1):
        if same_doi(h.get("doi"), answer["doi"]) and holds(h.get("text") or "", answer["contains"]):
            passage = i
            break
    return {"passage": passage, "papers": [h.get("doi") for h in hits[:3]]}


def run_tree(q: dict[str, Any], k: int) -> dict[str, Any]:
    from . import retrieve
    from .library import library_root, open_library
    from .store import open_store

    lib = open_library(library_root(), q["lib"])
    if lib is None:
        return {"error": f"no library {q['lib']}"}
    conn = open_store(lib.store_path)
    try:
        out = retrieve.query(conn, q["question"], retrieve.OllamaEmbedder(), k=k)
    finally:
        conn.close()
    return {**judge_tree(q["answer"], out["hits"]), "down": bool((out.get("embedder") or {}).get("down"))}


def run_lit(q: dict[str, Any], k: int) -> dict[str, Any]:
    repo = Path(__file__).resolve().parents[2]
    cmd = ["node", "--experimental-transform-types", "--no-warnings", "src/cli.ts", "query", q["lib"], q["question"], "--json", "--limit", str(k)]
    p = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, encoding="utf-8", timeout=600, env={**os.environ})
    if p.returncode != 0:
        return {"error": (p.stderr or p.stdout).strip()[-300:]}
    try:
        hits = json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"error": p.stdout[-300:]}
    return judge_lit(q["answer"], hits)


def rate(rows: list[dict[str, Any]], field: str, k: int) -> dict[str, Any]:
    got = [r.get(field) for r in rows if "error" not in r]
    n = len(got)
    return {"n": n, "at1": sum(1 for g in got if g == 1), "at3": sum(1 for g in got if g and g <= 3), f"at{k}": sum(1 for g in got if g and g <= k),
            "mrr": round(sum(1 / g for g in got if g) / n, 3) if n else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m litrag_parser.bench", description=__doc__.split("\n\n")[0])
    ap.add_argument("bench")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--lit", action="store_true", help="also run the deprecated lit CLI over lit.sqlite, for the before number")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    qs = [json.loads(line) for line in Path(a.bench).read_text("utf-8").splitlines() if line.strip()]
    rows = []
    for q in qs:
        r = {"id": q["id"], "by": q.get("by"), "tree": run_tree(q, a.k)}
        if a.lit:
            r["lit"] = run_lit(q, a.k)
        rows.append(r)
        t = r["tree"]
        line = f"{q['id']:18} tree passage {t.get('passage') or '-':>2} context {t.get('context') or '-':>2}"
        if a.lit:
            line += f"   lit passage {r['lit'].get('passage') or '-':>2}" + (f" ({r['lit']['error'][:60]})" if "error" in r["lit"] else "")
        print(line, flush=True)
    summary = {"questions": len(qs), "by": sorted({q.get("by") for q in qs}), "k": a.k,
               "tree_passage": rate([r["tree"] for r in rows], "passage", a.k), "tree_context": rate([r["tree"] for r in rows], "context", a.k)}
    if a.lit:
        summary["lit_passage"] = rate([r["lit"] for r in rows], "passage", a.k)
    print(json.dumps(summary, indent=1))
    if a.json:
        Path(a.json).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
