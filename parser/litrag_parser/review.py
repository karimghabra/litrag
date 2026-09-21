"""The reader's own log, read back by a local model, which may fill a silence and nothing else.

The outline judge (outline.py) shows a model the paper and asks where its sections are. This asks a
different question: it shows the model *the reading* — every section with the lane the reader gave
it, every paragraph with the lane it inherited — and asks which of those assignments are wrong.
Karim's words for it: "actually have a model read the paper once it has received information".

What it may change is fixed by CLAUDE.md's fifth invariant, and by Karim's decision on
2026-09-19: **a verdict fills a silence; it never overrules a rule that fired.** So of the repairs
the model may answer, only one is applied — a lane for a section whose own heading names none and
which holds no lane at all (`_silent`), in a paper that reads as research. The others are recorded
as notes, so the next measurement can say whether they would have been worth applying:

- `relabel` — applied where the rules are silent, and never towards `abstract`, `references` or
  `back`, which the reader reads from the paper's own words and its order;
- `not_a_section` — noted. A heading refused on a model's word alone would file a section's prose
  under the section before it; `tree._refuse_front_furniture` refuses one on the line's shape, and
  what this note is for is to say how much that shape rule still misses;
- `split` — noted. The boundary half of `outline.apply` builds a heading where a model finds one,
  and until that is shared here a second builder would give the same heading two lanes.

Every answer is a row in `reviews`, keyed by the log the model was shown (the log holds every lane,
so a reader that changes invalidates the answer instead of replaying it against another tree) and
by the policy that read it. A rebuild replays the row and asks nothing. An XML states its own
structure and is never judged.

    python -m litrag_parser.review --pdf-lib DIR --xml-lib DIR [--model M] [--limit N] [--json F]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .outline import CONTENT, LANES, RESEARCH_LIKE, default_url, own_lane, paragraphs_of

PROMPT_VERSION = "review-1"
#: what may be applied, and what is only written down. Karim's decision, 2026-09-19
POLICY_VERSION = "fill-silence-1"
DEFAULT_MODEL = "qwen3:14b"
OPS = ("relabel", "not_a_section", "split")
#: a lane the reader reads from the paper's own words and order, never from a model's
NEVER = ("abstract", "references", "back")
EXCERPT_WORDS = 40
BUDGET_WORDS = 14000

PROMPT = """You are given the *reading* of a scientific paper as a program produced it, not the paper itself. Every section it found is a line "S<n> | level | lane | by <what decided the lane> | <heading>", and every paragraph is a line "[p<n>] in S<n> | lane | <the first words>". The lanes are abstract, introduction, methods, results, results-discussion, discussion, other, back.

Your job is to say where the reading is wrong, as a JSON array of repairs, each an object on its own line:
{"op": "relabel", "section": "S3", "lane": "<lane>", "why": "<a few words>"}
{"op": "not_a_section", "section": "S3", "why": "<a few words>"}
{"op": "split", "section": "S3", "after": "p12", "title": "<the heading you would give what follows>", "why": "<a few words>"}

"relabel" when a section's lane is wrong for the text its paragraphs hold. "not_a_section" when the line the program took for a heading is not one — a journal's name, an author line, a page number, a figure's label. "split" when one section plainly holds two, and the program missed the heading between them.

Answer with the JSON array only, compact, one object per line: no explanation before it, no prose after it, the first character of your answer is [ and the last is ]. Answer [] when the reading is right."""


def enabled() -> str:
    """`off` (nothing asked), `notes` (asked, nothing applied) or `on` (a silence may be filled)."""
    flag = os.environ.get("LITRAG_REVIEW", "").strip().lower()
    return flag if flag in ("notes", "on") else "off"


def default_model() -> str:
    return os.environ.get("LITRAG_REVIEW_MODEL") or DEFAULT_MODEL


def _silent(section: Any) -> bool:
    """Whether the reader has no lane of its own for this section: its own heading names none and it
    holds `other`. A lane inherited from a section the vocabulary did name belongs to that rule, not
    to a silence — the outline judge measured what taking a model's word there costs (a subsection of
    the methods called results sent a paper from 0.996 to 0.594). Numbering is no part of this: the
    numbering settles where a section sits, never what lane its words are in."""
    return own_lane(section.heading or "") == "other" and section.role == "other"


def _lane_source(section: Any) -> str:
    """How the reader came by this section's lane, in the words the log shows the model."""
    if section.label == "built":
        return "a heading the reader built"
    if own_lane(section.heading or "") != "other":
        return "its own heading"
    if (section.level or 1) > 1:
        return "the section above it"
    return "nothing: the heading names no lane"


def reading_log(tree: Any) -> tuple[str, list[Any], list[Any]]:
    """The reading as a log: its sections with their lanes and where each lane came from, then its
    paragraphs with the lane each inherited. The reference list and the front matter are left out,
    as the outline judge leaves them out."""
    paragraphs = paragraphs_of(tree)
    number = {id(n): i + 1 for i, n in enumerate(paragraphs)}
    sections: list[Any] = []
    holds: dict[int, list[int]] = {}
    for n in tree.walk():
        if n.type == "section" and n.heading and n.heading != "Front matter" and n.role != "references":
            sections.append(n)
    index = {id(n): i + 1 for i, n in enumerate(sections)}
    for n in paragraphs:
        owner = next((s for s in reversed(sections) if any(id(c) == id(n) for c in s.children)), None)
        if owner is not None:
            holds.setdefault(index[id(owner)], []).append(number[id(n)])
    total = sum(len((n.text or "").split()) for n in paragraphs)
    cut = EXCERPT_WORDS if total > BUDGET_WORDS else 60
    lines = ["THE SECTIONS"]
    for n in sections:
        ps = holds.get(index[id(n)]) or []
        where = f" | p{ps[0]}–p{ps[-1]}" if ps else " | no paragraphs of its own"
        lines.append(f"S{index[id(n)]} | level {n.level or 1} | lane {n.role} | by {_lane_source(n)} | {(n.heading or '').strip()[:120]!r}{where}")
    lines.append("\nTHE PARAGRAPHS")
    for n in paragraphs:
        owner = next((s for s in reversed(sections) if any(id(c) == id(n) for c in s.children)), None)
        words = (n.text or "").split()
        lines.append(f"[p{number[id(n)]}] in S{index[id(owner)] if owner is not None else '?'} | {n.role} | {' '.join(words[:cut])}{' …' if len(words) > cut else ''}")
    return "\n".join(lines), sections, paragraphs


def signature(log: str, model: str) -> str:
    """The answer is keyed by the log and the model, never by the policy that reads it: a policy is
    weighed again against the answers already stored, without asking a model twice."""
    return hashlib.sha1(f"{PROMPT_VERSION}\n{model}\n{log}".encode()).hexdigest()


def ask(log: str, *, model: str | None = None, url: str | None = None, timeout: float = 1800.0) -> dict[str, Any]:
    """One answer from the model, as the outline judge asks for one: no sampling, no thinking."""
    model, url = model or default_model(), url or default_url()
    words = len(log.split())
    num_ctx = min(40960, max(8192, 2048 * (1 + int((words * 1.45 + 2 * len(PROMPT.split()) + 2000) // 2048))))
    body = {"model": model, "stream": False, "think": False,
            "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": log}],
            "options": {"temperature": 0, "seed": 7, "num_ctx": num_ctx, "num_predict": 4000}}
    req = urllib.request.Request(f"{url}/api/chat", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        reply = json.loads(r.read().decode("utf-8"))
    return {"content": reply.get("message", {}).get("content", ""), "seconds": round(time.time() - t0, 1),
            "prompt_tokens": reply.get("prompt_eval_count"), "answer_tokens": reply.get("eval_count"), "num_ctx": num_ctx}


def parse(content: str, sections: list[Any], paragraphs: list[Any]) -> list[dict[str, Any]] | None:
    """The repairs the answer holds, as strictly as `outline.parse` reads an outline: an op we
    know, a section that exists, a lane that is a lane. None when there is no array."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if len(text.split("```")) > 1 else text
        text = text[4:] if text.lower().startswith("json") else text
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        return None
    try:
        answer = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        cut = text.rfind("}")
        if cut < start:
            return None
        try:
            answer = json.loads(text[start : cut + 1] + "]")
        except json.JSONDecodeError:
            return None
    if not isinstance(answer, list):
        return None
    out: list[dict[str, Any]] = []
    for entry in answer[:12]:
        if not isinstance(entry, dict):
            continue
        op = str(entry.get("op") or "").strip().lower()
        if op not in OPS:
            continue
        try:
            which = int(str(entry.get("section") or "").strip().lstrip("Ss"))
        except ValueError:
            continue
        if not 1 <= which <= len(sections):
            continue
        repair = {"op": op, "section": which, "why": str(entry.get("why") or "")[:80]}
        if op == "relabel":
            lane = str(entry.get("lane") or "").strip().lower()
            if lane not in LANES:
                continue
            repair["lane"] = lane
        if op == "split":
            try:
                after = int(str(entry.get("after") or "").strip().lstrip("Pp"))
            except ValueError:
                continue
            if not 1 <= after <= len(paragraphs):
                continue
            repair["after"] = after
            repair["title"] = str(entry.get("title") or "").strip()[:200]
        out.append(repair)
    return out


def apply(tree: Any, repairs_answer: list[dict[str, Any]], sections: list[Any], repairs: dict[str, int], *, model: str, act: bool, paper_type: str | None = None) -> dict[str, Any]:
    """The repairs, weighed against the policy: a lane where the rules are silent is taken (when
    `act`), everything else is written down. Returns what was done, by op.

    Only in a paper that reads as research. A review's sections are topical and its XML lanes them
    `other` however much they read like methods or results; the outline judge measured what taking a
    model's lane there costs (0.996 → 0.594 on one paper), and the first run of this pass measured it
    again (a review from 0.979 to 0.306). So in a review the model's lane is written down, never
    taken, and the reader's silence stands."""
    from .tree import _descendants

    done: dict[str, int] = {"relabel": 0, "not_a_section": 0, "split": 0, "noted": 0}
    changed = False
    for r in repairs_answer:
        section = sections[r["section"] - 1]
        op = r["op"]
        research = paper_type in RESEARCH_LIKE
        if op == "relabel" and act and research and _silent(section) and r["lane"] in CONTENT and r["lane"] not in NEVER and r["lane"] != section.role and section.role not in NEVER:
            for n in [section, *_descendants(section)]:
                n.role = r["lane"]
            done["relabel"] += 1
            changed = True
            continue
        why = {"relabel": "the reader has a lane for it already, and a rule stands" if not _silent(section) else ("this paper reads as a review, whose sections are topical" if not research else "a lane the reader reads from the paper's own order"),
               "not_a_section": "a heading is refused on the shape of its line, never on a model's word",
               "split": "a heading is built by the outline judge, which lanes it by the reader's rule"}[op]
        tree.notes.append({"kind": f"review-{op}", "node_id": section.node_id, "page": section.page,
                           "message": f"{model} says {op} {section.heading!r}" + (f" → {r['lane']}" if r.get("lane") else "") + f" ({r['why']}); not applied: {why}"})
        done["noted"] += 1
    if changed:
        roles: dict[str, int] = {}
        for n in tree.walk():
            if n.type != "document":
                roles[n.role] = roles.get(n.role, 0) + 1
        tree.roles = dict(sorted(roles.items()))
        tree.has_methods = roles.get("methods", 0) > 0
    for k, v in done.items():
        if v:
            repairs[f"review_{k}"] = repairs.get(f"review_{k}", 0) + v
    return done


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS reviews (
      paper TEXT NOT NULL, signature TEXT NOT NULL, model TEXT NOT NULL, policy TEXT NOT NULL,
      repairs TEXT NOT NULL, applied TEXT, prompt_tokens INTEGER, answer_tokens INTEGER, seconds REAL,
      at TEXT NOT NULL, PRIMARY KEY(paper, signature))""")
    conn.commit()


def stored(conn: sqlite3.Connection, paper: str, sig: str) -> list[dict[str, Any]] | None:
    _ensure(conn)
    row = conn.execute("SELECT repairs FROM reviews WHERE paper = ? AND signature = ?", (paper, sig)).fetchone()
    return json.loads(row[0]) if row else None


def remember(conn: sqlite3.Connection, paper: str, sig: str, model: str, answer: list[dict[str, Any]], applied: dict[str, Any], stats: dict[str, Any]) -> None:
    _ensure(conn)
    conn.execute("INSERT OR REPLACE INTO reviews (paper, signature, model, policy, repairs, applied, prompt_tokens, answer_tokens, seconds, at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (paper, sig, model, POLICY_VERSION, json.dumps(answer, ensure_ascii=False), json.dumps(applied), stats.get("prompt_tokens"), stats.get("answer_tokens"), stats.get("seconds"),
                  time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    conn.commit()


def judge(tree: Any, conn: sqlite3.Connection, paper: str, *, model: str | None = None, url: str | None = None, ask_model: bool = True, repairs: dict[str, int] | None = None, paper_type: str | None = None, pub_types: str | None = None) -> dict[str, Any]:
    """The pass itself: replay the row, or ask once and store it. An XML is never judged."""
    model = model or default_model()
    repairs = repairs if repairs is not None else tree.repairs
    if not tree.pages:
        return {"model": model, "asked": False, "skipped": "an XML states its own structure"}
    if paper_type is None:
        from . import lanes
        from .paper_type import decide

        paper_type = decide(tree, pub_types=pub_types, oracle=lanes.active()).get("type")
    log, sections, paragraphs = reading_log(tree)
    if not sections:
        return {"model": model, "asked": False, "skipped": "no sections to read"}
    sig = signature(log, model)
    answer = stored(conn, paper, sig)
    asked = False
    stats: dict[str, Any] = {}
    if answer is None:
        if not ask_model:
            return {"model": model, "asked": False, "replayed": False}
        try:
            reply = ask(log, model=model, url=url)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
            return {"model": model, "asked": True, "error": str(e)[:200]}
        answer = parse(reply["content"], sections, paragraphs)
        asked = True
        stats = reply
        if answer is None:
            return {"model": model, "asked": True, "unreadable": reply["content"][:200]}
    done = apply(tree, answer, sections, repairs, model=model, act=enabled() == "on", paper_type=paper_type)
    if asked:
        remember(conn, paper, sig, model, answer, done, stats)
    return {"model": model, "asked": asked, "repairs": len(answer), **done, "seconds": stats.get("seconds")}


def measure(pdf_lib: Path, xml_lib: Path, *, model: str, limit: int | None = None, url: str | None = None, act: bool = True, keys: set[str] | None = None) -> list[dict[str, Any]]:
    """Every pair read twice — as the reader has it, and with the log read back — reporting what the
    model would change and what it costs. Only papers with a silence to fill are asked."""
    from . import lanes
    from .harness import read_paper
    from .library import library_root, parsed_papers
    from .pairs import compare, pairs_of

    lanes.configure_from_env(library_root())
    conn = sqlite3.connect(str(Path(pdf_lib) / "store.sqlite"))
    out: list[dict[str, Any]] = []
    rows = {r["key"]: r for r in parsed_papers(Path(pdf_lib))}
    for p_row, x_row in pairs_of(Path(pdf_lib), Path(xml_lib)):
        if limit is not None and len(out) >= limit:
            break
        if keys is not None and p_row["key"] not in keys:
            continue  # a held-out half is measured once, at the end, and never tuned on
        pdf, kind, _ = read_paper(Path(pdf_lib), p_row)
        xml, _, xml_bytes = read_paper(Path(xml_lib), x_row)
        silent = [n for n in pdf.walk() if n.type == "section" and n.heading and _silent(n)]
        if not silent:
            continue
        before = compare(pdf, xml, xml_bytes)
        os.environ["LITRAG_REVIEW"] = "on" if act else "notes"
        verdict = judge(pdf, conn, p_row["key"], model=model, url=url, ask_model=True, paper_type=kind.get("type"))
        after = compare(pdf, xml, xml_bytes)
        out.append({"key": p_row["key"], "type": kind.get("type"), "silent": len(silent), "verdict": verdict,
                    "type_is_research": kind.get("type") in RESEARCH_LIKE,
                    "before": {k: before[k] for k in ("faithful", "placed", "precision")},
                    "after": {k: after[k] for k in ("faithful", "placed", "precision")}})
        r = out[-1]
        print(f"  {r['key']:40} {str(r['type'])[:10]:11} silent {r['silent']:2} · repairs {verdict.get('repairs', 0)} relabelled {verdict.get('relabel', 0)} noted {verdict.get('noted', 0)}"
              f" · faithful {r['before']['faithful']:.3f} → {r['after']['faithful']:.3f} · placed {r['before']['placed']:.3f} → {r['after']['placed']:.3f} · {verdict.get('seconds')}s", flush=True)
    conn.close()
    return out


def summary(records: list[dict[str, Any]]) -> str:
    if not records:
        return "no paper had a lane the rules left silent"
    d = [(r["after"]["faithful"] - r["before"]["faithful"]) for r in records]
    p = [(r["after"]["placed"] - r["before"]["placed"]) for r in records]
    applied = sum(r["verdict"].get("relabel", 0) for r in records)
    noted = sum(r["verdict"].get("noted", 0) for r in records)
    better = sum(1 for x in d if x > 0.002)
    worse = sum(1 for x in d if x < -0.002)
    secs = [r["verdict"].get("seconds") or 0 for r in records]
    return (f"{len(records)} papers with a silence · {applied} lanes filled, {noted} repairs noted · "
            f"faithful {sum(d) / len(d):+.4f} mean, placed {sum(p) / len(p):+.4f} mean · {better} papers better, {worse} worse · "
            f"{sum(secs) / len(secs):.1f}s a paper")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="litrag_parser.review", description=__doc__.split("\n\n")[0])
    ap.add_argument("--pdf-lib", required=True)
    ap.add_argument("--xml-lib", required=True)
    ap.add_argument("--model", default=None)
    ap.add_argument("--url", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--notes-only", action="store_true", help="ask and write down, change nothing")
    ap.add_argument("--keys-file", help="only these papers, one key a line: the working half of a held-out set")
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    model = args.model or default_model()
    print(f"the reading read back by {model}, policy {POLICY_VERSION}")
    keys = {ln.strip() for ln in Path(args.keys_file).read_text("utf-8").splitlines() if ln.strip()} if args.keys_file else None
    records = measure(Path(args.pdf_lib), Path(args.xml_lib), model=model, limit=args.limit, url=args.url, act=not args.notes_only, keys=keys)
    print(summary(records))
    if args.json:
        Path(args.json).write_text(json.dumps(records, indent=1, ensure_ascii=False), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
