"""A local model reads two adjacent blocks and says whether they are one paragraph.

The layout model splits a paragraph wherever a page or a column ends and never joins it
back. The rules in `tree.py` join the clear cases — a lowercase continuation, a bracket
closed on the next page, a trailing comma. What is left is a block that ends without a
full stop followed by one that starts with a capital: a sentence broken before a proper
noun, a numbered species name, a citation. Those a person decides by reading; so does a
model on this machine, through Ollama on 127.0.0.1, one short prompt per pair, JSON
back. Nothing leaves the machine.

Every verdict is a row in `judgments`, keyed by the two blocks' ends, so a `rebuild` reuses
it without a model and an `ingest` asks only about pairs no one has judged. The judge is
consulted only where the rules are silent; where they join, they join. Since the boundary
scorer (boundary.py) exists, a pair no row answers goes to it first and to the generative
model only when that is asked for; `main()` below configures the oracle of meaning too,
so the trees it saves are the ones an ingest would build.

    uv run --project parser python -m litrag_parser.judge --lib ~/.protracker/library/looped-ligament [--key K] [--model qwen3:14b] [--dry-run]
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

from .library import now_iso, parsed_papers, safe_key
from .store import judgment as cached_judgment
from .store import save_judgment

DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:14b"
TAIL, HEAD = 500, 400

SYSTEM = (
    "You check whether two consecutive text blocks extracted from a scientific paper are one paragraph that a page "
    "or column break split in two, or two separate paragraphs. A paragraph split in two has a sentence that runs from "
    "the end of the first block into the start of the second. Reply with JSON only: "
    '{"same_paragraph": true} or {"same_paragraph": false}.'
)


def pair_key(a: str, b: str) -> str:
    return hashlib.sha1((a[-TAIL:] + "\n␞\n" + b[:HEAD]).encode("utf-8")).hexdigest()


def prompt_for(a: str, b: str) -> str:
    return (
        f"Block A ends with:\n«{a[-TAIL:]}»\n\nBlock B begins with:\n«{b[:HEAD]}»\n\n"
        "Is Block B the continuation of Block A's last sentence, so that A and B are one paragraph? "
        "Or does B start a new paragraph?"
    )


def default_url() -> str:
    return os.environ.get("LITRAG_OLLAMA_URL") or DEFAULT_URL


def default_model(lib_dir: Path | None = None) -> str:
    """The library's own chat model when its `library.json` names one, else the environment's, else qwen3:14b."""
    if lib_dir is not None:
        try:
            cfg = json.loads((lib_dir / "library.json").read_text("utf-8"))
            model = (cfg.get("ollama") or {}).get("chat")
            if model:
                return str(model)
        except Exception:
            pass
    return os.environ.get("LITRAG_JUDGE_MODEL") or DEFAULT_MODEL


def available(url: str | None = None, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{url or default_url()}/api/tags", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def ask_ollama(a: str, b: str, *, model: str, url: str, timeout: float = 120.0) -> bool | None:
    """One verdict from the model, or None when it cannot be had (down, slow, not JSON)."""
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt_for(a, b)}],
        "stream": False,
        "format": "json",
        "think": False,
        "options": {"temperature": 0, "num_predict": 30},
    }
    req = urllib.request.Request(f"{url}/api/chat", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            reply = json.loads(r.read().decode("utf-8"))
        content = json.loads(reply["message"]["content"])
        verdict = content.get("same_paragraph")
        return bool(verdict) if isinstance(verdict, bool) else None
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, OSError):
        return None


class Judge:
    """Callable for `build_tree(judge=…)`: cached verdicts first; then the boundary scorer
    (boundary.py) when one is given; then the generative model, only when asked to ask.
    A rebuild gives neither, and answers from the rows alone."""

    def __init__(self, conn: sqlite3.Connection, paper: str, *, ask_model: bool = False, model: str | None = None, url: str | None = None, scorer: Any = None):
        self.conn, self.paper = conn, paper
        self.ask_model = ask_model
        self.model = model or default_model()
        self.url = url or default_url()
        self.scorer = scorer  # `(a, b, ctx) -> bool | None` with a `.name`, or None
        self.calls = self.hits = self.asked = self.scored = self.yes = self.errors = 0
        self.log: list[dict[str, Any]] = []

    def __call__(self, a: str, b: str, ctx: dict[str, Any] | None = None) -> bool | None:
        self.calls += 1
        key = pair_key(a, b)
        cached = cached_judgment(self.conn, self.paper, key)
        if cached is not None:
            self.hits += 1
            self.yes += int(cached)
            return bool(cached)
        verdict: bool | None = None
        who = self.model
        if self.scorer is not None:
            verdict = self.scorer(a, b, ctx)
            if verdict is not None:
                self.scored += 1
                who = getattr(self.scorer, "name", "boundary")
        if verdict is None:
            if not self.ask_model:
                return None
            self.asked += 1
            verdict = ask_ollama(a, b, model=self.model, url=self.url)
            if verdict is None:
                self.errors += 1
                return None
        save_judgment(self.conn, self.paper, key, verdict, who, now_iso())
        self.yes += int(verdict)
        self.log.append({"same": verdict, "by": who, "a": a[-120:], "b": b[:120], "page": (ctx or {}).get("page")})
        return verdict

    def summary(self) -> dict[str, int]:
        return {"pairs": self.calls, "cached": self.hits, "scored": self.scored, "asked": self.asked, "joined": self.yes, "errors": self.errors}


def main(argv: list[str] | None = None) -> int:
    from .citations import link_citations
    from .harness import pdf_title
    from .recover import recover_from_pdf
    from .store import open_store, save_refs, save_tree
    from .tree import build_tree

    ap = argparse.ArgumentParser(prog="litrag_parser.judge", description=__doc__.split("\n\n")[0])
    ap.add_argument("--lib", required=True, help="a library directory")
    ap.add_argument("--key", action="append", default=[], help="one paper (repeatable); every parsed paper when absent")
    ap.add_argument("--model")
    ap.add_argument("--url")
    ap.add_argument("--dry-run", action="store_true", help="count the pairs the model would be asked about; ask nothing, save nothing")
    ap.add_argument("--show", action="store_true", help="print each verdict with the two ends")
    args = ap.parse_args(argv)
    lib = Path(args.lib).expanduser()
    from . import lanes

    lanes.configure_from_env(lib.resolve().parent)
    url = args.url or default_url()
    model = args.model or default_model(lib)
    if not args.dry_run and not available(url):
        print(f"Ollama is not answering at {url}; start it, or pass --dry-run to count pairs", file=sys.stderr)
        return 2
    conn = open_store(lib / "store.sqlite")
    rows = parsed_papers(lib, args.key or None)
    t0 = time.time()
    totals = {"pairs": 0, "cached": 0, "scored": 0, "asked": 0, "joined": 0, "errors": 0}
    for row in rows:
        raw, source = row["raw"], row["source"]
        hint = pdf_title(source) if row["format"] == "pdf" and source and source.exists() else None
        judge = Judge(conn, row["key"], ask_model=not args.dry_run, model=model, url=url)
        if args.dry_run:
            judge.ask_model = False
            asked = []
            judge_dry = lambda a, b, ctx=None: (asked.append((a, b)), None)[1]  # noqa: E731
            doc = json.loads(raw.read_text("utf-8"))
            recover_from_pdf(doc, source if row["format"] == "pdf" else None)
            tree = build_tree(doc, row["key"], title_hint=hint, judge=judge_dry)
            totals["pairs"] += len(asked)
            print(f"{row['key'][:40]:<40} {len(asked):>3} pairs to judge")
            continue
        doc = json.loads(raw.read_text("utf-8"))
        recover_from_pdf(doc, source if row["format"] == "pdf" else None)
        tree = build_tree(doc, row["key"], title_hint=hint, judge=judge)
        save_tree(conn, row["key"], tree, parser=f"judge {model}", parsed_at=now_iso(), seconds=0.0)
        refs, cites = link_citations(tree, source.read_bytes() if row["format"] == "jats" and source and source.exists() else None)
        save_refs(conn, row["key"], refs, cites)
        s = judge.summary()
        for k in totals:
            totals[k] += s[k]
        print(f"{row['key'][:40]:<40} pairs {s['pairs']:>3} · cached {s['cached']:>3} · asked {s['asked']:>3} · joined {s['joined']:>3}" + (f" · errors {s['errors']}" if s["errors"] else ""))
        if args.show:
            for v in judge.log:
                print(f"    {'JOIN' if v['same'] else 'keep'} …{v['a']!r} || {v['b']!r}…")
    conn.close()
    print(f"\n{len(rows)} papers · {totals} · {round(time.time() - t0, 1)}s · model {model}")
    return 1 if totals["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
