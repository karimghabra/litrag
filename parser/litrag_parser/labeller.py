"""The truth set labelled by the local model, for a person to audit.

A person's labels are the truth for the finding→method links (`truth.py`), and a hundred of
them is an evening. Here the local model — Ollama on 127.0.0.1, `qwen3:14b` unless
`LITRAG_LABEL_MODEL` names another — labels the findings a person would be offered from
scratch (`truth.draw`, by the same seed), and every finding a person has already labelled.
The person then labels as before: the queue offers the model's findings first and never says
what it answered, so those labels are its audit. Once a person has labelled `AUDIT_MIN` of
them and agrees on `AGREEMENT_GATE` (`truth.agreement`), the model's labels count for the
findings no person labelled; until then they are compared, never counted.

- **The question** is the person's, with the same rule beside it: the paper's methods
  candidates (`edges.method_candidates`), each subsection numbered with its paragraphs — [M1],
  [M1.1], [M1.2], … — then the finding, where it sits, and the captions of the figures it
  cites. The methods come first and the finding last, so the findings of one paper share the
  model's cached prompt.
- **The answer** is a JSON object, constrained by a schema: `{"methods": ["M2"], "paragraphs":
  ["M2.3"]}` — the methods it was measured by, none for no method in this paper, and the
  paragraph each rests on when one does. Thinking off, temperature zero, a fixed seed.
- **The rows** are `model_labels`, the shape of `link_labels`, `by` the model: `yes` for each
  method named, `no` for every other candidate, or `none` alone. A finding this model has
  labelled is not asked again; another model's labels on it are replaced. An answer that
  cannot be read is counted and stores nothing.

Nothing leaves the machine: the paper's text goes to the model on 127.0.0.1 and nowhere else.

    uv run --project parser --no-sync python -m litrag_parser.labeller --lib DIR [--n 100] [--seed 0] [--model M]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from . import truth
from .tree import Node

DEFAULT_MODEL = "qwen3:14b"
DEFAULT_URL = "http://127.0.0.1:11434"
PROMPT_VERSION = "label-1"
BUDGET_WORDS = 7000  # methods longer than this are sent as the first EXCERPT_WORDS of each paragraph
EXCERPT_WORDS = 80
CAPTION_CHARS = 400

# The person's question and the rule beside it, as the labelling panel puts them (app/src/renderer/labels.ts)
RULE = ("Tick the procedures that produced what the finding reports — several if it rests on several. "
        "Leave the preparation of the material and the statistics out unless the finding reports them.")
PROMPT = f"""You are given the methods section of a scientific paper, as subsections numbered [M1], [M2], … with their paragraphs numbered [M1.1], [M1.2], …, and then one finding from the same paper.

Which of the paper's methods was this finding measured by? {RULE} If none of them produced it — it reports another paper's work, or this paper does not say how it was measured — name none.

For each method you name, also name the one paragraph inside it the finding rests on most, if one paragraph clearly does.

Answer with a JSON object only: {{"methods": ["M2", "M4"], "paragraphs": ["M2.3"]}} — "methods" empty for none, "paragraphs" empty when no paragraph clearly stands out."""

SCHEMA = {
    "type": "object",
    "properties": {"methods": {"type": "array", "items": {"type": "string"}}, "paragraphs": {"type": "array", "items": {"type": "string"}}},
    "required": ["methods", "paragraphs"],
}

Asker = Callable[[str, str, str], dict[str, Any]]


def default_model() -> str:
    return os.environ.get("LITRAG_LABEL_MODEL") or DEFAULT_MODEL


def default_url() -> str:
    return (os.environ.get("LITRAG_OLLAMA_URL") or DEFAULT_URL).rstrip("/")


# -- the question -------------------------------------------------------------------------------------------


def _paragraphs(c: Node) -> list[Node]:
    return [p for p in truth._walk(c) if p is not c and p.type in ("paragraph", "list_item") and (p.text or "").strip()]


def _captions(conn: sqlite3.Connection, finding: str) -> list[str]:
    """The captions of the figures and tables the finding cites (`cites_figure` edges)."""
    out = []
    for r in conn.execute("SELECT dst FROM edges WHERE src = ? AND kind = 'cites_figure' ORDER BY rowid", (finding,)):
        text = " ".join(x["text"] for x in conn.execute("SELECT text FROM nodes WHERE parent = ? AND type = 'caption' ORDER BY ordinal", (r["dst"],)) if x["text"])
        if text:
            out.append(text if len(text) <= CAPTION_CHARS else text[:CAPTION_CHARS].rsplit(" ", 1)[0] + " …")
    return out


def methods_text(candidates: list[Node]) -> tuple[str, dict[str, str]]:
    """The candidates numbered as the model sees them, and what each number names: `M2` a
    method's node id, `M2.3` a paragraph's. Long methods go as the opening words of each
    paragraph."""
    blocks: list[tuple[str, str]] = []  # (label, text) in order; a method's heading has label M<i>
    ids: dict[str, str] = {}
    for i, c in enumerate(candidates, 1):
        m = f"M{i}"
        ids[m] = c.node_id
        if c.type == "section":
            blocks.append((m, c.heading or "(untitled)"))
            for j, p in enumerate(_paragraphs(c), 1):
                ids[f"{m}.{j}"] = p.node_id
                blocks.append((f"{m}.{j}", p.text))
        else:  # a methods paragraph standing for a section with no subheadings
            blocks.append((m, c.text))
    words = sum(len(t.split()) for _, t in blocks)
    cut = words > BUDGET_WORDS

    def shown(label: str, text: str) -> str:
        w = " ".join(text.split())
        if cut and "." in label:
            ws = w.split()
            w = " ".join(ws[:EXCERPT_WORDS]) + (" …" if len(ws) > EXCERPT_WORDS else "")
        return f"[{label}] {w}"

    return "\n".join(("\n" if "." not in label and k else "") + shown(label, text) for k, (label, text) in enumerate(blocks)), ids


def prompt_for(conn: sqlite3.Connection, candidates: list[Node], finding: Node) -> tuple[str, dict[str, str]]:
    """The whole prompt for one finding, and what its numbers name."""
    methods, ids = methods_text(candidates)
    where = " › ".join(finding.ancestry or []) + (f", p. {finding.page}" if finding.page else "")
    caps = _captions(conn, finding.node_id)
    tail = f"FINDING ({where}):\n{' '.join((finding.text or '').split())}"
    if caps:
        tail += "\n\nThe figures it cites:\n" + "\n".join(f"- {c}" for c in caps)
    return f"{PROMPT}\n\nMETHODS:\n{methods}\n\n{tail}", ids


# -- the model ----------------------------------------------------------------------------------------------


def ask(prompt: str, model: str, url: str, timeout: float = 600.0) -> dict[str, Any]:
    """One request to Ollama: `{content, prompt_tokens, answer_tokens, seconds}`. Thinking off,
    temperature zero, the answer held to the schema, a window sized to the prompt."""
    need = int(len(prompt.split()) * 1.45) + 600
    num_ctx = min(40960, max(8192, ((need + 2047) // 2048) * 2048))
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "format": SCHEMA,
        "options": {"temperature": 0, "seed": 7, "num_ctx": num_ctx, "num_predict": 300},
    }
    req = urllib.request.Request(f"{url}/api/chat", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("error") or detail
        except (json.JSONDecodeError, AttributeError):
            pass
        raise RuntimeError(f"the model {model!r} at {url}: {detail}" + (f" — `ollama pull {model}`" if "not found" in str(detail) else "")) from None
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"Ollama is not answering at {url} ({getattr(e, 'reason', e)}): start it, and `ollama pull {model}`") from None
    return {"content": (resp.get("message") or {}).get("content", ""), "prompt_tokens": resp.get("prompt_eval_count"),
            "answer_tokens": resp.get("eval_count"), "seconds": round(time.time() - t0, 2)}


_LABEL = re.compile(r"^\s*\[?\s*(M\d+(?:\.\d+)?)\s*\]?\s*$", re.I)


def parse(content: str, ids: dict[str, str]) -> dict[str, Any] | None:
    """The answer read: `{methods: [node_id], paragraphs: {method: node_id}}` — numbers the
    prompt did not give left out, a paragraph only inside a method named, the first for each.
    None when there is no object to read, or it names nothing the prompt gave yet is not empty."""
    s = content.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s).rstrip("`").strip()
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        raw = json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("methods"), list):
        return None

    def label(x: Any) -> str | None:
        m = _LABEL.match(str(x))
        return m.group(1).upper() if m else None

    named = [label(x) for x in raw["methods"]]
    # a paragraph's number where a method's was wanted names its method
    methods = list(dict.fromkeys(ids[m.split(".")[0]] for m in named if m and m.split(".")[0] in ids))
    if raw["methods"] and not methods:
        return None  # it named something, and nothing the prompt gave
    paragraphs: dict[str, str] = {}
    for x in list(raw.get("paragraphs") or []) + [m for m in named if m and "." in m]:
        p = label(x)
        if p and "." in p and p in ids:
            m = ids[p.split(".")[0]]
            if m in methods and m not in paragraphs:
                paragraphs[m] = ids[p]
    return {"methods": methods, "paragraphs": paragraphs}


# -- the rows -----------------------------------------------------------------------------------------------


def rows_of(p: truth._Paper, finding: Node, answer: dict[str, Any], model: str, at: str) -> list[tuple[Any, ...]]:
    """The answer as `model_labels` rows: yes for each method named, no for every other
    candidate, or `none` alone."""
    head = (p.key, finding.node_id, finding.text[: truth.TEXT_CHARS])
    if not answer["methods"]:
        return [(*head, "", "", None, None, "none", model, at)]
    out = []
    for c in p.candidates:
        yes = c.node_id in answer["methods"]
        para = answer["paragraphs"].get(c.node_id) if yes else None
        out.append((*head, c.node_id, truth._method_label(c), para, p.by_id[para].text[: truth.TEXT_CHARS] if para else None, "yes" if yes else "no", model, at))
    return out


def sample(conn: sqlite3.Connection, papers: truth._Papers, n: int, seed: int, per_paper: int) -> list[tuple[truth._Paper, Node]]:
    """What the model labels: the findings a person would be offered from scratch, then every
    finding a person has labelled that is not among them, so each can be compared."""
    drawn = truth.draw(papers, n, seed, per_paper)
    have = {f.node_id for _, f in drawn}
    for a in truth.anchor(conn, papers):
        fid = a["finding_now"]
        if fid and fid not in have:
            p = papers.get(a["paper"])
            if p is not None and p.candidates and fid in p.by_id:
                have.add(fid)
                drawn.append((p, p.by_id[fid]))
    return drawn


def label(conn: sqlite3.Connection, n: int = 100, seed: int = 0, per_paper: int = truth.PER_PAPER, *, model: str | None = None, url: str | None = None,
          asker: Asker | None = None, on_progress: Callable[[int, int, str], None] | None = None) -> dict[str, Any]:
    """The model's labels for the sample (`sample`): `{model, findings, asked, labelled, already,
    unreadable, seconds, prompt_tokens}`. A finding this model has labelled is not asked again;
    Ollama not answering stops the run with the reason, the labels so far kept."""
    from .library import now_iso

    model = model or default_model()
    url = url or default_url()
    asker = asker or (lambda prompt, m, u: ask(prompt, m, u))
    papers = truth._Papers(conn)
    todo = sample(conn, papers, n, seed, per_paper)
    mine = {a["finding_now"] for a in truth.anchor(conn, papers, table="model_labels") if a["by"] == model and a["finding_now"]}
    out = {"model": model, "findings": len(todo), "asked": 0, "labelled": 0, "already": 0, "unreadable": 0, "seconds": 0.0, "prompt_tokens": 0}
    for i, (p, f) in enumerate(todo):
        if on_progress:
            on_progress(i, len(todo), f"{model} labelling finding {i + 1} of {len(todo)}")
        if f.node_id in mine:
            out["already"] += 1
            continue
        prompt, ids = prompt_for(conn, p.candidates, f)
        got = asker(prompt, model, url)
        out["asked"] += 1
        out["seconds"] = round(out["seconds"] + float(got.get("seconds") or 0), 2)
        out["prompt_tokens"] += int(got.get("prompt_tokens") or 0)
        answer = parse(str(got.get("content") or ""), ids)
        if answer is None:
            out["unreadable"] += 1
            continue
        truth._write(conn, p, f.node_id, rows_of(p, f, answer, model, now_iso()), papers, table="model_labels")
        out["labelled"] += 1
    if on_progress:
        on_progress(len(todo), len(todo), f"{model} labelled {out['labelled']} findings")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="litrag_parser.labeller", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--lib", required=True, help="a library directory")
    ap.add_argument("--n", type=int, default=100, help="findings to label (default 100)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--per-paper", type=int, default=truth.PER_PAPER)
    ap.add_argument("--model", default=None, help=f"the Ollama model (default {DEFAULT_MODEL}, or LITRAG_LABEL_MODEL)")
    args = ap.parse_args(argv)
    conn = truth._open(args.lib)
    try:
        out = label(conn, args.n, args.seed, args.per_paper, model=args.model,
                    on_progress=lambda done, total, msg: print(f"\r{msg}", end="", file=sys.stderr, flush=True))
        print(file=sys.stderr)
        print(json.dumps(out, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
