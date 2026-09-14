"""What kind of paper this is — research, review, letter, editorial, case report, protocol,
data descriptor, correction — decided before its headings are read with that in mind.

A paper says what it is more often than not, and the reader takes the statement before it
infers anything. A cascade, each step only where the one before is silent, every verdict
kept with its source:

1. **The file says.** A JATS file's `article-type` on its root element.
2. **The record says.** Europe PMC's publication types for the DOI or PMID, fetched once
   at ingest (unless `offline`) and stored with the paper (`papers.pub_types`).
3. **The page says.** The label a publisher prints on the first page ("ORIGINAL
   RESEARCH", "REVIEW", "Letter to the Editor"), which the front-matter kind already files
   as a `notice`, read by the oracle against example labels per type (`type-label`).
4. **The paper's shape says.** Its profile — title, the first sentences of the abstract,
   its top-level headings in order — against example profiles per type (`profile`).
5. `other`.

Steps 1 and 2 label most papers for free, which is what lets step 4 be measured with the
label hidden before it decides anything: `python -m litrag_parser.paper_type --measure`.
The type is a column (`papers.type`, with `type_source`), and it decides what the audit
expects — no methods in a review is nothing, no methods in a research article is a warning.

    uv run --project parser python -m litrag_parser.paper_type --fetch --lib …     Europe PMC's types for every paper with a DOI or PMID
    uv run --project parser python -m litrag_parser.paper_type --measure --lib …   the printed label and the profile against the file's and the record's word
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from .tree import Tree

TYPES = ("research", "review", "letter", "editorial", "case-report", "protocol", "data", "correction", "other")

#: JATS `article-type` values, as publishers use them
JATS_TYPES = {
    "research-article": "research", "brief-report": "research", "rapid-communication": "research", "short-report": "research", "clinical-trial": "research",
    "review-article": "review", "systematic-review": "review", "meta-analysis": "review", "mini-review": "review",
    "letter": "letter", "reply": "letter", "correspondence": "letter",
    "editorial": "editorial", "article-commentary": "editorial", "discussion": "editorial", "book-review": "editorial", "news": "editorial", "product-review": "editorial", "perspective": "editorial", "opinion": "editorial", "commentary": "editorial", "viewpoint": "editorial",
    "case-report": "case-report", "case-study": "case-report", "case-series": "case-report",
    "protocol": "protocol", "study-protocol": "protocol", "methods-article": "protocol",
    "data-paper": "data", "data-descriptor": "data",
    "correction": "correction", "erratum": "correction", "retraction": "correction", "addendum": "correction", "expression-of-concern": "correction",
}

#: Europe PMC / MeSH publication types, most specific first: a record is ["review-article", "Review", "Journal Article"]
RECORD_TYPES = [
    ("correction", ("published erratum", "retraction of publication", "erratum", "retraction", "correction", "addendum")),
    ("protocol", ("clinical trial protocol", "study protocol", "protocol")),
    ("data", ("dataset", "data-paper", "data descriptor")),
    ("case-report", ("case reports", "case-report", "case report")),
    ("letter", ("letter", "comment", "correspondence")),
    ("editorial", ("editorial", "commentary", "news", "viewpoint", "perspective", "opinion", "interview", "biography", "historical article", "lecture", "address")),
    ("review", ("review", "systematic review", "meta-analysis", "review-article", "scoping review", "guideline", "practice guideline", "consensus development conference")),
    ("research", ("research-article", "journal article", "clinical trial", "randomized controlled trial", "observational study", "comparative study", "evaluation study", "validation study", "multicenter study", "research support")),
]

_ARTICLE_TYPE = re.compile(r"<article\b[^>]*\barticle-type\s*=\s*[\"']([^\"']+)[\"']", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def from_jats(xml: bytes | None) -> tuple[str, str] | None:
    """The root element's `article-type`, mapped; None when the file has none or names something unmapped."""
    if not xml:
        return None
    m = _ARTICLE_TYPE.search(xml[:20000].decode("utf-8", "replace"))
    if not m:
        return None
    raw = m.group(1).strip().lower()
    return (JATS_TYPES.get(raw, "other" if raw in ("abstract", "preprint", "other", "obituary", "in-brief") else None), raw) if (raw in JATS_TYPES or raw in ("abstract", "preprint", "other", "obituary", "in-brief")) else None


def from_record(pub_types: list[str] | str | None) -> tuple[str, str] | None:
    """Europe PMC's publication types, the most specific taking precedence; None when there are none."""
    if not pub_types:
        return None
    raw = [p.strip().lower() for p in (pub_types.split(";") if isinstance(pub_types, str) else pub_types) if p and p.strip()]
    if not raw:
        return None
    for kind, names in RECORD_TYPES:
        if any(r in names for r in raw):
            return kind, "; ".join(raw)
    return None


def lookup_types(doi: str | None = None, pmid: str | None = None, timeout: float = 6.0) -> list[str] | None:
    """Europe PMC's publication types for one paper, by DOI else PMID; None offline or unknown."""
    if not doi and not pmid:
        return None
    query = f'DOI:"{doi}"' if doi else f"EXT_ID:{pmid} AND SRC:MED"
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={urllib.parse.quote(query)}&format=json&resultType=lite&pageSize=1"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    hits = (data.get("resultList") or {}).get("result") or []
    if not hits:
        return None
    raw = hits[0].get("pubType") or ""
    return [p.strip() for p in raw.split(";") if p.strip()] or None


def profile_of(tree: Tree) -> str:
    """What the paper's shape says: its title, the first sentences of its abstract, and its
    top-level headings in order."""
    abstract = ""
    for n in tree.root.children:
        if n.type == "section" and n.role == "abstract":
            text = " ".join(c.text for c in n.children if c.type == "paragraph")
            abstract = " ".join(_SENTENCE.split(text)[:3])[:400]
            break
    heads = [n.heading for n in tree.root.children if n.type == "section" and n.heading and n.heading not in ("Front matter",)]
    return f"Title: {tree.title}. Abstract: {abstract or '(none)'} Sections: {'; '.join(heads[:14]) or '(no headings)'}"


def printed_labels(tree: Tree) -> list[str]:
    """The notices the front matter carries: what the publisher printed above the title."""
    out = []
    for n in tree.walk():
        if n.type == "meta" and n.label == "notice" and n.text and len(n.text.split()) <= 6:
            out.append(n.text.strip())
    return out


def decide(tree: Tree, *, jats_xml: bytes | None = None, pub_types: list[str] | str | None = None, oracle: Any = None) -> dict[str, str]:
    """`{type, source, detail}`: the file's word, else the record's, else the printed label by
    meaning, else the profile by meaning, else `other`."""
    got = from_jats(jats_xml)
    if got is not None and got[0] is not None:
        return {"type": got[0], "source": "jats", "detail": got[1]}
    got = from_record(pub_types)
    if got is not None:
        return {"type": got[0], "source": "record", "detail": got[1]}
    if oracle is not None:
        for label in printed_labels(tree):
            v = oracle.nearest("type-label", label)
            if v.sure and v.name in TYPES:
                return {"type": v.name, "source": "printed", "detail": label}
        v = oracle.nearest("profile", profile_of(tree))
        if v.sure and v.name in TYPES:
            return {"type": v.name, "source": "meaning", "detail": f"cosine {v.score} margin {v.margin}"}
    return {"type": "other", "source": "none", "detail": ""}


# -- the measurement and the fetch --------------------------------------------------------------------


def measure(libs: list[Path], oracle: Any) -> dict[str, Any]:
    """On every paper the file or the record labels, hide the label and ask the printed label
    and the profile: the confusion table per type, and how often each answered."""
    from .library import parsed_papers
    from .recover import recover_from_pdf
    from .tree import build_tree

    truth_n = 0
    answered: dict[str, Counter] = {"printed": Counter(), "meaning": Counter()}
    confusion: dict[str, Counter] = {}
    labelled_by: Counter[str] = Counter()
    for lib in libs:
        for row in parsed_papers(lib):
            xml = row["source"].read_bytes() if row["format"] == "jats" and row["source"] and row["source"].exists() else None
            got = from_jats(xml)
            stated = (got[0], "jats") if got and got[0] else None
            if stated is None:
                got = from_record(row.get("pub_types"))
                stated = (got[0], "record") if got else None
            if stated is None or stated[0] == "other":
                continue
            truth, src = stated
            truth_n += 1
            labelled_by[src] += 1
            doc = json.loads(row["raw"].read_text("utf-8"))
            recover_from_pdf(doc, row["source"] if row["format"] == "pdf" else None)
            tree = build_tree(doc, row["key"])
            guess = decide(tree, jats_xml=None, pub_types=None, oracle=oracle)
            if guess["source"] in ("printed", "meaning"):
                answered[guess["source"]][truth] += 1
                confusion.setdefault(guess["source"], Counter())[(truth, guess["type"])] += 1
    out: dict[str, Any] = {"labelled": truth_n, "labelled_by": dict(labelled_by), "by_source": {}}
    for src in ("printed", "meaning"):
        c = confusion.get(src, Counter())
        n = sum(c.values())
        right = sum(v for (t, g), v in c.items() if t == g)
        per_type = {}
        for t in TYPES:
            named = sum(v for (tt, g), v in c.items() if g == t)
            correct = c.get((t, t), 0)
            have = sum(v for (tt, g), v in c.items() if tt == t)
            per_type[t] = {"truth": have, "named": named, "correct": correct, "precision": round(correct / named, 3) if named else None}
        out["by_source"][src] = {"answered": n, "right": right, "accuracy": round(right / n, 3) if n else None, "per_type": per_type, "confusion": {f"{t}→{g}": v for (t, g), v in sorted(c.items()) if t != g}}
    return out


def fetch(libs: list[Path], sleep: float = 0.2) -> dict[str, int]:
    """Europe PMC's publication types for every paper with a DOI or PMID and none stored yet."""
    from .store import open_store, set_pub_types

    done = Counter()
    for lib in libs:
        conn = open_store(Path(lib) / "store.sqlite")
        rows = conn.execute("SELECT key, doi, pmid FROM papers WHERE pub_types IS NULL AND (doi IS NOT NULL OR pmid IS NOT NULL)").fetchall()
        for r in rows:
            types = lookup_types(r["doi"], r["pmid"])
            if types:
                set_pub_types(conn, r["key"], types)
                done["fetched"] += 1
            else:
                done["unknown"] += 1
            time.sleep(sleep)
        conn.close()
    return dict(done)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="litrag_parser.paper_type", description=__doc__.split("\n\n")[0])
    ap.add_argument("--lib", action="append", default=[], help="a library directory (repeatable)")
    ap.add_argument("--fetch", action="store_true", help="store Europe PMC's publication types for every paper with a DOI or PMID")
    ap.add_argument("--measure", action="store_true", help="the printed label and the profile against the papers the file or the record labels")
    args = ap.parse_args(argv)
    libs = [Path(l).expanduser() for l in args.lib]
    if not libs or not (args.fetch or args.measure):
        ap.print_help()
        return 2
    if args.fetch:
        print("Europe PMC:", fetch(libs))
    if args.measure:
        from . import lanes

        lanes.configure_from_env(libs[0].resolve().parent)
        r = measure(libs, lanes.active())
        print(f"{r['labelled']} papers labelled by the file or the record {r['labelled_by']}")
        for src, m in r["by_source"].items():
            print(f"  {src:<8} answered {m['answered']:>4} · right {m['right']:>4} · accuracy {m['accuracy']}")
            for t, v in m["per_type"].items():
                if v["truth"] or v["named"]:
                    print(f"           {t:<12} truth {v['truth']:>4} · named {v['named']:>4} · correct {v['correct']:>4} · precision {v['precision']}")
            if m["confusion"]:
                print("           confusions:", m["confusion"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
