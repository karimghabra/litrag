# Campaign status

**Read order after any fresh start or compaction:** `campaign/PROMPT.md` (the
brief), `campaign/PLAN.md` (what to build), then this file. Then carry on.

- **Phase:** 4 (closed-loop reading) — **gate not met, and the reason is the
  finding**: thirteen invariants are built and priced on DEV, and none reaches a
  precision a deterministic repair could use, so all thirteen stay advisory and
  the repair loop was not built. `reports/phase4.md`. Phase 0 **gate passed**;
  Phase 1 (reliability) landed; Phase 2 (the measurement apparatus) and Phase 3
  (one reader change, the abstract's end) landed. Phases 5-8 not started; VAL,
  SEALED and EXAM never scored.
- **Branch:** `campaign/phase4-closed-loop`, cut from `campaign/dynamic-reader`,
  itself cut from `8fae8aa` (`origin/claude/decisions-by-meaning`, the merge
  commit of PR #20). See `DECISIONS.md` D1.
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

### Operational gotchas on this machine, each learned the hard way

1. **Bash heredocs mangle backslashes and non-ASCII.** An escaped newline inside a
   heredoc'd Python string became a real newline and broke a file mid-run; a
   heredoc containing an em dash fails to parse at all. Write Python with
   `Write`/`Edit`, or to a script file — not through a heredoc.
2. **MSYS paths do not survive into Python.** `--json /c/Users/...` silently
   writes to `C:\c\Users\...`. Always pass Windows paths (`C:/Users/...`).
3. **`nohup ... &` from the Bash tool does not survive.** Three long jobs were
   killed this way (a 25-PDF validation, a recon wave, a corpus fetch), each
   leaving a half-finished result that looked like a finding. Use the Bash tool's
   own `run_in_background: true`, which the harness tracks.
4. **Never pipe a background job to `tail`.** Nothing is written until the job
   ends, so it looks hung. Redirect to a file and tail the file.
5. **`pkill -f <script>` is too blunt here** — it took out an unrelated job.

Gotcha 3 cost a wrong conclusion once: a run that was *killed* looks exactly like
a run that *crashed* — a paper left `parsing`, the rest `queued`. Any claim about
the native crash has to rest on a tracked run.

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

## Phase 1 (reliability) — what landed

Commit `28db03f`. Three faults, each of which cost a whole run rather than one paper.

1. **The layout runs in a child process the worker supervises** (`layout.py`):
   per-paper timeout, respawn on death, one retry in a fresh child, then the
   paper is raised with a reason. Long-lived, because Docling takes seconds to
   build. `LITRAG_LAYOUT_CHILD=off` is the way back. Nine fault-injection tests
   drive a stub child that fails on demand, including a **real** access violation
   (a bad pointer — `ctypes.string_at(0)` raises a catchable `OSError` and would
   have tested nothing).
2. **Every pdfium handle goes through `recover.open_pdf`.** Five call sites leaked
   a document on their exception path; pypdfium2 closes what it still holds at GC
   time, which can be inside a Docling parser thread that is itself in pdfium, and
   litrag never takes Docling's lock. The 199 tuned pairs differ in **no cell**
   after the change.
3. **"Already read" is now a fact about the tree**, not about the `papers` row:
   nodes exist *and* the raw Docling document exists. A paper left `parsing` by a
   dead worker is given `failed` and a reason when its library is next opened, and
   `harness.unread_papers` names every paper it is passing over.

**Still open from Phase 1:** the two-full-pass gate (runs on the new corpus, Phase
2); and `quit` is `os._exit`, so a queued-but-unstarted paper is silently
abandoned — it is *reported* by `unread_papers` and rescued by `reparse`, but the
worker does not drain its queue. Noted for `BACKLOG.md`.

**Measured on the way:** Docling's layout is **not** bit-reproducible — six
conversions of one PDF gave two distinct documents — but the difference is
confined to `pictures` bounding boxes at ~1e-4 pt; `texts` and `tables` are
identical across all six, and node counts never moved. (`DECISIONS.md` D5.)

---

## Phase 2 (the measurement apparatus) — in flight

**The corpus is the thing `BACKLOG.md` asked for and no previous set was**: drawn
by publisher, not by topic.

- `fitted.py` → `campaign/fitted-publishers.json`: **87 DOI registrant prefixes
  over 1,371 papers** are what the reader was built on. Five of them are half of
  it (10.3390 MDPI 278, 10.1038 136, 10.1016 120, 10.3389 115, 10.1002 109).
  Novel = a prefix not in these 87.
- `recon.py` → `corpus/pool.json`: 106 queries (96 ordinary subject terms across
  the whole of biomedicine and its neighbours, plus 8 `PUB_TYPE` queries for the
  kinds the reader reads worst), 3 pages of 100 each, metadata only.
  **29,270 papers over 426 prefixes, 351 of them novel.**
- `manifest.py` → `campaign/corpus-manifest.json`: split **by a hash of the
  prefix** and a fixed salt — recomputable, order-free, cannot drift.

| split | papers | publishers | weak kinds |
|---|---|---|---|
| DEV | 236 | 113 | 41% |
| VAL | 155 | 80 | 32% |
| SEALED | 174 | 76 | 33% |
| EXAM | 125 | 56 | 37% |
| RESERVE | 31 | 13 | 19% |

`PLAN.md` gives DEV/VAL/SEALED as 40/30/30 but also wants EXAM drawn from
publishers in none of them; those cannot both hold, so the shares are
35/25/20/15/5 and EXAM is **defined now and not fetched until Phase 8**.

**A real obstacle, being handled:** `HAS_PDF:y` in Europe PMC's index does **not**
mean EBI's bulk open-access area holds a PDF. On a first sample five of eight
candidates 404'd on the bulk zip, on the REST PDF route, and the publisher is not
to be scraped. A witness needs both formats, so `probe.py` HEAD-checks every one
of the 1,830 novel-prefix candidates *before* the manifest is fixed — finding this
out afterwards would silently shrink whichever split the misses fell in.

## What T1 actually says, and one claim to correct

Measured on the pilot's twenty papers, after the line-break mend and the
running-head records: **accounted 0.811** — 0.794 inside a node, 0.016 inside a
dropped record. Before those two fixes it read 0.788. On the 487 legacy papers,
before them, it read 0.959 with the alphabetic-only tokeniser; the number is not
comparable across those changes and the ledger says which is which.

So T1 is a long way from 0.999, and **where the other 19% goes is not yet known.**
Two attributions were tried and neither survived:

1. *"It is mostly text inside figures."* Stated in commit `e25db5d`'s message.
   **Wrong** — measured, only 0.7% of unaccounted words lie inside a picture box.
2. *"It is mostly text inside tables."* 54% of unaccounted words do lie inside a
   table's box — but **46% of table nodes cover more than half their page**, so
   that test catches the body text on the same page and says nothing.

What *is* solid is the shape: **71% of unaccounted words are on lines of one or
two words**, about 2,000 such lines a paper. That is the signature of figure and
table interiors — axis ticks, panel letters, column heads — each its own visual
row. Naming it properly needs a containment test that is not defeated by a
page-sized table box, which is Phase 3/4 work, not Phase 2's.

The lesson worth keeping: both wrong attributions were plausible and both took one
measurement to refuse. The classifier's *shape* evidence was reliable; the
*spatial* evidence was not, and the difference was not visible until it was
checked.

## The per-rule table, and a third wrong claim caught

`ruletable.py` prices every rule that takes a block out of the body: the claim
each makes is *this block is not the paper's prose*, and the witness says whether
that is true. On the 487 fitted papers:

| rule | fires | right | body words lost |
|---|---|---|---|
| `dropped:running` | 1,986 | 0.971 | 647 |
| `front:affiliations` | 479 | 0.950 | 955 |
| `front:correspondence` | 417 | 0.959 | 659 |
| `front:authors` | 393 | 0.995 | 21 |
| `front:notice` | 332 | 0.967 | 866 |
| **`front:keywords`** | 272 | **0.368** | 2,644 |
| `front:dates` | 268 | 0.989 | 71 |
| `dropped:label` | 181 | 0.961 | 140 |
| **`front:other`** | 155 | **0.632** | 3,003 |
| `dropped:furniture` | 69 | 1.000 | 0 |
| `front:funding` | 25 | 0.960 | 62 |

`front:keywords` at 0.368 looked like the worst rule the reader has. **It is not a
reader error at all.** The examples it "loses" read `Keywords: Stem cell,
Mesenchymal stromal cell, Rotator cuff…` — plainly keywords, correctly filed as
front matter. What happens is that the *XML's own reading* leaves them in the body,
so the witness holds them as prose and the test scores the removal wrong. Same for
most of `front:affiliations`.

That is the fourth claim of mine a measurement has refused, and the most
instructive: the measure was pricing **the witness's habits, not the reader's**.
The table now reports precision twice — against any prose the XML holds, and
against prose the XML holds **in a named body lane** — and the gap between them is
a first estimate of the witness's own noise on front matter.

What survives as a real defect is `front:other` (3,003 words, and its examples are
genuine body prose: `REVIEW that was used between the mid 1980s…`) and a minority
of `front:affiliations` (`METHODS: This randomized controlled clinical trial
involved 60 patients…` filed as an affiliation).

**The novel column is empty** — every one of those 487 papers is from a fitted
publisher. The contrast the week needs comes from running the same table on the
new corpus's DEV split.

## Where Phase 3 should aim, and why

Phase 0 localised the publisher-keying: ~150 literal publisher tokens in eight
constants, **all in `tree.py`'s front-matter path**. Everything else the reader
does is already keyed to the document. So the week's target is one decision, made
in one place: *is this block the paper's prose, or the publisher's furniture?*

The rules answer it today by recognising the publisher's strings — `sciencedirect`,
`licensee`, `to cite this article`, `full list of author information`. That is
right on a publisher already fitted and silent on the next one, which is exactly
the measured 0.966 → 0.906.

Four document-derived signals can answer the same question without naming anyone,
and three of them the repo already computes:

1. **Recurrence** — furniture repeats across pages. `_recurring_furniture` and
   `_repeated_short` already do this.
2. **Typography relative to the paper's own body** — furniture is set apart.
   `typography.py` already measures every row against `body_style`.
3. **The paper's own metadata record** — `record.py` already fetches title,
   authors, journal and year from Europe PMC. A line that *matches the record* is
   front matter by identity rather than by a publisher's string, and identity
   transfers to every publisher. This is the biggest single replacement available:
   most of `_FURNITURE`'s job is recognising the journal name, the citation line
   and the author block, and all three are in the record. `_journal_name`
   (`tree.py:1259`) already does a sliver of it.
4. **Genre vocabulary** — `Keywords`, `Funding`, `Conflicts of interest`. Already
   genre-keyed, and transfers.

A caution worth stating before measuring: `_LICENCE` may not be publisher-keyed at
all. Creative Commons wording is standardised across publishers, so it is closer
to genre boilerplate than to a house style, and it may already transfer. The
per-rule table (`ruletable.py`) is what will say, rule by rule, rather than a
guess — it prices each removal against the witness, split by familiarity.

## The corpus, as built (Phase 2)

Fetched and ingesting. **336 of 340 papers landed, covering 181 of 181 novel
publishers** — the four lost had no XML. DEV 131, VAL 97, SEALED 108. EXAM (74
papers, 31 publishers) is defined by the hash and **not fetched**; it is drawn in
Phase 8.

- XML side: 335 of 336 ingested in 342 s. One failure, with a reason Docling
  gave (`doi:10.13081/kjmh.2026.35.253`) — a terminal state, which is what T4 asks
  for.
- PDF side: about 4.6 s a paper. **The native crash happened during it** — the
  layout child exited `3221226356` (`0xC0000374`, heap corruption) on
  `doi:10.1162/IMAG.a.1236`, was retried in a fresh child, and the run continued.
  That is the Phase 1 design working on real data rather than on a stub.

Libraries: `corpus-pdf` and `corpus-xml` under the campaign root. One pair holds
every split; the manifest says which paper is in which, so a split is a filter at
scoring time rather than a different shelf.

## First T2 numbers (FITTED, the publishers the reader was built on)

487 papers, 63 publishers, 21,139 witness paragraphs. Precision on asserted lanes
**0.978 micro, 0.912 macro**, 95% CI [0.959, 0.989]; coverage 0.738. The
micro-macro gap is the thing to watch — it says small publishers do much worse,
which is what T3 is about.

**That run also exposed a structural fault in the metric**, since fixed: a quarter
of the witness's own paragraphs are laned `other`, and on those it was
*impossible* to score correct, because asserting means naming a lane and being
correct means matching `other`. 5,280 of 21,139 paragraphs read precision 0.0 for
that reason alone. Precision is now reported twice — strict, and with the
witness's silences set aside. The FITTED ledger line from 09:45 predates the fix
and is superseded.

## Next three actions

1. Score DEV on the new corpus (`python -m litrag_parser.campaign --split DEV`),
   and re-score FITTED with the corrected metrics. Those two together are T3: the
   familiar-versus-novel comparison the whole week rests on.
2. Run `ruletable.py` on both, which prices every rule that takes a block out of
   the body against the witness, by familiarity. That is what tests Phase 0's
   attribution — that the front-matter path owns most of the gap — before Phase 3
   acts on it.
3. Write `campaign/reports/phase2.md` and close the phase gate.

## Open questions

- None blocking. Five reversible defaults are in `DECISIONS.md`.

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

---

## Phase 4: closed-loop reading — gate not met

`parser/litrag_parser/invariants.py`: thirteen checks, each returning pass, fail
or not-applicable with a **location** — the thing `confidence.py`'s shares cannot
give a repair. `evaluate.node_verdicts` is the join that lets a located finding be
priced: this block of the reading, and whether the XML twin disagrees with what
was done to it, keeping lane errors, merges and splits apart.

Priced on DEV (123 papers, 66 novel publishers, 5,671 judgeable blocks), with the
lift bootstrapped over publishers and the base rate resampled inside each draw:

| | |
|---|---|
| checks built | 13, all `advisory` |
| rules whose lift interval clears 1 | **4** |
| rules that survive conditioning on the paper | **1** |
| checks precise enough to be `hard` | **0** |

The survivor is `I12 odd-one-out:paragraph-read-as-section` — text set the way its
document sets its paragraphs, read as a section heading. Lift 4.23 [1.91, 8.53]
against a wrong lane; **within-paper lift 5.85 [1.82, 17.88]** over 60 papers. But
against its own literal claim — that the block is not a heading — it scores 0.4375
against a base rate of 0.4699, lift 0.93. It finds a bad place; the heading there
is usually real. That is a question for escalation (Phase 6), not a repair.

Three ledger lines: `invariants-priced-DEV`, `invariants-within-paper-DEV`,
`spurious-headings-DEV`. Run outputs in `~/.protracker/campaign/runs/phase4/`.

**Nothing in the reader changed in this phase.** `npm run check:all` green at 321
tests (271 → 321: 49 invariant and node-verdict tests, one for heading node ids).
