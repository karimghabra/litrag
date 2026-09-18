"""Whether one block continues another, by how likely its first words are after the last.

The rules in `tree.py` join a paragraph the page cut when the words say so (an open
bracket, a lowercase start, a trailing "and") and the page's geometry joins it when the
layout says so (a full last line before a flush-left first line, in a paper that indents).
What is left is a block that ends without a full stop and a block that starts with a
capital, a digit or a bracket: "…was measured on the" ‖ "Instron 5944 at 1 mm/min". A
person reads the two ends and knows. An embedder does not — two paragraphs of one methods
section lie as near each other whether or not they are one paragraph — so this is not a
question for `meaning.py`. It is a question of likelihood: how probable are the first
words of B after the last words of A, compared with how probable they are on their own.
A small language model scores that in one forward pass each, with no generation, and
the difference (a pointwise mutual information over the boundary) is the verdict once
it clears a threshold calibrated against the verdicts a person and the generative judge
already gave.

The model (`Qwen/Qwen2.5-0.5B` unless `LITRAG_BOUNDARY_MODEL` says otherwise) is fetched
once from Hugging Face into the same cache Docling's models live in, and runs here, on
the CPU in single precision, so the same pair gives the same score to the last place on
one machine and to a few thousandths on another. What makes a rebuild the same on every
machine is not the arithmetic but the row: every verdict goes into `judgments` beside
the judge's, keyed by the two blocks' ends, and a rebuild replays it. `LITRAG_BOUNDARY=off`
leaves the scorer out; with no model cached and no network, it is simply unavailable and
the judge behaves as before.

    uv run --project parser python -m litrag_parser.boundary --calibrate --lib ~/.protracker/library/looped-ligament [--lib …]
        scores every pair the judge already ruled on and reports precision and recall of the
        joins per threshold, with the threshold chosen paper-out
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from .judge import HEAD, TAIL, Judge, pair_key
from .library import parsed_papers

DEFAULT_MODEL = "Qwen/Qwen2.5-0.5B"
HEAD_TOKENS = 24  # the boundary is where the signal is; a whole head dilutes it
#: nats per token of mutual information. On 600 pairs cut from the corpora's own paragraphs in
#: the shape `_judge_candidate` puts to the judge (paper-out) the best threshold was 1.05 with
#: precision 0.92 and recall 0.92; at 1.5 precision is 0.97 and recall 0.71. The 71 pairs the
#: generative judge had ruled on and the rules still leave open are harsher: five of their 69
#: keeps score above 1.5 (a funding line, an affiliation line, a sentence ending in a zero-width
#: space), so the bar is set high.
THRESHOLD = 1.5
#: whether an ingest asks the scorer without being told to. Off: the synthetic gate is passed,
#: but on the real leftover pairs a join in fifteen would be wrong, and a wrong join is a
#: silent merge. `LITRAG_BOUNDARY=on` turns it on; NOTES.md has the numbers.
DEFAULT_ON = False


def default_model() -> str:
    return os.environ.get("LITRAG_BOUNDARY_MODEL") or DEFAULT_MODEL


def enabled() -> bool:
    flag = os.environ.get("LITRAG_BOUNDARY")
    if flag is None:
        return DEFAULT_ON
    return flag.strip().lower() in ("on", "1", "true", "yes")


class BoundaryScorer:
    """Callable with the judge's protocol: `(a, b, ctx) -> bool | None`, None when the
    model cannot be had. `name` is what the verdict is recorded under."""

    def __init__(self, model: str | None = None, threshold: float = THRESHOLD, head_tokens: int = HEAD_TOKENS):
        self.model_name = model or default_model()
        self.threshold = threshold
        self.head_tokens = head_tokens
        self.name = f"boundary:{self.model_name}@{self.threshold}"
        self._model: Any = None
        self._tok: Any = None
        self._down = False
        self.error: str | None = None
        self.scored = 0
        self.seconds = 0.0

    # -- the model ------------------------------------------------------------------------------
    def _load(self) -> bool:
        if self._model is not None:
            return True
        if self._down:
            return False
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            torch.manual_seed(0)
            self._tok = AutoTokenizer.from_pretrained(self.model_name)
            self._model = AutoModelForCausalLM.from_pretrained(self.model_name, dtype=torch.float32)
            self._model.eval()
            return True
        except Exception as e:  # noqa: BLE001 - no model, no scorer: the judge behaves as before
            self._down = True
            self.error = f"{type(e).__name__}: {e}"[:300]
            return False

    def available(self) -> bool:
        return self._load()

    def _logp(self, context: list[int], target: list[int]) -> float:
        """Mean log-probability per token of `target` after `context`, in nats."""
        import torch

        ids = torch.tensor([context + target])
        with torch.no_grad():
            logits = self._model(ids).logits[0]
        logp = torch.log_softmax(logits[len(context) - 1 : len(context) - 1 + len(target)].float(), dim=-1)
        picked = logp[torch.arange(len(target)), torch.tensor(target)]
        return float(picked.mean())

    def score(self, a: str, b: str) -> float | None:
        """log p(head of B | tail of A) − log p(head of B | nothing), per token."""
        if not self._load():
            return None
        t0 = time.time()
        tail = self._tok(a[-TAIL:], add_special_tokens=False)["input_ids"][-160:]
        head = self._tok(" " + b[:HEAD].lstrip(), add_special_tokens=False)["input_ids"][: self.head_tokens]
        blank = self._tok("\n", add_special_tokens=False)["input_ids"] or [self._tok.eos_token_id]
        if not tail or not head or blank[0] is None:
            return None
        s = self._logp(tail, head) - self._logp(blank, head)
        self.scored += 1
        self.seconds += time.time() - t0
        return round(s, 3)

    def __call__(self, a: str, b: str, ctx: dict[str, Any] | None = None) -> bool | None:
        s = self.score(a, b)
        return None if s is None else s >= self.threshold

    def summary(self) -> dict[str, Any]:
        return {"model": self.model_name, "threshold": self.threshold, "scored": self.scored, "seconds": round(self.seconds, 1), "down": self._down, "error": self.error}


# -- calibration ------------------------------------------------------------------------------------


class _Recorder:
    """A judge that answers from the store and writes down every pair it answered, so the
    pairs behind the rows can be scored."""

    def __init__(self, conn: Any, paper: str):
        self.judge = Judge(conn, paper, ask_model=False)
        self.pairs: list[tuple[str, str, bool]] = []

    def __call__(self, a: str, b: str, ctx: dict[str, Any] | None = None) -> bool | None:
        v = self.judge(a, b, ctx)
        if v is not None:
            self.pairs.append((a, b, v))
        return v


def _prf(rows: list[tuple[float, bool]], threshold: float) -> tuple[int, int, int, int]:
    tp = sum(1 for s, y in rows if y and s >= threshold)
    fp = sum(1 for s, y in rows if not y and s >= threshold)
    fn = sum(1 for s, y in rows if y and s < threshold)
    tn = sum(1 for s, y in rows if not y and s < threshold)
    return tp, fp, fn, tn


def _best_threshold(rows: list[tuple[float, bool]]) -> float:
    """The threshold with the best F1 on the joins, among the scores seen."""
    best, at = -1.0, THRESHOLD
    for th in sorted({s for s, _ in rows}):
        tp, fp, fn, _ = _prf(rows, th)
        f1 = 2 * tp / max(2 * tp + fp + fn, 1)
        if f1 > best:
            best, at = f1, th
    return at


_SENTENCE_CUT = re.compile(r"(?<=[a-z])\s+(?=[A-Z0-9(][A-Za-z0-9]*\s)")  # after a word, before a capitalised or numbered one: the shape the rules leave open


def synthetic_pairs(libs: list[Path], n: int, seed: int = 7) -> dict[str, list[tuple[str, str, bool]]]:
    """Pairs whose answer is known without a judge, made from the corpus's own paragraphs.
    A paragraph cut mid-sentence before a capitalised or numbered word gives the head of A
    and, as B, either the rest of its own sentence (one paragraph: True) or the start of
    another paragraph of the same paper (two paragraphs: False) — and only pairs
    `tree._judge_candidate` would put to the judge are kept, so the numbers describe the
    pairs the threshold guards. Keyed by paper, for paper-out calibration."""
    import random
    import sqlite3

    from .tree import _judge_candidate

    rng = random.Random(seed)
    out: dict[str, list[tuple[str, str, bool]]] = {}
    for lib in libs:
        conn = sqlite3.connect(lib / "store.sqlite")
        rows = conn.execute("SELECT paper, text FROM nodes WHERE type = 'paragraph' AND role IN ('introduction', 'methods', 'results', 'results-discussion', 'discussion') AND length(text) BETWEEN 400 AND 2000 ORDER BY paper, ordinal").fetchall()
        conn.close()
        by_paper: dict[str, list[str]] = {}
        for paper, text in rows:
            by_paper.setdefault(paper, []).append(" ".join(text.split()))
        for paper, paras in by_paper.items():
            if len(paras) < 3:
                continue
            for text in paras:
                cuts = [m for m in _SENTENCE_CUT.finditer(text) if 120 < m.start() < len(text) - 120]
                if not cuts:
                    continue
                m = rng.choice(cuts)
                head, rest = text[: m.start()], text[m.end() :]
                if len(head.split()) < 12 or len(rest.split()) < 8:
                    continue
                other = rng.choice([p for p in paras if p is not text])
                if _judge_candidate(head, rest) and _judge_candidate(head, other):
                    out.setdefault(paper, []).append((head, rest, True))
                    out.setdefault(paper, []).append((head, other, False))
    papers = list(out)
    rng.shuffle(papers)
    picked: dict[str, list[tuple[str, str, bool]]] = {}
    total = 0
    for paper in papers:
        if total >= n:
            break
        picked[paper] = out[paper][:6]
        total += len(picked[paper])
    return picked


def calibrate(libs: list[Path], model: str | None = None, show: bool = False, synthetic: int = 0) -> int:
    from .recover import recover_from_pdf
    from .store import open_store
    from .tree import build_tree

    scorer = BoundaryScorer(model)
    if not scorer.available():
        print(f"no model: {scorer.error}", file=sys.stderr)
        return 2
    by_paper: dict[str, list[tuple[float, bool]]] = {}
    t0 = time.time()
    if synthetic:
        pairs = synthetic_pairs(libs, synthetic)
        for key, ps in pairs.items():
            for a, b, same in ps:
                s = scorer.score(a, b)
                if s is not None:
                    by_paper.setdefault(key, []).append((s, same))
                    if show:
                        print(f"  {'JOIN' if same else 'keep'} {s:>7.3f}  …{a[-60:]!r} || {b[:60]!r}…")
        what = "synthetic pairs"
    else:
        for lib in libs:
            conn = open_store(lib / "store.sqlite")
            for row in parsed_papers(lib):
                rec = _Recorder(conn, row["key"])
                doc = json.loads(row["raw"].read_text("utf-8"))
                recover_from_pdf(doc, row["source"] if row["format"] == "pdf" else None)
                build_tree(doc, row["key"], judge=rec)
                for a, b, same in rec.pairs:
                    s = scorer.score(a, b)
                    if s is not None:
                        by_paper.setdefault(row["key"], []).append((s, same))
                        if show:
                            print(f"  {'JOIN' if same else 'keep'} {s:>7.3f}  …{a[-60:]!r} || {b[:60]!r}…")
            conn.close()
        what = "judged pairs"
    rows = [r for rs in by_paper.values() for r in rs]
    if not rows:
        print("no pairs to score: run the judge first (npm run judge), or pass --synthetic N")
        return 1
    joins = sum(1 for _, y in rows if y)
    print(f"{len(rows)} {what} in {len(by_paper)} papers, {joins} joins · {round(time.time() - t0)}s · {scorer.model_name}")
    for label, want in (("joins", True), ("keeps", False)):
        ss = sorted(s for s, y in rows if y is want)
        if ss:
            print(f"  {label:<5} score p10/p50/p90 {ss[len(ss) // 10]:.3f} / {ss[len(ss) // 2]:.3f} / {ss[9 * len(ss) // 10]:.3f}")
    print("  threshold   precision  recall   tp  fp  fn  tn")
    for th in (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0):
        tp, fp, fn, tn = _prf(rows, th)
        print(f"  {th:>9.2f}   {tp / max(tp + fp, 1):>9.2f}  {tp / max(tp + fn, 1):>6.2f}  {tp:>3} {fp:>3} {fn:>3} {tn:>3}")
    # paper-out: the threshold chosen on every other paper, judged on this one
    tp = fp = fn = tn = 0
    for key, rs in by_paper.items():
        others = [r for k, rest in by_paper.items() if k != key for r in rest]
        th = _best_threshold(others) if others else THRESHOLD
        a, b, c, d = _prf(rs, th)
        tp, fp, fn, tn = tp + a, fp + b, fn + c, tn + d
    p, r = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    ci = 1.96 * math.sqrt(max(r * (1 - r), 0.01) / max(joins, 1))
    print(f"  paper-out: precision {p:.2f} recall {r:.2f} (±{ci:.2f} at n={joins})  tp {tp} fp {fp} fn {fn} tn {tn}")
    print(f"  in-sample best threshold {_best_threshold(rows)}; gate is recall ≥ 0.8 at precision ≥ 0.8")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="litrag_parser.boundary", description=__doc__.split("\n\n")[0])
    ap.add_argument("--lib", action="append", default=[], help="a library directory (repeatable)")
    ap.add_argument("--calibrate", action="store_true", help="score every pair the judge ruled on and report")
    ap.add_argument("--model")
    ap.add_argument("--show", action="store_true", help="print every pair with its score")
    ap.add_argument("--synthetic", type=int, default=0, metavar="N", help="score about N pairs cut from the corpus's own paragraphs, whose answer is known, instead of the judged pairs")
    args = ap.parse_args(argv)
    if args.calibrate:
        return calibrate([Path(l).expanduser() for l in args.lib], args.model, args.show, args.synthetic)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
