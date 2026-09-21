# Campaign status

**Read order after any fresh start or compaction:** `campaign/PROMPT.md` (the
brief), `campaign/PLAN.md` (what to build), then this file. Then carry on.

- **Phase:** 0 (orientation and baseline) — **gate passed**, see below.
- **Branch:** `campaign/dynamic-reader`, cut from `8fae8aa`
  (`origin/claude/decisions-by-meaning`, the merge commit of PR #20). See
  `DECISIONS.md` D1.
- **Campaign root:** `~/.protracker/campaign/library` — copies of all eleven
  libraries plus `lanes.sqlite`. **Every command sets
  `LITRAG_ROOT=C:/Users/ihave/.protracker/campaign/library`.** Karim's own root
  is never written to; `campaign/karim-root-digest.json` proves it.
- **Scratch scripts:** `~/.protracker/campaign/scripts` (inherited from the
  previous rounds' `measurements/*/scripts`, plus mine).
- **Run outputs:** `~/.protracker/campaign/runs` (outside the repo, as the house
  style requires).

---

## Phase 0 gate: passed

`npm run check:all` green at the base commit (219 parser tests, 10 app tests,
CLI typecheck + tests). Every headline number the brief asked me to reproduce
reproduces **exactly**, on copies of the libraries:

| Figure | Brief / `NOTES.md` | Reproduced |
|---|---|---|
| Tuned pairs, n | 199 | **199** |
| faithful by words | 0.9730 | **0.97300** |
| mean faithful | 0.96747 | **0.96747** |
| mean precision | 0.97630 | **0.97630** |
| well matched | 185 | **185** |
| held-out 3, one chunk right lane | 5,195 / 89.4% | **5,195 / 89.4%** |
| held-out 3, right lane at all | 5,427 / 93.4% | **5,427 / 93.4%** |
| held-out 3, XML chunks | 5,808 | **5,808** |
| ingestion (words held anywhere) | 0.9995 | **0.9995** of 721,848 |

Ledger lines: `campaign/LEDGER.jsonl`, phase 0, names `checkall`,
`pairs-199-tuned`, `chunks-heldout3`, `ingested-heldout3`.

---

## The environment, as found

| | |
|---|---|
| OS | Windows 11 Home 10.0.26200, PowerShell + Git Bash |
| GPU | RTX 5080 (16 GB) |
| Python | 3.13.5, `uv` at the hermes path |
| Docling | 2.126.0 |
| Node | present; `npm run check:all` works |
| Ollama | **up**, 0.34.0, at 127.0.0.1:11434 |
| Ollama models | `qwen3:14b`, `qwen3:8b`, `qwen3:4b`, `gemma3:12b`, `llama3.1:8b`, `qwen2.5:7b`, `qwen3-ptingest`, **`nomic-embed-text`** |
| Java | present | 
| Docker | present (not yet used; a heavy dependency — would be a `DECISIONS.md` entry) |
| Free disk | 254 GB at start; campaign root costs 3.5 GB |
| Network | Europe PMC reachable (the previous rounds fetched from it) |

**Caution:** Bash heredocs mangle backslashes and non-ASCII on this machine —
write Python via `Write`/`Edit`, not heredocs. And **MSYS paths (`/c/...`) do not
survive into Python**: a `--json /c/Users/...` argument silently writes to
`C:\c\Users\...`. Always pass Windows paths (`C:/Users/...`) to Python.

---

## Plan-vs-code discrepancies found so far

Recorded per `PROMPT.md`: where the plan and the code disagree, the code wins.

1. **`claude/decisions-by-meaning` is not behind — it is the integration
   branch.** PR #20 merged into it, not into `main`. `main` does not contain the
   reader being measured. (`DECISIONS.md` D1.)

2. **There is no "chunk" in `pairs.py`.** `PLAN.md` T2 is phrased in chunks; the
   parser's unit is `pairs.Unit` (a text-bearing tree node). The chunk numbers in
   the brief come from `chunkreport.py`, a *scratch script outside the repo*,
   where a chunk is a paragraph node. Any T2 harness must define its unit
   explicitly and carry `chunkreport`'s definition forward, or the campaign's
   numbers will not be comparable with the brief's.

3. **`PLAN.md` T2 says "matches the witness under `pairs.py`'s `placed`
   definition" — but `placed` is not a lane metric.** In `pairs.py:343`, `placed`
   compares the **top-level section heading** (`_same_section`, Jaccard ≥ 0.8);
   the *lane* metric is `faithful` (`pairs.py:342`). They are materially
   different: two methods subsections are the same lane but different sections.
   **T2 will be measured on lane agreement, and `placed` reported beside it.**

4. **`PLAN.md` Phase 1's stated crash hypothesis is refuted.** It guesses "the
   worker answers reads on the main thread while an ingest thread runs Docling".
   No read op touches pdfium at all — every litrag pdfium call site is on the
   ingest thread (`worker.py:123,145,211`; `recover.py:83`; `typography.py:125`;
   `harness.py:67`). A better-supported hypothesis is in "Phase 1 leads" below.

5. **`PLAN.md` says "the raw Docling document is already written before any
   row".** True of the *node* rows; **false of the `papers` row**, which is
   INSERTed at `worker.py:347` with `status='queued'` before any parsing. That
   inversion is what makes the skip logic unsafe.

6. **`LITRAG_REVIEW` currently gates nothing in the ingest/rebuild path.**
   `review.judge` is called from nowhere in `worker.py` or `harness.py`; it is
   reachable only through `python -m litrag_parser.review`.

---

## What the code actually is (the orientation that matters)

**Where the publisher-keying lives, precisely.** This is the central finding of
Phase 0 and it is better news than the brief assumed. Roughly **150 literal
publisher tokens sit in eight constants, all in the front-matter path of
`tree.py`** — `_FURNITURE` (`:1106`), `_LICENCE` (`:1368`), `_GENERIC_LABELS`
(`:1105`), `_FRONT_LABEL` (`:1252`), `_MESSAGE_BOX` (`:1478`),
`_ABSTRACT_PART_WIDE` (`:1517`), `_CITE_LINE` (`:1206`), `_EDITOR_LINE`
(`:1154`), plus `_ABSTRACT_BLOCK` (`:1234`) and `_WRAPPER_HEADINGS` (`:1235`).
Everything else — the geometry, the column and reading-order repair, the
stitching, `infer_level`, `depth_by_type` — is already document-relative.
`glyphs.py` `RULES` is a second, smaller publisher-keyed cluster (Symbol-font
maps named for Wiley/Elsevier/RSC/Hindawi).

So "replace rules keyed to publishers with a reader that induces the document's
own template" is mostly **one classifier's worth of work — the front-matter
classifier — not a rewrite of the reader.** That is where Phase 3's template
induction should aim first, and it is where the measured familiar-vs-novel gap
(0.966 → 0.906) most plausibly comes from.

**The oracle.** `meaning.py` is the only module that talks to Ollama
(`nomic-embed-text`, `/api/embed`). Ten *kinds*, each with a threshold and a
margin; below either, the answer is `other` — that is the single abstention
point for every learned question. Verdicts are rows in `<root>/lanes.sqlite`
keyed by `model@kind.signature()`, so editing a kind's examples or thresholds
re-asks once. **107,771 verdicts** are already stored, which is why `rebuild`
usually needs no model.

**Lane precedence** (`tree.py:2045-2059`): `facets.role_of` exact regex →
`headings.top_level_lane` (catalogue) → the embedder → a `_NOT_NUMBERED` veto →
structured-abstract override → built-heading lane. Deeper sections inherit;
`structure.lane_sections` is a post-pass that may relane a top-level `other`
section in a research-like paper only.

---

## Phase 1 leads (reliability), ranked by fit to "intermittent, PDF path only"

1. **GC-time `FPDF_CloseDocument` on an arbitrary thread.** `sniff_ids`
   (`worker.py:130-131`) and `guess_title` (`worker.py:167-168`) leak a
   `PdfDocument` on their exception paths; `harness.pdf_title`
   (`harness.py:66-72`) has the same shape. pypdfium2 closes via
   `weakref.finalize`, so the native close runs whenever the collector next
   runs — possibly inside a Docling parser thread that is concurrently in
   pdfium. litrag's own pdfium calls **never take Docling's
   `pypdfium2_lock`**. This is exactly the shape of an intermittent 0xC0000005.
2. **A textpage outliving its page**: `tp = pdf[0].get_textpage()` where `pdf[0]`
   is a temporary (`worker.py:152`, `:128`; `harness.py:148`), then `get_charbox`
   in a loop.
3. The recorded PNAS crash is *inside* `converter.convert` (Docling's own
   threads), where litrag holds no handle — so it is a second, independent
   source, not explained by 1 or 2.

**The silent-failure hole, confirmed.** `do_ingest`'s skip predicate
(`worker.py:350`) is `existed and status=='parsed' and file` — **no node count,
no check that `parsed/<key>.docling.json` exists**. And `rebuild` only visits
`status='parsed'` rows with a raw file (`library.py:49,57-59`), so a paper stuck
at `'parsing'` after a native crash is invisible to `rebuild` **forever**.
Nothing ever resets `'parsing'`. The app never sends `reread` and has no
per-paper reparse. Also `do_ingest` reports every filed key in `done.parsed`
regardless of outcome (`worker.py:373`), because the list is built before
`parse_one` runs.

---

## In flight

- `tieprobe.py` running over all five pair sets: counting how often
  `landed.most_common(1)` has a tie at the top, which is the one place a
  `frozenset` iteration order could reach a metric. Two-seed comparison on
  held-out 1 (33 pairs) already showed **zero** differing cells.

## Next three actions

1. Finish the determinism finding (tie probe), and write
   `campaign/reports/phase0.md`.
2. Commit Phase 0 (campaign scaffolding only — no code change yet) and open the
   draft PR for it.
3. Start Phase 1 (reliability) and, **in parallel**, begin the Phase 2 corpus
   fetch — it is the long pole and `BACKLOG.md` already specifies it: thirty-plus
   DOI prefixes none of the libraries hold, weighted to editorials and letters,
   built by adapting `fetch_heldout4.py` to query by `PUBLISHER:`/prefix instead
   of topic.

## Open questions

- None blocking. `DECISIONS.md` holds the three reversible defaults taken so far.

## Assets inherited from previous rounds (do not rebuild from scratch)

- `~/.protracker/campaign/scripts/`: `chunkreport.py` (the chunk metric),
  `ingested.py` (T1's ancestor), `fastpairs.py`, `fetch_heldout4.py` (the Europe
  PMC pair fetcher), `ledger.py`, `residue2.py`, `cutkinds.py`, `joinreach.py`,
  `proseheads.py`, `latefurniture.py`, `missing_layer.py`, `repair_heldout4.py`,
  `working-half.txt` (held-out 3's dealt halves).
- **`held-out-4` (161 pairs, 110 journals, 2025-26) is already fetched, ingested
  and never diagnosed.** It is the closest thing to an unspent exam already in
  hand — but 92% of held-out 4 comes from publisher prefixes the rules were
  already fitted on, so it is *not* a novel-publisher set. Treat it as a second
  novel-**paper** set, not as EXAM.
