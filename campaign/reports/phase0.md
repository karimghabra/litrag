# Phase 0 — orientation and baseline

**Gate: passed.** `npm run check:all` is green at the base commit and every
headline number the brief asked for reproduces exactly. One real defect was found
while proving it and is fixed here.

Base commit `8fae8aa` (`origin/claude/decisions-by-meaning`, the merge of PR #20).
Branch `campaign/dynamic-reader`. Ledger: `campaign/LEDGER.jsonl`, phase 0.

---

## 1. The baseline reproduces

Measured on byte-identical copies of the eleven libraries under
`~/.protracker/campaign/library` (`DECISIONS.md` D2), with
`LITRAG_ROOT` pointed there. Ollama up, `nomic-embed-text` answering.

| Figure | `NOTES.md` / the brief | Reproduced | |
|---|---|---|---|
| Tuned pairs, n | 199 | 199 | ✔ |
| faithful by words | 0.9730 | 0.97300 | ✔ |
| mean faithful | 0.96747 | 0.96747 | ✔ |
| mean precision | 0.97630 | 0.97630 | ✔ |
| well matched (faithful ≥ 0.9 **and** precision ≥ 0.9) | 185 | 185 | ✔ |
| held-out 3 papers | 127 | 127 | ✔ |
| held-out 3 XML chunks | 5,808 | 5,808 | ✔ |
| one chunk, right lane | 5,195 (89.4%) | 5,195 (89.4%) | ✔ |
| in the right lane at all | 5,427 (93.4%) | 5,427 (93.4%) | ✔ |
| ingestion, words held anywhere | 0.9995 | 0.9995 of 721,848 | ✔ |

Per corpus, faithful by words: pilot 0.9802 (31), held-out 1 0.9768 (33),
held-out 2 0.9689 (135).

`npm run check:all`: green. 219 parser tests at the base commit, **221** after
this phase's two new tests.

## 2. A defect found while proving it: the metric was not deterministic

`pairs.Unit.shingles` is a `frozenset[str]`. `_land` fills a `Counter` by
iterating it, so the Counter's **insertion order follows `PYTHONHASHSEED`**.
`Counter.most_common(1)` breaks a tie by insertion order, and that tie decides
`top` — which paragraph of the PDF a piece of the XML is judged to lie in. `top`
feeds `held_top`, which decides **intact vs split**, and the `dominant` map that
produces **merged**.

Rather than sample two seeds and hope, I counted the ties directly
(`tieprobe.py`, over all five pair sets):

| set | XML prose units | ties at the top | …between units that disagree on the lane |
|---|---|---|---|
| pilot + held-out 1 | 2,956 | 1 | 1 |
| held-out 2 | 5,382 | 5 | 2 |
| held-out 3 | 5,808 | 3 | 2 |
| held-out 4 | 6,993 | 6 | 3 |
| **all** | **21,139** | **15** | **8** |

Eight units in 21,139 — **0.04%** — could be judged differently by two runs of
identical code. That is negligible against today's 93.4% and **not** negligible
against a 99.9% bar, where it is a twenty-fifth of the whole error budget. It
also silently corrupts any A/B between two reader versions.

**Fixed** in `parser/litrag_parser/pairs.py`: a `_top()` helper breaks the tie the
same way `_land` already breaks its own (most shingles, then the earliest unit),
and `_ranked()` does the same for the four reported counters (`lane_only`,
`landed`, `confusion`, `split_reasons`), whose key order was equally
order-dependent. Two tests pin both.

**Verification.** Three corpora scored at `PYTHONHASHSEED=0` and `=999`:
**0 differing cells** across all 199 pairs × 30 fields. Every baseline number
above is unchanged by the fix, and `chunkreport` on held-out 3 is unchanged to
the chunk.

This is the first of pre-return check #1's "hunt the nondeterminism", done before
any number was quoted rather than after.

## 3. What the code is, where it differs from the plan

Six plan-vs-code discrepancies are recorded in `STATUS.md`. The three that change
what gets built:

1. **`PLAN.md` T2 keys on `pairs.py`'s `placed`, but `placed` is not a lane
   metric** — it compares the top-level *section heading* (`pairs.py:343`,
   Jaccard ≥ 0.8). The lane metric is `faithful` (`:342`). T2 will be measured on
   lane agreement, with `placed` reported beside it.
2. **There is no "chunk" in the parser.** The brief's chunk numbers come from
   `chunkreport.py`, a scratch script outside the repo. Its definition (a chunk
   is a paragraph node) must be carried into the campaign harness verbatim, or
   the new numbers will not be comparable with the brief's.
3. **Phase 1's stated crash hypothesis is refuted** — no read op touches pdfium.
   A better-supported hypothesis, and the confirmed silent-failure hole, are in
   `STATUS.md`.

## 4. The finding that should shape Phases 3–5

The publisher-keying is **not** spread through the reader. It is concentrated:
roughly 150 literal publisher tokens in eight constants, and all eight are in
`tree.py`'s **front-matter path** (`_FURNITURE`, `_LICENCE`, `_GENERIC_LABELS`,
`_FRONT_LABEL`, `_MESSAGE_BOX`, `_ABSTRACT_PART_WIDE`, `_CITE_LINE`,
`_EDITOR_LINE`, plus `_ABSTRACT_BLOCK` and `_WRAPPER_HEADINGS`). `glyphs.py`'s
Symbol-font `RULES` is a second, smaller cluster.

Everything else the reader does — the column and reading-order repair, the
stitching, `infer_level`, `typography.depth_by_type`, the geometry — is already
keyed to the document itself. The repo got there first; it simply never said so
in one place.

That reframes the week's target. "A reader that adapts to each document instead
of accumulating rules per publisher" is mostly **one classifier's worth of
work** — the front-matter classifier — not a rewrite. It is also the most likely
seat of the measured familiar-vs-novel gap (0.966 → 0.906), because front matter
is exactly what a publisher sets its own way, and `NOTES.md` already records that
the whole junk-chunk class was publisher furniture read as body prose.

Phase 2 will test that attribution before Phase 3 acts on it: the per-rule
precision table by familiarity will say how much of the gap the front-matter path
actually owns.

## 5. Assets inherited, not rebuilt

`held-out-4` — 161 pairs, 110 journals, 2025–26, ingested and **never
diagnosed**. But 92% of it comes from DOI prefixes the rules were already fitted
on, so it is a novel-*paper* set, not a novel-*publisher* one. It is not EXAM.

`~/.protracker/campaign/scripts/` carries forward `chunkreport.py`,
`ingested.py`, `fastpairs.py`, `fetch_heldout4.py` (the Europe PMC pair fetcher
Phase 2 will adapt to sample by publisher), and the refusal probes
(`joinreach.py`, `proseheads.py`, `latefurniture.py`, `tablenotes.py`).

## 6. Karim's libraries were not written to

Fingerprinted before any command ran: 3,354 files, size and mtime, in
`campaign/karim-root-digest.json`. Re-checked at this gate: **unchanged**.
