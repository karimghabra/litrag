# The assistant's notebook

litrag's own memory: what an assistant living with this repository needs to
remember between sessions about the libraries, the machine, and what was
tried. It ships with the repository so every session finds it. The research
context itself — the user's program, projects, vocabulary and people — lives
in Protracker's `NOTES.md`, the notebook of the tool this one serves; read
that one before talking to the user about their work, and keep this one to
the literature.

How to maintain it: long-term memory holds what stays true (the libraries,
their query sets, the models that worked, standing decisions); short-term
memory holds the current stretch, dated, overwritten freely, promoted upward
when it turns out durable. Mark inference as inference.

---

## Long-term memory

### The libraries

- **looped-ligament** (Protracker project `n156`) — the pilot. Seeded
  2026-09-02 with three Europe PMC searches: `"electrochemically aligned
  collagen"`, `(electrocompaction OR electrocompacted OR "electrochemical
  alignment") AND collagen`, and a broad tendon/ligament/delamination
  query that pulls in off-topic reviews and should be pruned. The
  open-access half is ~78 papers; the paywalled half (~47, `lit wanted`)
  is the ELAC canon — Akkus lab 2008–2019 — and is what the crosslinking
  and delamination questions actually need.
- **looped-ligament-pairs** (made 2026-09-17, a measuring library, not a
  project's) — the open-access PDFs of 32 papers that looped-ligament holds
  as XML, from EBI's bulk area, so each can be read twice and compared
  (`pairs.py`). With `held-out-pdf`/`held-out-xml` and
  `held-out-2-pdf`/`held-out-2-xml` it is what the confidence score is
  calibrated on. It shows in the window's library list; delete it when the
  comparisons are no longer wanted.

### What worked

- **Docling 2.126** on the CPU (4 cores, no GPU): ~15 s for an 8-page and a
  14-page PDF alike once the models are loaded (~3 s), ~0.5 GB of models
  fetched from Hugging Face on the first paper. JATS through the same
  converter: 20 ms, once a JATS DOCTYPE is prepended (Europe PMC's XML has
  none and Docling's detector keys on it). Docling gives every heading
  level 1 — `heading_hierarchy_options` changes nothing on these PDFs — so
  the hierarchy is the tree builder's: numbering, then the lane vocabulary,
  then "beneath an open section means child". Two real failures found on
  the first two papers and turned into rules: a top heading merged into
  the subheading below it as a list item, and a heading echoed across a
  page break.
- Electron's binary does not download through this cloud box's proxy
  unless `ELECTRON_GET_USE_PROXY=1 GLOBAL_AGENT_HTTPS_PROXY=$HTTPS_PROXY
  NODE_EXTRA_CA_CERTS=/root/.ccr/ca-bundle.crt` are set for `npm install`.
  Karim's machine needs none of that.

- Europe PMC's JATS full text needs no PDF parsing; its text-mined terms
  give the graph real entities (genipin, ethanol, carbodiimides, rabbit)
  at no cost. Both are one REST call per paper.
- `bge-small-en-v1.5` quantised on the CPU: ~4 minutes for 4,900 chunks on
  a slow cloud box; seconds on a GPU through Ollama.
- Hybrid retrieval with the graph as a third list: on the pilot it removed
  word-match false positives and left already-good answers alone. Modest
  until the model stage adds materials and methods as entities.

### Standing decisions

- **2026-09-30** — NCBI is allowed (Karim: "yes, access ncbi"): E-utilities'
  `efetch` by PMCID, for the author manuscripts Europe PMC will not serve.
  An identifier out, the article in; no paper text leaves. `CLAUDE.md`
  invariant 1 names it. Publishers' own text-mining APIs (Elsevier, Wiley,
  SAGE) were checked and listed in `BACKLOG.md`, and are *not* allowed by
  this: they sit behind the institution's licence and are his call.

- **2026-09-11** — Karim is "not at all attached to the current
  implementation". More than one language is fine. What he wants first is
  an app with a GUI to *see* papers being ingested and trees being built:
  Electron, with Docling doing the parsing. Revision 2 of `DESIGN.md`.
  The `lit` CLI stays in `src/` untouched until the app has its verbs.

- **2026-09-03** — The assistant lives on the user's machine for the
  literature (Karim: "you can live as an agent on my machine and utilize
  the cli for the literature RAG"). Nothing is exported for cloud sessions.
- **2026-09-03** — litrag is its own repository; Protracker's copy of the
  code is to be retired. Libraries stay under `~/.protracker/library` so
  the app finds them.
- The click stays the user's: collecting paywalled PDFs is done by a person
  under institutional access; the tool catches files, never fetches behind
  a login.

## Short-term memory

- **2026-09-30: citations and the type, ported from `claude/ingestion-generalization` — and not
  yet measured** — That branch (tip bea09b2, 19 commits of 2026-09-17/18, never a PR) forked from
  1d8b5ad, and main's `citations.py` and `paper_type.py` were still byte-identical to the fork
  point. Ported onto `claude/relaxed-pascal-mtp4qm`, one commit each:
  - **Citations** (880af6b → e55e3d9): bracket runs, caret superscripts, `_is_word` (BaTiO3 is
    no citation), true-minus and "e" ranges, entries run together split at the printed number,
    the front matter not read. One adaptation: the branch recorded a split through its
    `changes.Repairs` log, which main does not have, so its own test failed here; the count is
    now *set* on `tree.repairs["split_references"]`, because the worker links a tree and
    `confidence.py` links it again. `invariants.py`'s I5 imports `_NUMERIC`/`_expand_numeric`
    and now sees the new ranges too. The branch says a caret paper's carets are its only
    superscripts; the code it measured does not do that — the older bare-number guess still runs
    beside them, and "4 mm. 12 Samples" in a caret paper links entry 12. Left as measured, the
    comment corrected; `superscript_style = not caret_style and …` is the one-line change to try.
  - **The type** (97fc360 → b97151c): the shape answers in order (a results heading beside a
    methods or a discussion is research; short prose with neither lane is an editorial; an
    abstract and no results is a review), `SHAPE_DECIDES` gains letter and editorial, a title
    rule reads "Expression of Concern". Checked by hand: every verdict the old rules gave, the new
    ones give too; only trees that got none can gain one.
  - **Mine, not the branch's** (80ec4d0): on main, the editorial rule fired on a tree with *no
    text at all* — a title and nothing else — because main files the prose of a paper with no
    headings as front-matter `meta`, not paragraphs, so `words` is 0 for a real heading-less
    editorial and for a reading that found nothing alike. The rule now needs one line of prose
    anywhere (the `first` line the letter rule reads). Drop the commit for the branch verbatim.
  - **`_tight` only, of 9df932b** (a6c7353): the spacing Docling leaves in Wiley's and Nature's
    entries is taken out before any entry pattern reads a block, in `_infer_references`,
    `_entries_follow` and main's re-entry rule. **The `_BIB_TAIL`/`_entry_flags` half is held
    back**, measured by hand to misfile: a block that looks like an entry's tail ("et al.",
    "(2020)") carries a run without breaking it, main's `_REF_ENTRY` takes "However, Smith and
    colleagues reported in 2019" for an entry, and in an author–year paper with no References
    heading the run opened there and filed the whole discussion as references. The branch tip
    does the same. A test pins it (`test_a_discussion_that_cites_by_author_and_year_…`). Main's
    own run already bridges three such blocks (BACKLOG.md).
  **Nothing here was measured on main**: the corpora are on Karim's machine, and this was done in
  a cloud container without them. The branch's own figures — citation agreement 0.672 → 0.723
  held-out; the type cascade 0.786 → 0.927 on 248 labelled papers and 0.738 → 0.820 on four
  libraries never inspected; reference lists 0.851 → 0.970 / 0.797 → 0.939 / 0.875 → 0.945 —
  were taken on the branch's reader, with its heading-depth fixes and 9df932b's gather under
  them, on sets mostly from the publishers the rules were written from. Treat them as a ceiling.
  What each commit can move, so the before/after is read in the right place (before = a362248):
  - e55e3d9 moves `refs` and `citations` rows only — no node, no lane. `pairs.py` over the three
    pairings (`--pdf-lib looped-ligament-pairs --xml-lib looped-ligament`, `held-out-pdf`/`-xml`,
    `held-out-2-pdf`/`-xml`): `citations.ratio` and `references.ratio`. Both sides of that ratio
    go through `citations.py`, so it moves on the XML side too; the branch named link precision
    as what the ratio cannot see. Then the same over `corpus-pdf`/`corpus-xml` for publishers the
    rules never saw. The harness's `--gate` counts citations lost and gained per paper.
  - b97151c and 80ec4d0 move `papers.type` only, and with it the confidence score (a typed paper
    loses the 0.15 "type unsettled" penalty) and the canonical skeletons. `python -m
    litrag_parser.paper_type --measure --lib …` over the four XML libraries and the held-out
    ones; watch editorial precision (the branch: 1.000 → 0.846), review precision on held-out
    (0.880 there), and how many papers leave `other`. `LITRAG_OUTLINE=on` is the one way the type
    reaches a lane — a paper newly typed research gets the outline's research treatment.
  - a6c7353 moves nodes: `npm run gate:ingestion` (`python -m litrag_parser.chunks --pairs …`,
    and `--corpus DEV`/`--corpus VAL`), plus `references.ratio` above. The chunk gate should not
    move for the first three commits at all; if it does, something is wrong.
  Write the numbers here and in CHANGELOG.md's Unreleased section before merging.
  Seen on the way, not fixed: a heading-less letter or editorial links no citations on main
  (its prose is front-matter `meta`, and only paragraphs, list items, captions and footnotes are
  read for markers); and the editorial rule's 3,000-word ceiling counts paragraphs only, so it
  does not see such a paper's length at all.

- **2026-09-30: 0.3.x on `main`, NCBI, and a first run in a cloud box** — PR #23 merged
  (`a362248`): `main` had sat at 0.2.0 while v0.3.0 and v0.3.1 were released from
  `claude/studio`. Drafts #21 and #22 closed as landed. What was found and done:
  - **NCBI, measured live.** For an NIH author manuscript (PMC5653421) Europe PMC's
    `fullTextXML` answers 500; NCBI's OAI service and `efetch` both give the whole JATS (46
    paragraphs, 15 sections). OAI wraps it in the JATS 1.4 namespace, `efetch` in a bare
    `<pmc-articleset>` shaped like Europe PMC's, so `efetch` it is. A publisher's closed
    deposit (PMC9469745) came back 400 in the morning and 200 with front matter only an hour
    later: an article must carry a `<body>` to count. On `"electrochemically aligned
    collagen" AND genipin` 14 of 22 hits are author manuscripts; 19 of 22 now have XML.
  - **The real window, end to end** (Xvfb, live Europe PMC and NCBI, Docling on the CPU,
    Ollama's `nomic-embed-text`): a project made, that search run, four fetched — one from
    Europe PMC, two from NCBI, one `needs-pdf` — and an open PDF (doi:10.1002/jbm.b.35116)
    added; all four read, typed research, methods found, confidence 1.00; 174 passages
    embedded; "How was the degree of genipin crosslinking measured?" answered with the TNBS
    passage and the methods it was measured by. 3.5 minutes, no errors in the log. Collect
    PDFs opened on the closed paper and could not reach the publisher from the box.
  - **Stranded work (inference checked by `git cherry`).** `collect-mode` holds 20 CLI
    commits that never reached `main`, including the fixes for #12 and #13 and the work nine
    closed issues were closed on (#3, #5, #9–#11, #15–#18); `claude/ingestion-generalization`
    holds citation styles and paper-type shape rules `main` never got (its `citations.py`
    and `paper_type.py` are the fork point's). Both kept; `BACKLOG.md` lists them for Karim.
    The six branches `main` now contains were not deleted: the session could not delete
    remote branches. Later the same day #24 brought `collect-mode`'s fixes for #12 and #13
    and the µm/°C repair to `main`, and #25 the citation and type rules (the entry above);
    the NCBI route reached `main` after both. What is left on the two branches is in
    `BACKLOG.md`.

- **2026-09-23: the studio, and the reader judged in the unit retrieval returns** — Karim asked
  for an independent app (projects, searches, trees, types with their canonical structures, a
  query that hydrates) and a harness that uses it end to end; branch `claude/studio`,
  `app/tests/e2e/studio.spec.ts` 10 of 10 on real Docling and a real embedder. What was learned:
  - **The measure.** `chunks.py`: an XML twin's prose paragraph arriving as *one node in the
    right lane* is "ingested perfectly". Its first version counted an **echo** as a cut — a
    chunk twelve units away sharing three of a paragraph's 217 shingles made it "cut" — so v2
    ignores a far piece holding under 5%. Tuned sets (487 pairs) 89.9% on v1 were 92.1% on v2.
  - **Cuts** (the columns/pages/headings patch, 6da3d3e): 92.1 → 93.3% on the tuned sets,
    silent merges 476 → 315, 178 papers better and 12 worse; the reserved VAL 84.5 → 85.1%.
    What is left of the cuts is mostly the XML being coarser than the page (structured
    abstracts, keywords) and scattered real misses.
  - **Unseen publishers lose to lanes, not cuts.** The campaign's DEV split is 72.7%, but one
    conference supplement (doi:10.15167/2421-4248/jpmh2019.60.3s1, 691 paragraphs, 0.0) is 15% of
    it; without it 85.5%, with *one chunk in the wrong lane* at 7.6% (1.8% on the tuned sets).
  - **T1 is not lost text.** Counted as a multiset of words, 87.1% of Karim's 74 PDFs' text layer
    is accounted for; counted by *line* (half a line's 3-word shingles in some node), only 2.1% of
    the words sit in lines no node holds — 0.9% body (mostly reference-list fragments), 0.6%
    table cells, 0.3% figure labels. The word count's gap is tokens (superscripts, "fi bronectin",
    numbers), not paragraphs. Retrieval is not starved of text.
  - **Retrieval.** 14 proxy questions (written by the assistant from passages it sampled, so
    tilted towards the tree): the answer in the top 3 for 14 of 14 counting the hydrated context
    (10 at rank 1), 10 by the passage alone, against 8 for `lit query` over `lit.sqlite`.
  - **Rounds two and three, and what transfers.** Lanes (abstract ends, depth, soft hyphens, a
    JATS file's own nesting) then notes, list tails and float pages, with the XML side reading a
    structured abstract in its parts and a bold-only `<p>` as a subheading. Tuned sets 93.3 →
    94.5%, DEV without the abstract book 85.5 → 90.3%, VAL 85.1 → 87.2%. Measured by patch: the
    round-three *reader* rules left VAL exactly where it was (2,761 chunks right either way) —
    they are real on the corpora they were written from and do not reach new publishers; VAL's
    gain that round is all the XML side's. A rule built on DEV is not proven until VAL moves.
  - **More XML exists for his PDFs, off-limits so far:** 31 of the 76 PDFs are author manuscripts
    in PMC ("in PMC, not OA"); their XML is at NCBI, a host the invariant does not list.

- **2026-09-21: the reader has one mechanism for a heading's lane, wearing three hats** — the
  vocabulary's anchored regexes, the catalogue of canonical spellings and the embedder on the
  heading disagree **zero times in 434 chances** across DEV's 1,134 scoreable top-level sections.
  They are different code — regexes, a lookup table, a cosine — reading the same string, and the
  table and the centroids are both harvested from the same corpus of canonical spellings, so a
  heading any of them knows is one they all know. Every policy built on their agreement scores
  identically to the last digit because they are the same policy.

  That also re-reads the 2026-09-21 route table. Vocabulary 0.891, catalogue 0.956, embedder
  0.988 was measuring **which route got there first**, not which route was right: precedence
  hands the vocabulary every heading it recognises, so the vocabulary's column is the canonical
  headings *plus* everything odd it matches anyway, and the embedder's column is only what the
  other two had already declined. A genuinely independent opinion has to read something other
  than the heading.

  **What agreement can still mean.** Not conflict resolution — how canonical the heading is.
  Asserting only where all three recognise it: section precision 0.9380 [0.9013, 0.9668] →
  **0.9886 [0.9759, 0.9974]**, word precision 0.9759 → **0.9972**, at word coverage 0.981 →
  0.811. Sixty-six publishers never seen, clustered over publishers.

- **2026-09-21: a threshold sweep cost a re-reading of the corpus, and half of it was impossible**
  — `Kind.signature()`, the oracle's cache key, hashed the threshold and the margin along with
  the examples and the prefix. Editing a threshold did not re-decide anything; it changed the key
  and re-embedded everything. `lanes.sqlite` has **125,739 rows and up to five signatures for one
  kind**, one per time a threshold was edited. And `_decide` stored the verdict without the
  ranking, so a row that says `other` no longer knows what it refused — **90.8 per cent of every
  row ever written**. A threshold could be raised by replay and never lowered.

  Split into `Kind.space()` (what fixes the ranking: examples, centroids, prefix, prior, embedder)
  and `Kind.rule()` (threshold, margin, per-lane margins), stored beside the verdict with the top
  of the ranking. DEV re-scored afterwards is identical to the digit — 0.90115 / 0.95073 /
  0.79715 / 0.96805 — which is what a refactor should be. `LITRAG_LANES_REFRESH=on` upgrades old
  rows and is off by default because it re-embeds a library.

  **What was not wrong:** whether `meaning.py` sends nomic-embed-text its task prefix. It does,
  at every call site, and the prefix is in the cache key. That was my hypothesis and it was wrong.

- **2026-09-21: the one mechanism that could disagree with a heading is refused before it can** —
  the block classifier reads a section's *paragraphs*, which makes it the only opinion independent
  of the heading. Its scores cluster: about 0.70–0.76 against the best lane and **0.02** behind
  against the second, where the kind requires a margin of **0.08**. So `lane_sections`'s note —
  "the heading names methods, the paragraphs read as results; the heading stands" — almost never
  fires. It was also the one kind storing no ranking, because `lane_sections` builds its `Verdict`
  by hand rather than through `Oracle._decide`: the kind most in need of a sweep was the one kind
  that could not be swept. Both fixed.

- **2026-09-21: a layout can be recognised without being named** — `template.py` reads a paper's
  setting off its own type: page size, one column or two, the body's face and size, the faces that
  stand apart from it, where the type block sits. No journal name, no DOI prefix, no publisher
  string, and a test asserts the field list. On 114 DEV papers over 64 unseen publishers, against
  the publisher as a lower bound on what a template is: **AUC 0.9061**, and the nearest neighbour
  shares the publisher **71 times in 82 — 0.866 against a chance rate of 0.0147**.

  Two fixes the first measurement forced. An embedded font subset carries a random six-letter tag,
  so `FVKCKB+ArnoPro-Regular` and `VEHTVM+ArnoPro-Regular` were one typeface counted as two;
  stripping it moved AUC 0.892 → 0.906 and the nearest neighbour 0.805 → 0.866. And the exact key
  repeated **not once** across 114 papers because it hashed the type block to the thousandth of a
  page — Phase 7's "on a known fingerprint, reuse" will have to key on the distance, not the key.


- **2026-09-21: thirteen invariants, priced, and twelve of them say nothing** — the reader now
  has a module that checks its own work and answers with a *place* rather than a share
  (`invariants.py`, I1-I13, each pass/fail/not-applicable with a node, a page and the text).
  Nothing in the reader acts on any of them, and that is the measured conclusion rather than
  caution: on DEV — 123 papers, 66 publishers never seen — **none reaches a precision a
  deterministic repair could use**, so all thirteen stay `advisory` and the repair loop was not
  built. `PLAN.md` wrote that fallback itself: if invariants prove imprecise, demote them.

  **The one that survives.** Text set the way its own document sets its paragraphs, but read as
  a section heading (`I12 odd-one-out:paragraph-read-as-section`). 80 firings, 64 papers, 41
  publishers, no paper owning more than 4 per cent of them. Precision 0.225 against a base rate
  of 0.053 — **lift 4.23, 95% [1.91, 8.53]** over publishers. And conditioned on the paper it is
  in, which is the test that matters: flagged sections hold a lane error 0.189 of the time
  against 0.032 for the *other sections of the same paper* — **within-paper lift 5.85, 95%
  [1.82, 17.88]** over 60 papers. It finds a place, not just a bad paper.

  **And it does not find what it says it finds.** Its literal claim is that the block is not a
  heading at all, and the witness answers that directly: of 3,207 headings the reading prints,
  1,507 have no match in the XML (0.470); I12 flags 80 and 35 are unmatched — 0.4375, **lift
  0.93**. No better than picking a printed heading at random. Its examples are `Abstract`,
  `OBJECTIVES`, `1. Study design` — real headings, set in a face that does not stand apart, in
  papers where something near them goes wrong. So the repair it licenses is *look here*, not
  *delete this heading*, and the first number alone would have produced the second.

  **Three tests now stand between a located finding and a claim, and each killed something.**
  (1) Price the rule, not the check it lives in — I6 is three rules; I13 looked like the best
  check in the set at 0.967 and is one rule on one paper. (2) Resample the base rate **with** the
  numerator, over publishers — a bootstrapped numerator over a fixed denominator is not a lift,
  and fixing it swapped which shape of I9 survived, days before "the page break is the
  discriminator" would have been written down. (3) Condition on the paper — this killed every
  split-predicting rule, `I9 lowercase-start` included: right 77 per cent of the time about a cut
  paragraph, and within its own paper the *unflagged* blocks are right 63 per cent of the time,
  lift 1.22 [0.89, 7.13].

  **That third test answers the join classifier `PLAN.md` asks for in this phase.** Its named
  structural features — punctuation, case, column extent — carry no information about *which*
  boundary is a join once the paper is held fixed. A classifier over them would score respectably
  leave-publisher-out by learning which papers are hard, and a boundary classifier that has
  learnt the paper is a confidence score wearing the wrong name. Whether the small language
  model's likelihood carries what the structure does not is the live question, and is not
  answered.

  **The supplement, for the third time.** `10.15167/2421-4248/jpmh2019.60.3s1` is **32 per cent
  of every judgeable block in DEV** — 1,834 of 5,671 — and dropping it moves the split base rate
  from 0.393 to 0.105 and the spurious-heading count by 1,091 of 1,507. It has now falsified the
  abstract-lane finding, I13's precision and two base rates. The per-paper column is what caught
  it each time; every rule table carries the largest paper's share of its firings for that reason.


- **2026-09-21: the reader measured on publishers it has never seen** — the corpus `BACKLOG`
  asked for, drawn by publisher rather than by topic, and the first honest answer to "how far
  does this generalise". 87 DOI registrant prefixes are what the reader was built on (1,371
  papers; five prefixes are half of them). 336 papers over **181 novel publishers**, split by a
  hash of each prefix so the assignment cannot drift when the corpus grows; DEV 131, VAL 97,
  SEALED 108, EXAM 74 defined and not fetched. Two things priced before the split was fixed:
  Europe PMC's `HAS_PDF:y` is its index and not EBI's holdings (**47 per cent** of candidates
  have no PDF in the bulk area, and one format is no witness), and PMC author manuscripts share
  one NIH layout so they would have counted as a publisher.

  **T3, both columns at one commit.** Precision is of the lanes the reader asserts; the second
  row sets aside the quarter of witness paragraphs the witness itself lanes `other`, where it is
  *impossible* to be scored correct because asserting means naming a lane.

  |                                   | fitted (63 publishers) | novel (66) |
  |-----------------------------------|------------------------|------------|
  | precision, strict                 | 0.9778                 | 0.9012     |
  | precision, witness-named lanes    | 0.9855                 | 0.9507     |
  | coverage, witness-named lanes     | 0.9767                 | 0.7974     |
  | conservation of the text layer    | 0.9179                 | 0.9681     |

  **The reader already fails in the right direction**: coverage falls far harder than precision
  under shift, which is what `other`-by-default buys and what no new machinery had to provide.
  And the per-lane table says where the loss is. On publishers never seen, methods reads
  **1.000** over 827 paragraphs, results 0.990, discussion 0.990 — and abstract 0.637. The body
  transfers; the front of the paper does not. Which is where all ~150 of the reader's literal
  publisher tokens live (`_FURNITURE`, `_LICENCE`, `_FRONT_LABEL`, `_MESSAGE_BOX`,
  `_ABSTRACT_PART_WIDE`, `_CITE_LINE`, `_EDITOR_LINE`, `_GENERIC_LABELS`), all of them in
  `tree.py`'s front-matter path. Everything else the reader does is already document-relative.

  **Which rules transfer.** Every rule that takes a block out of the body, priced against the
  witness on both columns. The four that lose most are the four that match publisher strings:
  `dropped:running` 0.972 → 0.853, `front:affiliations` 0.952 → 0.838, `dropped:label` 0.961 →
  0.857, `front:notice` 0.973 → 0.910. The ones keyed to something the document supplies hold or
  improve: `front:dates` 0.989 → 1.000, `front:authors` 0.998 → 0.989, `front:correspondence`
  0.959 → 0.963. `dropped:furniture` — a box recurring at the same height on three pages, and
  nothing else — is right **every time on both columns**, which is the whole principle in one
  rule.

  **Where the gap really is: the kind of paper, not the lane.** The share of badly-read papers
  quadruples (under 0.80 precision: 4.9 per cent fitted, **19.0 per cent** novel), and it is
  concentrated in the short forms. Editorials read 0.825 fitted and **0.419** novel; seven of
  DEV's eleven are among its twenty worst papers. Research papers 0.989 → 0.930, reviews 0.952
  → 0.928. This is the cell `NOTES` already named from 20 papers — "little text to anchor on
  *and* no convention to key off" — now measured on 66 publishers.

- **2026-09-21: the abstract stops where it starts citing** — the one reader change of the
  round, and it was found by attributing every wrong assertion to the route that named its lane
  rather than by guessing at a mechanism. The vocabulary — the reader's most confident route,
  89.5 per cent of all assertions — is its **least** precise at 0.8914, against the catalogue's
  0.9560 and the embedder's 0.9882. Its errors are almost all one shape: the heading reads
  "Abstract" and is read right, and what is wrong is where the section *ends*.

  An abstract does not cite: 5.4 per cent of the paragraphs a paper really puts in its abstract
  carry a citation mark, against 49.2 per cent of the ones the reading wrongly puts there.
  Priced on the candidate set before a line was written — `it cites` 0.907 DEV / 0.938 fitted;
  `it cites and is under 150 words` 0.947 / 1.000; **`it cites and is not the first paragraph`
  32/32 and 40/40**; position alone 0.757 / 0.451; length alone 0.836 / 0.553. The last two are
  why it is a conjunction. The first paragraph is exempt because a structured abstract's lead
  can carry a trial registration or name the paper it comments on.

  Gate: 199 tuned pairs 0.97300 → 0.97335 faithful by words, well matched 185 → 186, **three
  papers better and none worse**. `LITRAG_ABSTRACT_ENDS=off` turns it off.

  **And it is worth four fixed assertions, not the thirty-two its price suggested** — ten
  paragraphs moved on four papers, DEV precision 0.8999 → 0.9012. The reason is the lesson: the
  candidate set was built from the *witness's* paragraphs landing under an abstract-laned block,
  and the rule acts on the *reader's* paragraphs that are direct prose children of an abstract
  section. Of 166 abstract sections in DEV only 51 have two or more. **Price the candidate set
  from the side the rule acts on**, or the price is an upper bound nobody told you about.

- **2026-09-21: eight claims, seven of them refused by the next measurement** — kept because the
  pattern is the finding. (1) T1 is 0.959 — no, that tokeniser ignored every digit and scored
  hyphenation as lost text, 63.6 per cent of the apparent loss; it is 0.918 fitted, 0.968 novel.
  (2) T1's residue is figure text — 0.7 per cent of it is. (3) Then it is table text — the test
  is worthless, 46 per cent of table boxes cover more than half their page. (4) `front:keywords`
  is the worst rule the reader has at 0.368 — it is not a reader error at all: the lines read
  `Keywords: Stem cell, …` and the *XML's own* reading leaves them in its body, so the measure
  was pricing the witness. (5) The abstract lane collapses on novel publishers — one conference
  supplement, 15 per cent of every paragraph in DEV, moving its coverage from 0.779 to 0.675 on
  its own. (6) Short papers fail because the geometric furniture rule needs three pages and they
  have two — true that it never fires on them (25 of 25), and the fix has **zero** candidates,
  because Docling's own `page_header` labels already removed that text. (7) The corpus had no
  Europe PMC record while the legacy libraries had one for 94-98 per cent of papers, so T3 was
  confounded — real, removed, and it changed **nothing**: DEV identical to the digit. (8) The
  vocabulary route is the least precise and its errors are one boundary — this one held.

  The two that mattered most were not wrong arithmetic. They were numbers that were correct and
  read as something else: a micro-average owned by one document, and a rule priced against the
  witness's habits. A fresh-context reviewer found a third of the same kind — the clustered
  bootstrap's test asserted the interval "reaches below 0.92", which an **unclustered** interval
  also does (0.868), so the test could not have failed.


- **2026-09-20: what "held out" is worth, measured** — Karim, on being told the reader is not a
  trained model: *"Well yes but the papers we have tested on iteratively inform the rules we
  make."* He is right, and it is the sharper frame: the rules are the parameters and the assistant
  is the optimiser, so iterating against a corpus is fitting whether or not a weight moves. What
  that costs can be measured, because the rules key on **publishers' layout conventions** — MDPI's
  left margin, RSC's author biographies, Nature's first-page abbreviations, Cureus's whole-body
  wrapper — and a publisher either has been parsed before or has not.
  Splitting the 288 pairs of the two newest sets by DOI prefix (the publisher) against the seven
  corpora the rules were written on, faithful by words:

  |                            | publisher fitted | publisher never seen |
  |----------------------------|------------------|----------------------|
  | research / review          | 232 · **0.966**  | 13 · **0.906**       |
  | editorial / letter / short | 36 · **0.933**   | 7 · **0.699**        |

  Both effects are real and they compound rather than add: a new publisher costs about six points
  on an ordinary paper, an awkward kind costs about three on a familiar publisher, and a short
  editorial from a publisher never parsed costs twenty-seven. That last cell is the genuine
  failure mode — little text to anchor on *and* no convention to key off — and it is where the two
  worst papers of the whole fourth set sit (an editorial at 0.000 and a review at 0.174).
  **So the aggregate overstates generalisation, and by how much is now known.** 92 per cent of
  held-out 4 comes from a publisher prefix the fitted corpora already contain (150 of 163; only
  13 papers across 12 new prefixes, one or two each). Its headline — 0.959 faithful, 94.8 per cent
  of chunks in the right lane — is therefore mostly a measurement of transfer to new *papers*, not
  to new *layouts*, and the 90.4 against held-out 3's 89.4 that looked like improvement on a
  harder set is inside that same easy majority. The honest summary of where the reader stands is
  two numbers, not one: **≈0.96 on a publisher it has parsed before, ≈0.91 on one it has not**,
  with n=20 across both sets for the second and the direction replicating independently in each
  (held-out 3's seven new-publisher papers 0.886, held-out 4's thirteen 0.864).
  What would actually test the reader is a set **sampled by publisher rather than by topic** —
  thirty prefixes none of the corpora hold, weighted towards editorials and letters. Every set so
  far has been drawn by subject, which is why they keep landing on the same dozen publishers.

- **2026-09-20, the fourth held-out set** — the third set's sealed half had been read, so the
  next round needed an unspent exam. `held-out-4-pdf` and `held-out-4-xml`, **161 usable pairs**
  of 166 fetched (five XML papers have no PDF partner: their PDFs were filed under a content
  hash because no DOI could be read from them, the two conference abstracts among them). 110
  distinct journals, 2025-2026, **no overlap of any kind with the five older libraries** — the
  fetch skipped 1,372 known DOIs and PMCIDs, and the result was checked against every store
  again afterwards. Thirty topics, none identical to the third set's thirty and none of them in
  tissue engineering or collagen, which is what Karim's own two libraries are; then thirty-six
  more papers asked for by *kind*, because thirty topics at four papers each fill a 130-paper
  target before the kind queries are ever reached — which is how the first attempt ended up with
  one editorial and one letter. The set is **51 per cent weak-kind labels against the third
  set's 41**: 11 editorials, 19 case reports, 6 letters, 6 comments, 6 commentaries, 95 reviews.
  Dealt A/B by the hash of the PMCID as before; 152 of the 161 carry a known half (79 sealed, 73
  working) and the nine keyed by hash do not, which `chunkreport --halves` should learn to
  resolve through the XML's PMCID rather than the PDF's key.
  **The baseline, and it is a good one.** The reader as shipped today reads 6,319 of the set's
  6,993 chunks into one chunk in the right lane (90.4 per cent) and 6,628 into the right lane at
  all (94.8) — *better* than the third set's 89.4 and 93.4, on papers from other fields with more
  of the kinds it reads worst. And the halves invert: sealed 91.1 against working 88.2, where
  held-out 3 had the sealed half 3.8 points *lower*. Two sets disagreeing on the sign of that gap
  is what a paper-level variance looks like, and it is the evidence that the third set's gap was
  not the reader having been tuned to its working half.
  **What it cost.** The machine's disk filled during the first fetch and the run died at 30
  pairs with `records.json` truncated to nothing; the PDFs survived, five were unpaired or
  truncated and were dropped, and the index was rebuilt from the files. The first rebuild
  silently indexed **1950s papers** — `EXT_ID:<digits>` matches a PubMed id, so a PMCID's digits
  fetched a different article entirely — and it was caught only because the titles were absurd.
  The lookup now asks `PMCID:` alone and refuses any hit whose PMCID is not the one asked for
  rather than falling back to the first result. Then the ingest hit the worker segfault written
  up in BACKLOG, which left 17 PDFs filed with no tree; `repair_heldout4.py` reparses until
  nothing is empty. Scripts and logs are in `measurements/2026-09-20/heldout4/`.

- **2026-09-20: the last of the furniture, and four rules the witness refused** — Karim:
  "Keep going and don't stop I want to see a real attempt for 100% ingestion." Counting in
  chunks rather than words made the remaining gap legible, because a chunk is what retrieval
  returns. On the 127 novel papers the ledger read: 5,183 of 5,808 chunks arriving as one
  chunk in the right lane, 158 single chunks in the wrong lane, 333 paragraphs cut into 750,
  87 landing outside the prose, 47 missing, and 136 chunks of the reading's own carrying text
  no paper has. The junk was the most answerable: every one of them was the publisher's
  furniture — a licence sentence, an imprint, an editor's name, a first-page abbreviation
  list — read as body prose. Six rules took it to 108 and the produced chunk count from
  6,290 to 6,268, moving one paper on the 199 and moving it up (faithful 0.9726 → 0.9730, well
  matched 184 → 185, the sixth rule's doing and described below) and leaving
  the corpus gate exactly as it was: Karim's two libraries clean, one known flag on held-out
  1, the eight known ones on held-out 2.
  What the same measurements refused is worth more than what they passed:
  **the look-back join cannot be widened.** Of the paragraphs the reading cuts, only 20 of 135
  open lowercase — the near-proof signal — and 16 of those have a caption or table between the
  halves. But labelling every candidate by the XML, the nearest unfinished head before a
  lowercase opener is the right one **0.457** of the time, two blocks back 0.042, three back
  0.000. Semicolon-separated list items and a formula's "where" clause drown the signal
  (a head ending in ";" is right 0.200 of the time against 0.560 otherwise). Against a 0.98
  bar for a silent merge this is not close, at any reach.
  **A heading that reads as prose is usually a real heading**: of 214 such headings across
  three corpora, 161 are ones the paper's own XML has. Refusing them would be right 0.206 of
  the time at 8–11 words and 0.362 at 12–17. The RSC author-biography fragments that swallowed
  1,182 words of one review's introduction stay unfixed rather than take that rule.
  **A wrong title is cheaper than a lost heading.** Nature sets "A Nature Portfolio journal"
  above the abstract, where it wins the title and leaves the real title to be read as a section
  heading. Ranking a journal-naming candidate last changed nothing on its own — the real title
  sits below the abstract, past where the scan stops — and reading on until a better candidate
  appeared cost PNAS its first heading (faithful 0.885 → 0.794). Both reverted. `_journal_name`
  is also too loose to ask about a title candidate: it calls a figure panel's "A" the name of
  "Proc Natl Acad Sci U S A".
  What is left on the novel set, in order: 333 cut paragraphs (measured above, and mostly the
  XML being coarser than the reading — 115 of 135 open on a capital, many of them a structured
  abstract the reader correctly splits), 158 single chunks in the wrong lane (the heaviest by
  far is a journal's convention, not an error: Cureus wraps a whole systematic review in one
  "Review" section the XML lanes `other`, 44 chunks on one paper), 79 outside the prose, 47
  missing, 113 junk.
  **And the ingestion question, asked directly, is already answered.** "Right lane" is a
  placement number and had been standing in for an ingestion one. Asked whether each of the
  XML's prose paragraphs is in the reading's tree at all — any node, any type, at least half
  its shingles — the answer is 0.9995 of the words on the 127 novel papers and 0.9997 over the
  199 tuned pairs (`ingested.py`; pilot 0.9999, held-out 1 0.9998, held-out 2 0.9996). The 47
  "missing" chunks are about 360 words in total, because every one of them is short. Testing
  them against the PDF's own text layer with pypdfium2 killed the standing hypothesis that they
  were scanned pages: 43 of the 47 *are* in the text layer. They are run-in labels the XML
  holds as paragraphs and the reading reads as headings ("Body Weight Gain", "Filling Phase:"),
  plus six one-sentence graphical-abstract blurbs the PDF carries only as an image. Vision has
  no measured case left in this corpus, which retires Stage 5 of the plan for now.
  The last rule of the day came out of the only heavy misplacement left in the working half
  that was diagnosable — the three heaviest are in the sealed half and were left alone. A
  two-column page read right column first puts "2 METHODS" and "2.1" above "1 INTRODUCTION",
  so "2.2" arrived with the introduction open and the reader stood in an invented parent,
  `2. (heading not detected)`, role `other`, taking every later subsection with it. It now looks
  for a top-level section the author numbered "2" and goes back to it. This is the one change of
  the day that moved a tuned paper at all, and it moved it up by 0.113; faithful by words
  0.9726 → 0.9730, well matched 184 → 185, and on the novel set 5,191 → 5,195 chunks arriving
  as one chunk in the right lane with the wrong-laned falling 158 → 154. The reading-order pass
  itself was left alone: putting that page's columns back is the deeper fix, and `_columns`
  wants its own measurement before it is touched again.
  The habit that paid best: after each rule, read the ledger again rather than reason about it.
  The second reading found four shapes the rules nearly knew — a licence sentence the layout
  had cut in half ("International License, which permits any non-commercial use…", missed only
  because `_LICENCE` spelled `noncommercial` without the hyphen every journal prints), a date
  line `_late_front` let through and `_front_kind` then had no word for, an author list that
  never reached the gate at all, and Cureus's "Categories:" line. Five more junk chunks, no
  paper moved, precision on the 199 up 0.97615 → 0.97630. The lesson worth keeping is the
  second one: a two-part rule whose halves know different shapes will open a gate and then
  answer `other`, and that costs nothing visible — it just quietly does not work.
  **The sealed half, read once at the end**: 87.1 per cent of its chunks arrive as one chunk in
  the right lane and 91.5 per cent in the right lane at all, against the working half's 90.9 and
  94.7. A 3.8-point generalisation gap, and it is concentrated rather than spread — the sealed 64
  hold 43 of the set's 47 missing chunks and 57 of its 79 landing outside the prose, most of them
  in three papers, and three of the four heaviest wrong-lane papers. Two of those three are a
  journal's convention, not an error. Every diagnosis of the day used `working-half.txt`; the
  three heaviest misplacements of the whole novel set were left alone because they are sealed,
  and they are still there to be found in the next round. **A fourth set still needs fetching**
  (`fetch_heldout3.py`, fresh topics, excluding every DOI already held) so the next round opens
  with an exam that has not been spent.

- **2026-09-19, later still: the residue named by where it goes** — Karim:
  "Fix. We are getting close." So nothing was guessed: every word of the
  XML's prose the reading does not file in the same lane was counted by
  where it went instead (`residue2.py`, saved beside the libraries). Of the
  3.6 per cent, 1.44 was filed under another section, 1.04 not held at all,
  0.33 in a reference list or a back statement, 0.27 in the right section
  under the wrong lane, 0.24 in the front matter, 0.16 in a caption. Then
  the mechanisms behind each, one at a time, measured on the 199 pairs, kept
  only where no paper read worse.
  MDPI sets its reference list at the foot of the page, and the layout model
  reads the list before the text above it in the same column: a conclusion
  filed under "References" in five papers. A page read out of order in one
  column is put back — and the two guards came from the papers the first cut
  broke. An ACS sidebar, rotated, reaches Docling as a sliver claiming 298
  words: a box too small to hold its own text is not the block's (0.995 →
  0.918 before the guard). A heading at the top of a two-column page's right
  column stands above the left column's last paragraph and is read after it,
  which is the order the page means: on a page of two columns both runs must
  stand in the same one (0.989 → 0.930 before). Columns are read from the
  blocks that carry a paragraph — a heading or a one-line statement says
  nothing about how a page is set, and taking them for columns had cost the
  MDPI fix itself. Fifteen papers better, none worse.
  Diabetes Care prints "RESULTS" in its visual abstract, again in its
  structured abstract, and over the section itself: three pages, so both of
  its body headings were dropped as recurring furniture and the whole body
  read as methods. A running head is what recurs at the same *height*, and
  only the occurrences that stand together are dropped: 0.245 → 0.830.
  An Advanced Science review's "3 SEI Characterization" had been laned
  results-discussion by the content pass where its XML says `other`: a lane
  from content only in a paper whose own headings name its methods or its
  results — a review names neither, and its topical sections are `other`,
  which is what its XML says of them. The verdict is still stored and noted.
  0.697 → 0.983.
  ASTMH sets its headings in capitals in the body's own face, marked by
  nothing else; the layout model found seven and read the eighth as text, so
  1,080 words of discussion filed under the section before it. A line of
  capitals is a heading where the paper sets its headings so — three or more
  found that way — and only by relabelling a block the model read whole:
  0.880 → 0.973.
  And Diabetes Care's structured abstract, printed as sections named
  "OBJECTIVE", "RESEARCH DESIGN AND METHODS", "RESULTS", "CONCLUSIONS",
  had been read as the body, its introduction under "CONCLUSIONS". Three or
  more of a structured abstract's part names in a row on the first pages,
  each over short prose that cites nothing, are the abstract's parts, laned
  `abstract` under an abstract built over them; the citing prose after them
  opens the introduction (dc25 0.807 → 0.949, a data paper 0.918 → 0.993).
  The look-ahead weighs the part's *first* paragraph only: what follows the
  last part is already the body.
  Where it stands, 199 pairs, against the end of 2026-09-18: faithful by
  words 0.832 → 0.973 (pilot 0.754 → 0.980, held-out 1 0.809 → 0.977,
  held-out 2 0.871 → 0.968), mean 0.842 → 0.967, well matched 126 → 183,
  depth agreement 0.886 → 0.941, headings found 0.879 → 0.884, and the
  reference lists and citation links rose with them (0.928 → 0.953, 0.811 →
  0.834: a list with a conclusion in it is not a list). Precision unchanged
  at 0.974. Nineteen papers better than this morning, none worse.
  What is left, 2.7 per cent, is in BACKLOG by category; the largest loss in
  any one paper is now 971 words, and no mechanism repeats across more than
  a handful. One idea the measurement killed: "a PDF's abstract is one
  block, and a second long paragraph in it is the introduction" — 22 papers
  worse, none better, because a structured abstract's parts are paragraphs
  and not always headings. Reverted.
  Gate against the end of 2026-09-18: Karim's two libraries clean; held-out 1
  one flag, the Advanced Science review's lane going results-discussion →
  other, which is the fix itself, and two MDPI papers gaining citations as
  their reference lists came right (3 → 51, 23 → 77); held-out 2 the nine
  from this morning less one — the four front-matter labels, the editorial's
  built heading in both journals, three papers' finding→method links (pone
  3 → 1, as its XML; vjgb 13 → 1 against its XML's 5; s12888 18 → 10
  against 8) — and two more papers gaining citations (4 → 87, 5 → 55). The
  Diabetes Care paper's lost link came back with its headings.
  201 parser tests, one for each new rule; two old tests were fixtures that
  did not model a page (a running head at three heights, a paper with no
  named methods or results) and were corrected. `check:all` passes. Nothing
  committed.

- **2026-09-19, later: depth from the type, and the first page read in
  order** — Karim: "I'm less worried about retrieval and more concerned with
  getting clean ingestion. Start". Then, midway: "Wait what's the oracle
  ceiling" (the PDFs read with each heading's depth copied from its XML: the
  best any depth fix could do, faithful 0.935 by words against 0.832).
  Depth by type (`typography.depth_by_type`, taken by `infer_level`). First
  cut, straight from the probe: 0.832 → 0.924, 42 papers better and 11
  worse, and each worse paper named a rule. A fused "Results Device
  characterization and stability" split in two whose second half kept the
  row's top-level look (a Sci Rep paper, 0.991 → 0.389): the second half is
  the first's subsection. "Figure 1" and "Figure 2" read as headings, set
  bold like the anchors (JAAOS, 0.985 → 0.616): a figure's label gets no
  level. BMC's "Quantitative data", Semibold, measured larger than the Bold
  top level because a capital Q's tail reaches below the line: the size is
  a flat capital's height, round and pointed ones scaled back by the
  paper's own overshoot, and weight counts. "Ethical approval" inside a
  methods section (vetworld, 0.994 → 0.723) had kept the top level by the
  vocabulary and taken the methods under it: a back statement follows the
  type inside the body. PLOS's "1. Atomic-level features" and Frontiers'
  "1. Ban Unhealthy Foods" became anchors as first-level numbers: core
  names first, and a number the top level does not use, set below it, is a
  list's. "The Bottom Line", a box set larger than a numbered top level:
  no unnumbered heading is promoted where the top level is numbered. A
  heading nested under a "References" read too early: never under the
  reference list. Then the gate on Karim's own libraries, which have no
  twins, found eleven papers that lost their finding→method links — a
  Liebert paper whose subsections are set in another face of the same size
  (promotion now needs the same face, demotion a real difference), two
  scans whose text layer is one font (skipped whole), 1998 and 1985 papers
  among them — and on held-out 1 a Nature Genetics paper whose consortium
  lists after the references became sections and broke superscript
  citations, 162 → 37: nothing is set by its look in the reference list's
  region. With those: 0.938 by words, above the oracle, 158 well matched,
  Karim's libraries clean.
  Then the first page. ASM's and PNAS's "(1, 2)." and "(Plastics Europe
  2024)" were citations the built-introduction rule did not know (spectrum
  0.801 → 0.990, aem 0.844 → 0.970). A box before the introduction kept the
  prose read after it: two Frontiers editorials 0.000 → 0.988 and 0.998,
  Wiley's KEYWORDS under the abstract (phy2 0.736 → 0.945). A printed
  heading nested under a built "Introduction" (a Sci Rep paper 0.385 →
  0.962; Nature's main text 0.611 → 0.909, and 0.984 with the next rule).
  An OUP editorial, printed in Genetics and in G3, had its two section
  headings joined to the front matter because no heading the vocabulary
  knows came before page 3: front matter ends at the first printed heading
  after forty words of prose (0.001 → 0.567, 0.033 → 0.580). Elsevier's
  first page reads "1. Introduction" before the abstract, whose section
  then took the introduction's prose (bioactmat 0.834 → 0.984). The
  ligatures: Hindawi's STIX fonts read "fi" as "f" ("identifed"), RSC's
  draw it with a private-use glyph, Wiley's Advanced journals keep it with
  the left fragment ("specifi c"). Then the two papers still worse than
  the morning: BMJ's key-message box ("What is already known on this
  topic", …) set in the introduction had taken the introduction's prose
  read after it — its bullets cite nothing and the prose does, so prose
  that cites goes back, before the methods only (military 0.892 → 0.988,
  a BMJ Open paper 0.859 → 0.957) — and Hip & Pelvis, whose numbered
  methods subsections are set small under "MATERIALS AND METHODS" and
  nested so, where its XML keeps them flat (0.979 → 0.916: the page is
  right; left alone). And the editorial again: its "Natural selfish genetic
  element systems" nested under the abstract its opening paragraphs were
  read as. A rule lifting a heading out of the abstract when its prose
  cites took the editorial to 0.878 and a BMC paper from 0.989 to 0.149 —
  "Text box 1. Contributions to the literature", its main text read right
  after the label. Tightened, it was then redundant: the editorial names no
  core section, its headings are all set one way, and a paper like that has
  one level (`_one_level`; depth agreement 0.667 → 1.0 on it). The lifting
  rule came out.
  Where it stands, 199 pairs: faithful by words 0.832 → 0.964 (pilot 0.754
  → 0.978, held-out 1 0.809 → 0.963, held-out 2 0.871 → 0.959), mean 0.842
  → 0.959, well matched 126 → 180, depth agreement 0.886 → 0.942, reference
  lists and citation links unchanged (0.928, 0.811); 71 papers better, one
  worse (Hip & Pelvis). What is left, 3.6 per cent of 1,027,592 words: 1.0
  not held by the PDF's reading where the paragraph lies (0.47 not found
  as words anywhere), 2.5 in another lane; the largest, the
  Diabetes Care first page (3,072 words) and a review laned
  results-discussion by a vocabulary word (2,495) — BACKLOG. Gate against
  the end of 2026-09-18: Karim's two libraries and held-out 1 clean;
  held-out 2 ten flags, none a worse reading — four front-matter labels
  that lost a lane they should not have had ("Authors:", "Why did we
  undertake this study?", "CASE REPORT", "TOOLS FOR PROTEIN SCIENCE"), the
  editorial's built "Introduction" gone in both journals (its words were
  the printed section's), and four papers' finding→method links: pone 3 →
  1 and s12888 18 → 10, as their XML (1, 8); vjgb 13 → 1, its sections now
  the XML's, which links 5; dc26-0470 1 → 0, the Diabetes Care first page,
  broken before and after (its XML: 9). Sections between Methods and
  Results are `other` on both sides (BACKLOG, lanes by position). The
  quoted numbers were recomputed from the saved runs before writing this
  (`measurements/2026-09-19/`: the runs step by step, the gate, the
  scripts). All seven libraries rebuilt with the final reader; 195 parser
  tests, `check:all` and the headless e2e pass. Nothing committed.

- **2026-09-19, what stands between the reader and a perfect reading** —
  Karim: "What do we need to get this to 100% effectiveness". Measured
  before answering, over all 199 pairs as read on the 18th (1,027,592
  words of XML prose). 16.8 per cent of the words are misfiled or missing
  (faithful by words 0.832); the worst 20 papers hold 56 per cent of the
  misplaced words, the worst 40 hold 73; reviews lose 35 per cent of
  their words, editorials 70, research papers 12. The words themselves
  are there: 1.07 per cent missing. An oracle settled the order of work:
  the PDF read again with each heading's depth taken from its XML twin
  (`oracle_depth.py`, beside the libraries in `measurements/2026-09-19/`)
  — faithful by words 0.832 → 0.935, mean 0.842 → 0.915, well matched
  126 → 152 of 199; perfect depth is 61 per cent of the loss, and the
  lanes of the top-level headings taken from the XML as well add nothing
  (both sides lane by the same rules). In paper after paper the headings
  are found and nested wrong: a review's topical sections as the
  introduction's subsections (s41422 "INTRODUCTION" holds 8,122 words and
  15 subsections, the XML 377 and none), under a vocabulary word used as
  a subsection ("Cells"), under a footnote read as a heading
  ("Corresponding authors:", 6,532 words), under a back statement printed
  mid-paper (Diabetes Care's "Data and Resource Availability", 3,862).
  A first probe of depth from the type (`depth_probe.py`): an unknown
  unnumbered heading set as prominently as the paper's anchor headings
  (Introduction, Conclusions, a numbered first level) is top-level — right
  for 0.81 of 326 such headings on 64 pairs, the reader 0.77. The misses
  are the probe's own: anchors taken from a vocabulary word set as a
  subsection, a bullet ("■") read as the heading's first span, capital
  heights rounded to half points, rows matched by text rather than box.
  After perfect depth 6.5 per cent is left, 43 papers under 0.9:
  editorials and commentaries whose whole body lands under a back heading
  or a built introduction (five at 0.0–0.03 — a convention to settle, the
  XML's lane for them being its own rule's), back statements printed
  mid-paper adopting what follows, the abstract's end, results and
  discussion confused in combined sections, spelling (a few per cent of
  shingles: hyphenation, ligatures, symbols), paragraphs split at breaks
  (4.9 per cent of 8,192) and merged (1.7), captions read as prose (4.7
  per cent of 1,199), reference lists off by a tenth in 25 papers and
  citation links under 0.8 in 55 (ACS 8 of 9, Nature 9 of 20). Four
  papers read worse with the XML's depth: the XML nests where the page is
  flat — the yardstick has conventions of its own.

- **2026-09-18, later: smaller questions, and the page's type answers the
  biggest one** — Karim: "we should make the questions we ask to the models
  smaller and more targeted. feeding it the entire paper is probably not
  sensible. what would make more sense would be asking things like which
  heading does this chunk belong to, or: which of these chunks look like
  headings … we can use more than one agent call per paper, too … we need
  to get to nearly perfect ingestion." Then "go."
  The measurement first, nothing wired. Ollama's structured output (a JSON
  schema with an enum in `format`) holds every model to a one-word answer,
  the 4B included — the one that narrated its way out of the outline; a
  question of 250 tokens takes 0.13 to 0.3 s once the model is loaded (the
  first call loads it: 3 to 13 s). So the heading question went to every
  candidate line of the 64 pairs, with the XML's answer beside it: the
  reader's own headings (1,538), short text blocks (250), paragraphs that
  open with a short sentence (1,373), a vocabulary word at a paragraph's
  front (41), a numbering fused into a paragraph's start (9) or middle
  (15) — 3,226 questions, 581 s on the 8B. What the candidates said before
  any model did: the reader has 1,389 of the XML's 1,694 headings (0.82);
  93 are never printed in the PDF ("Footnotes", "Contributor Information",
  an "Abstract" the journal does not label), so the ceiling is 0.945; the
  candidate shapes reach 0.90; of the rest, 38 were MDPI's statements
  filed under the reference list, 21 headings fused into the middle of a
  paragraph, 16 two headings on one line, 15 run-ins with a comma or no
  punctuation ("Acknowledgment This work"), 7 headings with a glyph split
  ("T endon", "I NTRODUCTION", "Confl ict"). And 140 of the 1,694 are
  run-in headings, printed on the paragraph's first line.
  What the models said. The 8B, shown the line with two lines of context
  either side: it copies a heading from the context instead of reading
  the line ("RT-qPCR: Rat tenocytes…" → "Statistical Analysis"), calls a
  paragraph's topic sentence a heading ("Silk is another…" → "Silk"), and
  finds 60 per cent of the run-ins at precision 0.36 against the XML. The
  14B, shown the paragraph alone, with the answer validated against the
  line (the heading must be the line's own start and end at a full stop, a
  colon or a capital): recall 0.80, precision 0.57 — and nearly every
  "false" answer is a run-in label the XML files as a note, not a section
  ("Funding:", "Author Contributions:", "Abbreviations:", a structured
  abstract's "Methods:"). The model vetoing the reader's headings is not
  worth having: one-word topical headings ("Silk", "Cells") and statement
  headings go with the URLs and dates.
  Then the page answered the same question. pdfium gives every glyph its
  font name and weight (`FPDFText_GetFontInfo`, `FPDFText_GetFontWeight`),
  and a probe of the Hindawi review showed it at once: "2. Current
  Therapeutic Strategies" in Minion-Black, "2.1. Non Surgical Approach."
  in Minion-Italic for exactly the heading's 24 characters, the paragraph
  in Minion-Regular after it. `typography.py` (new): every pdfium line as
  runs of one style, the row's size a capital's glyph-box height on the
  page (the loose box reads 1.0 for a text matrix's scaled font), the
  body's style the face holding most of the letters at the largest size
  holding a third of them (a long reference list in a smaller size is not
  the body), a row set apart as bold (weight, or the face's name — Wiley's
  subset fonts carry weight 0 and a ".B"), italic, larger (a capital and a
  half taller) or numbered deep in the body's own face ("2.2.1. PU Coating
  onto the Bare Polyester Fabric": MDPI sets its third level like text).
  Probed on the pairs before wiring: typography alone reaches 0.72 of the
  XML's headings, the union with the reader 0.915. Wired into
  `recover_from_pdf`, before the tree, and again on rebuild: run-ins cut
  from their paragraphs, fused headings cut out after the sentence before
  them, dropped headings recovered, split letters spelt as the row prints
  them; every one marked and counted in repairs.
  Five rounds against the pairs set the guards, each a paper that broke.
  MDPI's reference lists fell from 0.95 to 0.09 (jfb16110403): the
  statements "Funding" to "Conflicts of Interest" are set between the
  entries in Docling's order, and once they were headings the first of
  them closed the list — `_entries_follow` now looks past a whole block of
  back-matter headings, and reference lists and citation links ended
  higher than before the change (pilot 0.88 → 0.95, links 0.84 → 0.90).
  Structured abstracts cut into sections ("Background:", "Methods:",
  "Results:" on the first page; OVJ 0.999 → 0.935): nothing is cut before
  the body's first heading — "Introduction" on its own row, a numbered
  row, or page two. A table's "Note:" and "Abbreviation:" set in bold
  inside a review (mtbio.2026.102977, 0.866 → 0.544, a third of the paper
  laned back) — a back-matter word before the body's last heading is a
  table's note. A consortium's members in bold ("GERAD", "Demgene",
  s41588): a heading is more than a name or an acronym and begins with a
  capital. A paragraph's first line in bold in Frontiers, as wide as the
  column: a heading is shorter than its column unless numbered, the
  column's width the prose rows' median (a title spans both columns, so
  the widest row is no measure). Wiley's "How to cite this article" after
  the list, and an entry's italic journal name: nothing is cut after the
  References row. PNAS's "Ethics Statement." and "Statistics." run in
  under Materials and Methods became top-level sections by their names
  and took the methods' text with them (findings linked 11 → 1): a run-in
  heading is a subsection of the section it stands in, whatever its name
  (`_runin`, level 2 in `tree.py`). "Supplemental legend (AEM…docx)" left
  a paragraph starting with ")": the heading's own closing bracket goes
  with it.
  Where it stands, on the 64 pairs against the 2026-09-17 reading: the
  XML's headings found 0.82 → 0.89 (pilot 0.819 → 0.910, held-out 1 0.826
  → 0.870); the PDF's own headings the XML has 0.91 → 0.87 and 0.88 →
  0.84, the difference the statements the XML keeps as notes; faithful
  0.775 → 0.776 and 0.832 → 0.844; no paper's text worse laned, s41588 0.35
  → 0.75 (its dropped headings back), polym14050989 0.973 → 0.998; depth
  agreement 0.935 → 0.902 on the pilot, the new subsections nested by the
  reader's guess — the depth from the type is the next thing
  (BACKLOG). Held-out 2, the 135 pairs nothing was tuned on: headings found
  0.837 → 0.865, faithful 0.857 → 0.857, reference lists 0.916 → 0.941,
  citation links 0.793 → 0.815, depth agreement 0.914 → 0.888. The
  calibration of the score barely moves (AUC 0.79 → 0.78, 0.83 → 0.83;
  126 of 199 well matched from 125). The corpus gate is clean on all
  three corpora against the 2026-09-17 baselines, with citation links
  gained on 17 papers (the Frontiers fix's twelve and five more:
  s41588 87 → 162, jox16020064 22 → 90, fneur.2026.1889180 0 → 61); the
  three papers that lost finding-to-method links on the way — a
  measurement "1.0 mL" read as a numbered heading, PNAS's run-ins at top
  level, a run-in subsection turning a methods section's text into a
  preamble no finding may link to (`edges.py` now keeps such paragraphs
  as methods) — are back where they were. `npm run check:all` green (186
  parser tests), all seven libraries rebuilt with the type read; the
  measurements of the day, the heading questions' records and the
  typography probe's, are in `~/.protracker/library/measurements/2026-09-18/`.
  The model's heading question stays a measurement (`heading_q3.py`,
  scratch), for the residue the page cannot answer: a run-in set in the
  body's own face. Nothing committed.

- **2026-09-18, a model in the loop: the outline judge, six models on the
  pairs** — Karim: "What if we went for an agent in the loop approach? …
  see if it can figure out … which lane they're meant to go in, so we can
  get that agreement score higher?", "or qwen", "Can Qwen3 interpret or
  ingest an entire paper? … maybe that will just give us the sections",
  then "build a working prototype of the entire pipeline using an agent in
  the loop, and test different local models." Also his idea, kept in the
  BACKLOG: fine-tune a classifier on the XML corpus's paragraphs and
  validate it on the PDF side of the pairs.
  The window first. Qwen 3 14B's context is 40,960 tokens; over 256 papers
  the median is 11,000 tokens as the reader shows them (headings marked,
  paragraphs numbered, the reference list left out), p90 19,000, 16 above
  24,000; a paper longer than 18,000 words goes as the first sixty words
  of every paragraph. The KV cache at a 32,000-token window is about 5 GB
  beside 9.3 GB of weights, so the 14B fits the 16 GB card with room; one
  answer 20 to 25 seconds (25 s a paper on the pilot, 20 on held-out 1,
  12,800 and 10,600 prompt tokens, 1,000 and 760 answer tokens, thinking
  off, temperature 0, seed 7). So `outline.py` asks one question per PDF
  and gets the outline as JSON: title, printed or inferred, depth, lane,
  first paragraph. Every answer is a row in `outlines` keyed by the
  paper's text and the model, replayed by rebuild and by the harness, never
  asked again; off unless `LITRAG_OUTLINE=on` or `outline: true` on the
  request; the XML side of a pair is never judged.
  What is taken from the answer was decided by the pairs, in five rounds,
  each a replay of the stored answers through a new `apply()` (a few
  minutes, no model). Round one took every lane and built every boundary
  with the model's lane: pilot 0.775 to 0.818 but twelve papers worse of
  31. The damage, paper by paper (`outline_build_diff.py`, which lists
  every built section and where it landed): a built heading given the
  model's lane — a Hindawi review's run-in "2.1. Non Surgical Approach",
  which the reader had not split, became methods and 0.996 fell to 0.594;
  a review's numbered subsections re-laned; the model's paragraph off by
  one at a section's start. So a built heading is laned the way the reader
  lanes any heading — by its own name at the top level, by the section
  above when nested, never by the model's word — and stands beside the
  section its numbering matches or after the top-level section it ends
  where that reorders no text (a dropped "4. Conclusions" read into "3.6"
  is a top-level section after "3"); a heading the reader has is never
  built twice; nothing is lifted out of the abstract. With that, building
  is neutral on the text's lanes and lifts headings found from 0.82 to
  0.85. Round three, the lanes: a Scientific Reports paper's reader-built
  "Introduction" went results-discussion (0.999 to 0.822) — a built
  heading that names a lane is the reader's rule now, not its guess; a
  research paper's methods subsection went `other` — so the judge is told
  the paper's type first (the reader's first decision, before the outline
  stage), and in a research paper a subsection keeps its section's lane
  whatever the model calls it. Round five, the one that mattered: Qwen 3
  14B calls a review's sections methods and results-discussion by their
  sense (a section on biodegradable polymers is "methods" to it), while
  the review's XML lanes them `other` by their headings — which is what a
  lane is in this project — so in a review a section the model puts
  outside the one the reader nested it under is a topical section,
  `other`, whatever the model calls it. That rule took the pilot from
  0.865 to 0.917 and held-out 1 from 0.839 to 0.906.
  The table, 64 pairs (pilot 31, held-out 1 33), faithful before 0.804,
  well matched 38, seriously off 19, headings found 0.823, the lanes
  headings agree on 0.883 (pilot) and 0.93 (held-out 1):
  Qwen 3 14B 0.911 · well 46 · serious 11 · 11 better, 2 worse · headings
  0.850 · lane agreement 0.96 / 0.975 · 25 and 20 s a paper.
  Qwen 3 8B 0.900 · well 44 · serious 13 · 10 better, none worse · 0.841 ·
  17 and 13 s.
  Gemma 3 12B 0.896 · well 42 · serious 13 · 12 better, 1 worse · 0.849 ·
  33 and 17 s.
  Qwen 2.5 7B 0.865 · well 43 · serious 14 · 9 better, 5 worse · 0.834 ·
  19 and 10 s.
  Llama 3.1 8B 0.861 · well 41 · serious 15 · 5 better, 2 worse, 3
  answers unreadable · 0.832 · 15 and 11 s.
  Qwen 3 4B: 63 of 64 answers unreadable — it narrates its reasoning
  instead of answering, with thinking off and the instruction firm; not
  usable. The gains are the reviews the reader had folded under one
  heading: nine of them from 0.07–0.29 to 0.67–0.99 (IJN.S550439 0.065 to
  0.986, ten.teb.2016.0181 0.108 to 0.985, s41422-020-0332-7 0.108 to
  0.988, jot.2017.02.005 0.114 to 0.927, s12951-019-0556-1 0.151 to 0.949,
  s41573-020-0090-8 0.159 to 0.906 with 23 headings built, 19490976 0.203
  to 0.963, 2041731420974861 0.236 to 0.921, fcell.2021.651164 0.290 to
  0.673), and pcbi.1014552 0.576 to 0.653 with its run-in headings built
  and every heading found. The two losses: fbioe.2021.621483 0.383 to
  0.285, a review whose XML really nests its topical sections under
  "Discussion" (the model's word is taken as it would be for any review);
  biom12101518 0.995 to 0.976, the model's paragraph off by one where the
  reader prints the heading differently. Precision does not move (0.958,
  0.98): nothing is dropped. Disagreement notes are many — 226 on the
  pilot, seven a paper, the model calling a review's numbered sections
  results and methods; recorded, never applied (BACKLOG).
  Also from the runs: four of 31 first answers were "it seems you have
  shared a list of references" because a Frontiers PDF's reference list
  had been read as 93 back-matter paragraphs — the reader's defect (the
  statements Frontiers sets in the left column under the start of the
  list cut it; fixed in `tree.py`, gate clean, twelve papers gained
  citations across the corpora), and reference-shaped paragraphs are left
  out of what the model sees besides. An answer cut short by the output
  budget keeps the sections it finished (`num_predict` 8,000 now).
  Standing: `LITRAG_OUTLINE=on`, `LITRAG_OUTLINE_MODEL` (default
  `qwen3:14b`; `qwen3:8b` is the cheaper, safer choice), the `outline`
  stage in the log, "outline by <model>: N lanes, N headings built" in the
  saved line, `outline_lanes`/`outline_built`/`outline_disagreements` in
  repairs. Off by default: twenty seconds of the card a paper, and the
  corpora with no XML twin are unmeasured — the way in is in the BACKLOG.
  Six models pulled into Ollama (qwen3:8b 5.2 GB, qwen3:4b 2.5 GB,
  gemma3:12b 8.1 GB, llama3.1:8b 4.9 GB beside qwen3:14b and qwen2.5:7b).
  The measurement files — every model's per-pair records under the final
  rules, the pairs and the calibration of the day — are in
  `~/.protracker/library/measurements/2026-09-18/`. `npm run check:all`
  green (180 parser tests), headless e2e green; the seven libraries
  rebuilt with the Frontiers fix. Nothing committed.

- **2026-09-17, papers read twice, and how far a reading can be trusted** —
  Karim: "i want to be able to sort papers by xml and pdf", "we also need to
  be able to filter by type of paper", and "We need to design some kind of
  confidence metric that we can use to screen … well-matched papers. I think
  we can probably do this, especially easily if we have both the XML and the
  PDF of a particular paper. Could you go through the loop ligament corpus
  and see which PDFs we can find XMLs for so that we can compare PDF and XML
  and iterate on that?"
  The list first: a sort (as added, format, type, title, year, confidence
  lowest first) and chips for format, type and band of confidence over the
  paper list (`app/src/renderer/papers.ts`, pure and tested; an e2e test
  ingests a second, synthetic review so the real window has two types to
  tell apart).
  The survey. looped-ligament holds 43 PDFs and 37 XML and no paper in
  both; the archive's 52 PDFs are those 43 files (nine are second copies
  under their old hash names). By DOI, Europe PMC knows 40 of the 43: 28
  are in PMC (23 as NIH author manuscripts, 5 publisher deposits that are
  not open access), 12 are indexed with no full text, 2 it does not know,
  1 has no DOI. Its REST service serves full-text XML for none of the 28:
  it answers 500 for anything outside the open-access subset. So the pairs
  were made the other way round, PDFs for the XML papers. The website's
  `?pdf=render` links, which worked on 2026-09-12, now sit behind a
  Cloudflare bot check (403 "Just a moment…") and were left alone; EBI's
  bulk area (`ftp.ebi.ac.uk/pub/databases/pmc/pdf/OA/PMCxxxx<block>/
  PMC<id>.zip`, block = the PMCID divided by 10,000 and rounded up) is made
  for scripts and had 32 of the 37 (the other five are too new). They are
  the library `looped-ligament-pairs`; 31 pair with their XML — the 32nd
  had been filed under its dataset's Zenodo DOI, printed above its own, and
  `pick_doi` now passes over a repository's DOI unless it is all there is
  (one paper in looped-ligament itself is filed that way,
  `doi:10.5281/zenodo.4897976`, and keeps its key until dropped and read
  again). With the held-out sets that is 199 pairs: 31 pilot, 33 and 135.
  Open for Karim: the 23 author manuscripts are served as XML by NCBI's PMC
  OAI service, one host more than the invariant lists (identifiers out,
  XML in), or by EBI's bulk tarballs at 1 to 4 GB per PMCID range, about
  14 GB for these. I did neither.
  What the pairs said (`pairs.py`). The text is there: recall 0.99 on all
  three sets once presence is counted in words — by shingles it read 0.96,
  and the missing four points were words hyphenated at a line's end, each
  costing four shingles. What goes wrong is the lane: faithful 0.78 (pilot),
  0.83, 0.86; precision 0.96 to 0.98; paragraphs intact 0.92 to 0.94, split
  0.04 to 0.06; headings found 0.82 to 0.84, depth agreeing 0.91 to 0.94;
  reference lists the same length 0.80 to 0.88; citation links 0.67 to
  0.81. 125 of 199 are well matched (faithful and precision both at least
  0.9), 47 seriously off (either under 0.8). The medians are high (0.95 to
  0.98) and the tail is long: a missed or fused top-level heading moves half
  a paper.
  None of the reader's own older measurements sees it. Rank correlation
  with faithful on the first 168 pairs: page coverage −0.02, dropped lines
  −0.12, glyph residue −0.06, audit errors −0.04, warnings −0.16; the best
  of the old numbers were whether the headings are numbered (0.34) and how
  many joins a page needed (−0.34).
  What the pairs found in the reader, fixed the same day: a Docling block
  that carries its paragraph twice, a copy cut short and then the whole, or
  the whole and then its start again (the fixture's own page 7 and 8;
  `unrepeat`; sets of shingles had hidden it, counting words showed the
  PDF held twice the XML's); a lane's heading fused with the subheading
  under it, "Results and discussion Contrasting glacier mass balance …",
  which had left three quarters of that paper under its methods
  (`split_fused_heading`; the looser first detector flagged "Limitations of
  the study" and "Ethics approval and consent to participate", so the rule
  is a core lane's bare name followed by a capitalised phrase); and the
  largest: the reader ignored the depth an XML states. `infer_level` nested
  every unnumbered heading the vocabulary does not know under whichever
  top-level section stood open, for XML as for PDF — 453 headings in 145 of
  513 XML papers, and whole review bodies read as `introduction`
  (`infer_level(stated=True)` for a document with no pages; vocabulary
  words still stand top-level, so BMC's "Declarations" wrapper still
  empties and drops). Not fixed: the PDF side of the same defect. Measured
  on 958 unnumbered unknown PDF headings with an XML twin (136 top-level,
  822 deeper): box height does not separate them, nor the left edge;
  capitals do where a paper sets its tops in capitals (7 of 7, 89 of 89).
  The font is what is left to try (BACKLOG). Also open: text layers woven
  twice line by line (the fixture again), "Natural Materials" laned methods
  by meaning inside a review, Nature's methods after the reference list.
  The score (`confidence.py`). Eleven checks, each a measurement of the
  tree with a limit read off the pairs and a graded penalty: the share of
  the prose in the abstract (no well-matched research paper exceeded 0.10),
  in back matter (0.20), in the reference list as paragraphs of a hundred
  words or more; the introduction (0.33) or the methods (0.45) as the
  largest lane, the discussion in a review (0.55); lanes the type should
  have; six-word shingles seen twice (0.04); unterminated paragraphs
  (0.10); three or more headings that are not headings; an unsettled type.
  Multiplied, reasons kept in words. On the 199 pairs: it ranks a
  well-matched paper above another 0.79 of the time, a seriously mismatched
  one below the rest 0.83, rank correlation with faithful 0.50. At 0.9 or
  more: 129 papers, 105 well matched, 11 seriously off, mean faithful
  0.94. Under 0.5: 30 papers, 25 seriously off, mean faithful 0.38. The
  limits were mostly read from held-out 2; on held-out 1 and the pilot
  together the top band is 32 of 38 well matched and the bottom 11 of 13
  seriously off, so it is not fitted to one set. What it misses: eleven
  papers at 0.95 to 1.0 whose faithful is 0.58 to 0.79 — a fifth of the
  text under a neighbouring lane because one heading was missed, run-in
  headings read as sentences. `papers.confidence` and `confidence_detail`,
  the `tree` event, the harness report, the card, the sort and the chips.
  It flags; it changes no tree.
  What went wrong on the way, for next time. Ollama was not running (the
  machine had restarted since the 14th): a day of comparisons ran with no
  embedder, every heading no rule names reading `other` on both sides. The
  harness says so in capitals and I read past it; `pairs` now says so too.
  After `ollama serve` the numbers moved in the third decimal, so they
  stand. The worker died once in native code (exit 3221226356, heap
  corruption) on a PDF that read fine alone afterwards; `resume_pairs.py`
  in the scratch directory reads the queued papers and then the one that
  was open, alone. `A && B && (x) & (y) & wait` backgrounds the whole
  and-list, so only the first job sees the variables: set them with `;`.
  The Write tool decodes `\u00ad` into the character itself: write code
  points as numbers. Another session (`claude/docling-bench`, its own
  worktree, data under `~/.protracker/bench`) is measuring Docling's
  backends and scores them with `pairs.compare`.
  The gate, with the embedder up. Pilot clean: lanes gained 28, methods
  sections 6. The held-out sets flag three lanes lost, and all three are
  the old baseline's mistakes coming back into view: PLOS's "Author
  summary" (twice) had been `back` and "Key findings" `results` by the
  hand-picked examples; the learned centroids of the 14th call both
  `other`, the reader then nested them under the abstract where no gate
  looked, and the XML's stated depth brought them back to the top level.
  The catalogue already calls "Key findings" a highlights box; "author
  summary" is one now, and ASM's "OBSERVATION", which had lost its results
  lane the same way, has it back by the catalogue's exact spelling. Beyond
  the buckets: titles, clean papers, errors, citations and front matter
  unchanged on all three corpora; findings in held-out XML 2,047 to 1,984,
  because a section the file puts beside the results is no longer under
  them, and in held-out PDFs 328 to 348 from the headings unfused. All
  seven libraries rebuilt; every paper carries a score. looped-ligament:
  69 of 80 at 0.9 or more, three under 0.5 — `doi:10.1002/adhm.201600096`
  (0.22, a Wiley communication whose built "Introduction" holds the
  results), `doi:10.1089/ten.teb.2023.0222` (0.30, a review read as one
  long introduction), `doi:10.1007/s11229-025-05319-6` (0.40, three lanes
  missing). Across the corpora at 0.9 or more: PDFs 53 of 76, 24 of 35, 94
  of 141; XML 140 of 151, 127 of 162, 160 of 200. `npm run check:all`
  green (172 parser tests, 42 CLI, 10 app), headless e2e 9 of 9. The
  harness runs, the pair records and the calibration are saved beside the
  libraries, `~/.protracker/library/measurements/2026-09-17/`
  (`harness-pilot.json`, `harness-heldout1.json`, `harness-heldout2.json`
  are the baselines to gate against from now on; the step-7 ones lived in
  a session's scratch directory). Nothing of this is committed yet.

- **2026-09-14, later: the type rebuilt on a canonical table, and headings
  canonicalised** — Karim: "every xml is formatted differently, so we must
  canonicalize everything. we also need to get better at the initial
  categorization of paper type", then "implement … strengthen
  categorization; this is hugely important. then … canonicalize different
  headings and start choosing headings from a learned vocabulary". What the
  corpus said first: `research-article` is what 351 of 514 XML files say
  and "Journal Article" what 729 of 747 records say — the publisher's
  default bucket, not evidence; 34 of the 566 papers typed research had no
  methods section. The record's `pubType` already carries MeSH publication
  types for MEDLINE papers (randomized controlled trial 24, multicenter
  study 11, case reports 11, clinical trial protocol 5, observational study
  4 …) and 252 files carry a `<subject>` line in 103 spellings ("Original
  Research", "Brief Research Report", "Study Protocol", "Narrative Review",
  "Letter to the Editor"); neither was read. What was built:
  `paper_type.py` rewritten — one table (`LABELS`) over every source's
  vocabulary that says which labels name a kind and which are defaults;
  subtypes; the title's own words as a source; the shape's rules (lanes, a
  case heading, a letter's opening, a systematic review's headings, a data
  descriptor's, a protocol's future tense); disagreement notes;
  `papers.subtype`. What was measured (`--measure`, run 3): 286 papers
  labelled by a stated specific source (record 249, subject 37; truth:
  review 137, research 86, editorial 22, letter 13, case report 13,
  protocol 5, data 5). Alone against that truth — title 42 answered, 0.93
  right (case report 6/6, letter 3/3, editorial 6/6, review 14/15, rct
  7/8); printed label 24, 0.88 (research 7/7, review 7/9); subject line 77,
  0.94 (review 55/55); the shape 202, 0.87: review 69/70, case report 9/9,
  data 5/5, letter 3/3, research 84/108 (the 24 wrong are reviews with a
  methods section 14, case reports without a case heading 4, guidelines 3,
  letters 2, editorials 1), protocol 5/7. So the shape decides reviews,
  case reports and data descriptors on its own, research only when a
  default label stands beside it, and protocol and letter stay notes. The
  whole cascade with the stated sources hidden — what a PDF without a
  record gets — is right 0.87 of the time on those papers, research at
  0.76 precision. On the corpora the file and the record still settle 97
  in 100; the change is that a default no longer counts as a label and the
  reviews hiding under it are named or noted.
  Run 4, after a paper-by-paper listing (`measure_list.py` in the scratch
  directory) named the confusions: the twelve reviews the shape read as
  research all had a methods lane, a discussion and no results lane ("2.
  Methodology", "5. Extraction Methods", "2. Bibliometric Analysis" among
  topical sections), and no labelled research paper had that shape — but
  the corpora did: Nature-family and PNAS papers set the methods after the
  discussion and their results under the main text's own headings, and a
  PLOS paper's results stood under topical headings, twelve papers typed
  research by default and shape that a lanes-only rule would have lost.
  What separates the two is the order and the body, not the lanes: the
  methods last (ten of the twelve), and measurements — ±, p <, n =, SD, CI,
  mean, median — in the body's paragraphs of 25 words or more: 0.12 to
  0.40 of them on those research papers, at most 0.07 on a review with a
  methodology section (median 0.01), 0.24 median on labelled research
  papers with a results lane, 0.0 median on reviews without methods (third
  quartile 0.03). So the shape's research rule is a results lane, or the
  methods after the discussion, or measurements in a tenth of the body; a
  review with a methodology section is unread now, not misread (its record
  names it anyway; an unlabelled one becomes `other` by default, which is
  honest). Two smaller fixes from the same listing: a protocol named in a
  subtitle ("…: protocol for an 11-hospital multicenter randomized
  controlled trial", "…: The CROSSMIRV Trial Protocol"), which the trial
  rule had read as an RCT, and Data in Brief's headings ("Value of the
  Data", "Data Description") beside Scientific Data's. Run 4: the shape
  alone answered 190 and was right 0.93 (research 97 named, 84 right,
  precision 0.87 from 0.76; review 69 named, all right, 69 of 72 recalled;
  case report 9 of 9; data 5 of 5; protocol 5 of 6; letter 4 of 4); the
  cascade with the stated sources hidden answered 230 and was right 0.90
  (from 0.85). Of the 23 left, nine are the profile kind's (measured only;
  it ships off), and the rest are case reports, letters and a guideline
  written as full research papers, MeSH's "historical article" and
  "video-audio media" on research papers, a "Debate" printed label on a
  comment, and one meta-analysis that is also an experiment — nothing a
  shape can read.
  Headings: the same files gave 12,964 titled sections (7,812 unique
  headings). The vocabulary named 70 % of the 4,890 top-level ones and 7 %
  of the 8,157 subsections; the rest were back-matter statements never
  listed (Associated Data 209, Contributor Information 70, IRB statement
  36, Informed Consent 33 …) and the topical methods vocabulary (Cell
  culture, Western blot, Study population, Outcome measures). Built:
  `headings.py` — the catalogue of thirty canonical names with the
  spellings the corpus uses (5,339 sections matched exactly, 302 by
  family), `nodes.canonical`, an exact top-level spelling settling depth
  and lane where the vocabulary was silent, built headings named from the
  catalogue ("Materials and methods"), and centroids from the harvest for
  the `heading` and `canonical` kinds. Measured library-out (four XML
  libraries, the vocabulary's and the catalogue's word as truth): the
  canonical centroids name a section right 0.993 of the time at 0.7/0.05
  (recall 0.88; Study design and Materials the weakest at 0.95; Abstract
  the one failure, a front-matter name that no body heading should carry,
  now kept out of the centroids). The lane prototypes as one centroid per
  lane put "Discussion" itself nearer "Results and discussion" than its
  own lane (a lane blended from "Discussion" and "Conclusions" spellings:
  results-discussion precision 0.28, discussion recall 0.55), so the lane
  prototypes are the per-name centroids grouped by lane, the best counting.
  After that change, library-out at 0.7/0.05 (shipped for lanes): every
  lane at precision 1.0 — introduction 461 of 464 recalled, methods 337 of
  344, results 247 of 248, results and discussion 83 of 83, discussion 600
  of 604, references 262 of 262, back 1,612 of 1,618; the 1,479 top-level
  headings the vocabulary calls `other` get back 345 (Contributor
  Information, Informed Consent Statement, Publisher's note, CRediT …),
  discussion 50 (limitations, future perspectives), methods 21,
  introduction 7 (Scientific Data's "Background & Summary"), results and
  discussion 6, and 1,128 stay `other`. The canonical centroids at 0.75/0.05
  (shipped for names): precision 0.995, recall 0.81 — Study design 0.95,
  Results 0.96, Implications 0.96, Ethics 0.98, the rest at or near 1.0 —
  and on the 7,109 unnamed headings they name 66 as Statistical analysis
  ("Sensitivity analysis" fairly, "Bioinformatics analysis" not), 39 as
  Materials and methods, 30 Study design, 25 Materials, 24 Results
  ("Outcomes"), 15 Data availability, and leave the rest unnamed. The
  `heading` kind now runs on those prototypes instead of the hand-picked
  examples; `data/headings.json` (306 KB, numbers and the modal spellings)
  carries both, with the thresholds. Every stored heading verdict is
  re-asked once, since the kind's signature changed.
  A fresh-context review of the two modules then found, and the code now
  answers: a heading word the layout model set above the title
  ("INTRODUCTION", "Abstract") reached the type table as a printed label;
  "Response to neoadjuvant chemotherapy …" read as a letter and "Correction
  of hallux valgus …" as a correction; a protocol for a systematic review
  read as the review; the subtype was whichever agreeing label the record
  listed first (MeSH lists alphabetically: an RCT came out "comparative");
  the embedder's stored verdict for a heading ran before the catalogue's
  rule; an exact single-word spelling ("Notation", "Consent") promoted a
  subsection to a top-level section of another lane, and a family
  ("Reference materials", "Image registration", "Contributions of
  macrophages …") laned prose sections as references or back matter; a
  canonical name could contradict its section's lane; the shape's case and
  review rules fired on "3.1 Case study" and "2.3 Quality assessment"
  inside ordinary research papers. The first run of the gate with the
  catalogue also showed the corpus effect of the centroids: on the pilot,
  33 sections the examples had left `other` took a lane (reviews' "Future
  perspectives" and "Limitations …" discussion, "Overview of gelatin"
  introduction, "Fabrication techniques" methods) and two lost one; the
  families now give lanes in the body only and the depth rests on
  two-word spellings. The second gate then lost 44 and 81 citations on two
  Frontiers PDFs: the new prototypes name "Publisher's note" and
  "Generative AI statement" back matter, which the old examples did not,
  and a heading named by meaning at the page's own level stands top-level
  — but Frontiers sets those two statements in the left column under the
  start of the reference list, so the layout model reads them between the
  entries, and the promoted heading cut the list in two (56 entries to
  14). Now a heading promoted while the reference list is open stays
  inside it when an entry stands among the next eight items; a back
  section after the list still opens as its own. A review's "Available
  treatments" read as back matter by meaning (0.72, a margin of 0.06 over
  the next lane, where a real statement lies 0.14 to 0.25 clear), so a
  `back` verdict by meaning needs a margin of 0.12. The same review's "8.
  Regulatory and Ethical Considerations" cleared even that (0.82, 0.1215
  over discussion) and the Ethics family named it beside; what refuses it
  is its number: across the three corpora 3,664 top-level sections are
  back matter, 768 reference lists, 702 abstracts, and the only one of
  those carrying a body number was this heading, while 1,457 body sections
  carry one. So a heading with a body number takes no abstract, references
  or back lane by meaning (the vocabulary's own word still counts: a
  preprint's "7. References" is the list); the section is `other` and the
  Ethics name, whose lane it no longer shares, is dropped with it.
  The last gate of the day (the harness on the three corpora against the
  step-7 baselines, the six libraries rebuilt, the type measured): no
  title, methods section, lane or citation lost anywhere; lanes gained 28
  on the pilot and 12 and 13 on the held-out sets, methods sections gained
  5 and 7. The libraries now: 765 papers, 9,470 top-level sections, 8,339
  of them with a canonical name (88 %; 174 by meaning; 42 built headings),
  the lanes back 3,663 · discussion 1,138 · methods 872 · references 768 ·
  introduction 761 · abstract 702 · results 440 · results and discussion
  130 · other 996; the named share runs from 78 % on looped-ligament (a
  PDF pilot) to 95 % on held-out-2-xml. Types: research 526, review 150,
  other 26, editorial 22, letter 14, case report 13, protocol 8, data 5,
  correction 1 — by the record 249, a default the shape confirms 430, the
  subject line 37, the title 15, the shape alone 14, the printed label 13,
  none 7; a subtype on 90 papers (rct 28, brief report 10, perspective 10,
  comment 6, meta-analysis 5, systematic review 4, observational 4 …); 38
  disagreement notes in 36 papers, from 51 in 49 before run 4. The pilot
  libraries: looped-ligament research 45, review 31, other 4;
  succinylated-collagen review 77, research 62, other 7, editorial 1.
  `npm run check:all` is green (155 parser tests, 42 CLI, 4 app) and the
  headless e2e passes 8 of 8. Karim then asked for it pushed "to litrag",
  with a brief doc on the pipeline and its usage "so as not to confuse
  workflow with deprecated code": `PIPELINE.md`, pointed to from README.md,
  AGENT.md and CLAUDE.md, and all of it committed on
  `claude/decisions-by-meaning`.

- **2026-09-14, the paper's type, the record, and RSC's first page** —
  Karim asked whether the reader knew what kind of paper it was reading
  (it did not), then saw the introduction and the front matter mixed up on
  a PDF whose abstract runs long, then brought an RSC paper (`doi:10.1039/
  d6ra07899k`, "Excitation-dependent evolution of emissive states …") whose
  headings read "RSC Advances, Front matter, Introduction, Experimental,
  Results and discussion, Conclusions" and asked for the front matter to be
  captured — authors and affiliations — and the rest thrown away. What was
  built: `paper_type.py` (the cascade of DESIGN R3.1, steps 1–4 and 6),
  `record.py` (a JATS file's contributors, journal and year; Europe PMC's
  record once at ingest), the columns and the byline in the window. What
  was found on the RSC PDF: Docling reads its two-column first page as
  banner, dates, "1. Introduction" and its first paragraph, the seven
  affiliation footnotes, the licence, *then* the title, the authors and the
  abstract — so the introduction's first lines (39 words) were dropped as a
  label above the title, "1. Introduction" became a notice, the authors
  line was the abstract's first paragraph (its "a" markers counted as the
  article, its "and" as nothing), the paragraphs after the abstract were the
  abstract's, and a displaced tail was rejoined to the licence line because
  the seven footnotes had pushed the true head out of the window. Each of
  those is now a rule (CHANGELOG), and the paper reads: Front matter
  (notices, dates, affiliations a–g, correspondence, authors) · Abstract
  (one paragraph, the graphical abstract) · 1. Introduction (five
  paragraphs, the first whole) · 2. Experimental · 3. Results and
  discussion · 4. Conclusions · back matter · References. Still wrong there:
  the rotated sidebar's "Published on 01 September 2026" inside the first
  paragraph (BACKLOG). What the gate then found: the first version of the
  "cites, so it is the introduction" rule took a Frontiers "Citation: …
  (2026)" line, an Advanced Science affiliation block ("China. 2 Department
  of …"), an "Abstract: … et al." paragraph and a forty-word "To cite this
  article" line for the introduction's opening (four pilot papers, an
  "Abstract" heading demoted to `discussion` in three) — each is now a
  named exclusion, and the rule stays silent when an "Abstract" heading
  comes later. In the held-out sets it fired on forty-three JATS papers,
  and rightly: PNAS, NEJM, OUP and the letters print the introduction
  untitled, and Docling had filed it under the abstract; Nature and OUP
  wrap the body in a "Main"/"Main text" section the vocabulary did not
  know, now the introduction lane. Two more rounds of the gate found what
  the front matter above the title now kept that it should not (IOP's
  "You may also like" titles and authors, Frontiers' editors and reviewers
  with their institutions, a date on a line of its own read as a numbered
  affiliation, "correspondingly" in a results paragraph read as
  correspondence, "Bi2WO6:Yb,Er@CuS@CS" as an e-mail, a key-point sentence
  naming a department read as an affiliation, a Frontiers citation line
  with the DOI on the next line read as the abstract) — each now a rule,
  each a test.
  The gate (the harness against the step-7 baselines, run 16): no bucket
  in any corpus. Pilot, 227 papers: titles 151/151 and 74/76, methods
  56/151 and 62/76, clean 227/227, citations 26,145 (+6) and 5,022,
  dropped sentences 67 in 22 papers (unchanged), built headings 4 — a
  Wiley communication, an AJSM paper, a 2000 J Biomed Mater Res paper and
  an Eye & Contact Lens review, each with its introduction printed
  unheaded. Held-out 1, 197 papers: methods 120/162 and 26/35, citations
  21,145 (+3) and 2,784, dropped 48 in 12 (unchanged), built 6 JATS
  (Nature Communications, JCI, a letter, an editorial) and 6 PDFs.
  Held-out 2, 341 papers: methods 178/200 and 126/141, citations 12,599
  (+3) and 7,679 (−84 in twelve PDFs, one or two citing nodes fewer in
  each: an author line or an affiliation block whose superscript markers
  had counted as citations now stands in the front matter — AJTMH's
  thirteen authors were thirteen "citations"), dropped 148 in 44 (+1: a
  page-2 fragment of `aem.00289-26` that no longer rejoins), front matter
  5.6 nodes a PDF (4.4 before: Frontiers, Cureus and JKMS first pages
  carry their authors, affiliations, ORCIDs, dates and disclosures; JKMS
  16), built 25 PDFs (Nature-family, PNAS, editorials, letters). The e2e
  suite's cap on front-matter nodes is 16 for that reason.
  What was measured for the type (`paper_type --measure`, the file's and
  the record's word hidden): 744 labelled papers (510 by the file, 234 by
  the record). The printed label answered 89, right 81 (0.91): research
  70 of 71 named (0.986), review 7 of 9 (0.78 — two case reports print a
  label the kind takes for "review"; a run before Cureus's "Review began
  …" lines were dates had it at 0.5), editorial 1 of 3, protocol 1 of 3,
  letter 0 of 1, case report 1 of 1, data 1 of 1. The profile answered
  215, right 142 (0.66): research 103 of 114 (0.904), review 31 of 46
  (0.674), letter 3 of 4, case report 3 of 4, protocol 1 of 43 — it calls
  a research paper a protocol 39 times.
  So the page decides research only (`PRINTED_DECIDES`), the profile is
  off unless `LITRAG_TYPE_PROFILE=on`, and `--measure` keeps scoring both.
  On the corpora the file and the record label 97 in 100: the pilot has
  13 `other` in 227, held-out 1 has 2 in 197, held-out 2 has 4 in 341.
  The record: Europe PMC answered for 738 of the 752 papers with a DOI or
  PMID (14 unknown, Zenodo DOIs mostly); the JATS files' own contributor
  groups override it on every rebuild.

- **2026-09-14, edges from a finding to its method** (R3.4 of the plan,
  pulled forward at Karim's request; `edges.py`, the `edges` table, the
  `edges` op, "Measured by" in the window). What it is: for each results
  paragraph (or a discussion paragraph that cites a figure), the methods
  subsection that produced it, by a pointer in the text, else by marks only
  that subsection owns (a heading word the body uses, a word pair it
  repeats, or a word pair the paper says in four blocks or fewer, none of
  them among the paper's commonplaces), else through the caption of the
  figure it cites, else by resemblance when the nearest candidate clearly
  wins — that last only with `LITRAG_EDGES_SIMILARITY=on`. What it did on
  the six libraries (765 papers): 8,249 findings, 5,401 linked (65 %) —
  13 by pointer, 7,312 edges by marks, 1,083 through captions — 2,848
  unlinked; 9,127 figure mentions; 3,986 method parts to link to. A
  paragraph reporting a modulus and a swelling ratio rests on two methods,
  so more than one edge per finding is often right.
  What was measured: only 13 findings in 11 papers carry a true pointer
  ("see Section 2.3"; the first regex also took "(2.4-fold)" for one, which
  the review caught), and the JATS files' section cross-references are
  almost all "Source data" and footnote links, so the truth set is small
  and noisy (a pointer names one of the methods a finding may rest on).
  With the pointer hidden, marks answered all 13 and named the pointed
  section 11 times (0.85); resemblance answered 5 and named it 3 times
  (0.60). The first marks rule (any owned pair, or two owned words) gave
  13,007 edges, linked "time points" and "liquid waste" to methods and
  named the pointed section 9 times in 14; the reviewed rule (heading
  words the body uses, repeated pairs, rare pairs, no commonplaces, no
  preamble paragraphs) gives 7,312 and 11 in 13. Marks ship; resemblance
  is off until it passes on pointers a person has confirmed — the window is
  where those will come from. The e2e suite selects a results paragraph and
  follows its first edge to a methods node (8/8 on the fixture, with the
  fixture paper's first finding required to link).

- **2026-09-13, the oracle of meaning, the content lanes, the boundary
  scorer** — Karim asked where an embedder could make the reader's ad hoc
  decisions, and to build it with a plan, a fresh-context review of the
  code, and honest measurement. What was built: `meaning.py` (one oracle,
  every question a *kind*), six call sites moved off lists, `structure.py`
  (lanes from paragraphs; built headings for a paper with none),
  `boundary.py` (continuation by likelihood). What was measured, on rows the
  rules had already typed, before any of it decided anything:
  - `front` (900 typed lines, seven groups): nearest-right authors 0.89,
    dates 0.85, keywords 0.98, notice 0.82, affiliations 0.76,
    correspondence 0.69, body prose 0.58 (prose lands on keywords/notice).
    At cosine 0.78 / margin 0.05 about one line in a hundred is misnamed
    and a third are named; shipped so. `figtext` (captions vs prose):
    captions nearest-right 0.82, prose 0.47; at 0.70/0.05 one in 140
    wrong, one in six named — so the verdict may only *add* a legend.
    `refentry`: entries nearest-right 0.97, prose 0.62; 0.70/0.05 shipped,
    and the run still needs eight entries at 0.6 density. `label` (back
    headings vs the first three words of paragraphs): labels 0.97 with a
    median cosine of 0.97; at 0.85/0.08 nothing named wrongly, a quarter
    named; shipped, and it is the one verdict allowed to veto a geometry
    join.
  - `block`, the hand-written example paragraphs (60 papers, headings
    hidden): per paragraph near chance — introduction 0.34, methods 0.36,
    results 0.32, discussion 0.23, references 0.64; per section (mean of
    ≥ 3) methods 0.45, results 0.31; every threshold left more than a fifth
    of named sections wrong. Not shippable as a decision. Replaced by
    centroids: one unit vector per lane from every labelled paragraph of
    the six libraries (17,500 embedded, capped at 2,500 per lane) and a
    histogram of where in a paper each lane sits, `data/block_lanes.json`
    (numbers, no text). Library-out over 22,763 paragraphs: per paragraph
    0.70 overall (methods 0.53, results 0.55, references 0.87); per section
    with the prior, methods 0.80, results 0.57, references 0.97; at margin
    0.08, 149 of 1,181 sections named, 2.7 % wrong, and among methods /
    results (alone or with discussion) / references 111 named, 4 wrong.
    Shipped at threshold 0.5 / margin 0.08 for those lanes only, with the
    position prior clamped to ±0.1 so it tips close calls and decides none. On the pilot corpus and one
    held-out library it then lanes **no** `other` section at all: the ones
    it should catch ("2. Case Presentation", "1. Patient Selection") sit at
    margins under 0.01, and a review's topical sections read as
    `discussion` or `references` at margins of 0.02–0.07. Honest and, for
    now, useless; the lead is in BACKLOG. Built headings: no paper of the
    three corpora prints none, so the pass has fired only in tests.
  - `boundary`: the 743 stored judge verdicts were mostly unreachable (the
    rules changed since; 71 pairs, 2 joins remain). Calibrated instead on
    600 pairs cut from the corpora's own paragraphs (a sentence cut before
    a capitalised word: the rest of it, or another paragraph's start; only
    pairs `_judge_candidate` would put to the judge), paper-out: precision
    0.92, recall 0.92 at 1.05 nats/token; at 1.5, precision 0.97, recall
    0.71; joins score p10/p50/p90 1.13/1.81/2.60, keeps −0.19/0.34/0.99.
    On the 71 real
    pairs, five of 69 keeps score above 1.5 (a funding line, an affiliation
    line, sentences ending in a zero-width space — that last now excluded
    from candidacy). Shipped at 1.5, off by default (`LITRAG_BOUNDARY=on`).
    Scoring: ~0.3 s a pair on the CPU; the model loads in ~4 s.
  - The corpus gate (harness with baselines, all three corpora) after the
    change, three times over because it caught things: a tail glued by
    `which` onto a "KEYWORDS …" line (eight citations gone; heads are now
    never front-matter lines), an empty "Author Contributions" section
    dropped by meaning that exposed two adjacent "Footnotes" as an echoed
    heading (empty sections are dropped on the list only), and, before the
    prior was clamped, review sections laned `references` (their citation
    markers then ignored). Final run: pilot 227 papers, titles 225, methods
    118, clean 227/227, citations 31,159 (+44 on one paper); held-out 1
    (197): titles 197, methods 146, clean 194, +90 on one paper; held-out 2
    (341): titles 340, methods 304, clean 332, +119 on one paper. No lane,
    title, methods section or error lost anywhere. Sections laned by content:
    one, in held-out 1 — "3. SEI Characterization" of a review on solid
    electrolyte interphases, taken for results-and-discussion (margin over
    0.08). A person would call it topical; it is the one lane the pass has
    taken across 765 papers, and the row to delete if it offends
    (`verdicts` where `kind='block'`). Built headings: none, no paper printed no heading.
    Headless e2e: fixture 7/7, six real papers 6/6; `npm run check:all`
    green (42 + 4 + 115). All six libraries rebuilt from their raw parses.
  - The vocabulary-off experiment (`LITRAG_VOCABULARY=off`, kind
    `heading-alone`): the embedder alone names every heading. Heading by
    heading, over 826 distinct top-level headings (8,301 sections): abstract,
    introduction, results, results-discussion, references 100 %; discussion
    99.7 %; methods 98.1 % (after "Experimental", "Experimental work" and
    "Experimental design" joined the methods examples: before, 94.8 %, the
    "2. Experimental" family lost to the margin against "Experimental
    results"); back matter 89.6 %, the rest to `other` except "Graphical
    abstract" → abstract on 21 sections, the trap the vocabulary guards.
    Paper level, against the vocabulary-on baselines: pilot 227 papers, 2
    methods lost ("Methods of literature search", "Extraction Methods"), 4
    lanes lost, 9 gained ("Future perspectives" and kin → discussion), 1
    paper's citations lost (the linker fragility in BACKLOG); held-out 1,
    2 methods lost, 4 lanes gained, 1 citations lost (100 → 77), 1 gained
    (0 → 182); held-out 2, 6 methods lost, nothing else. Ten of 765 papers
    lose their methods lane without the vocabulary; the examples carry the
    other 98.7 %, and the lanes it names beyond the vocabulary are right.
    Kept as an experiment switch; the vocabulary stays the free first pass.
  Two fresh-context reviews were run on the code (each a sub-agent given
  only the diff and CLAUDE.md); the first found eighteen things, among them
  a legend droppable on a verdict, a heading demotion that merged prose into
  the abstract, a front boundary moved on an outage, and thresholds outside
  the verdict key — all fixed, with tests. The old `lanes` table in
  `lanes.sqlite` is copied into `verdicts` once and renamed
  `lanes_migrated`; the 9,166 heading verdicts replayed (883 named, 0
  asked) on the first harness run.

- **2026-09-11, on Karim's machine (Windows 11, RTX 5080, driver 616.92)**
  — Revision 2 runs natively: `uv sync --project parser` (torch 2.14+cu130
  from PyTorch's index, uv's managed CPython 3.13), `npm ci` at the root
  and in `app/`, `npm run check:all` green, the headless smoke run parses a
  12-page ELAC PDF on the GPU with boxes drawn. Two Windows bugs found and
  fixed: the worker deadlocked on its first `import numpy` because the
  main thread sat in a blocking pipe read (now polled with PeekNamedPipe),
  and stdio was cp1252. Timings: Docling loads in ~14 s, the first paper of
  a process takes 30–40 s (CUDA warm-up), every paper after it 1.5–3 s.
  Docling's models cache in `~/.cache/huggingface/hub`. The checkout was
  previously driven from WSL (a dead `parser/.venv` symlink into
  `/home/mars`, Linux-built `app/node_modules`); both were replaced.
  Karim then ran the app and added the Micromachines JATS to
  `looped-ligament` (its `store.sqlite` now exists beside revision 1's
  `lit.sqlite`) and saw single-character nodes — "w", "/", "v" — where the
  XML has `(<italic>w</italic>/<italic>v</italic>)`: Docling's JATS backend
  emits styled runs as an `inline` group and the tree builder flattened
  it. Fixed in `tree.py` (inline groups joined back) and the library
  rebuilt with the `rebuild` op: 187 → 140 nodes. The PDF path never had
  this — the layout model drops the italics. Then the equation in 2.4 was
  found missing (Docling's JATS backend skips MathML; `mathml.py` now
  hands it a `<tex-math>`), and Karim asked for a harness that checks each
  node against its neighbours: `audit.py`, the `audit` op, and tests over
  the fixtures. Running it over the library also found the layout model
  cutting an italic "p" loose in the PDF (stitched back now), the Springer
  logo filed as 28 figures (dropped and counted), and "ORIGINAL RESEARCH"
  taken for a title (flagged; the numbered-heading half of that rule is
  fixed). The Springer PDF's equations are empty formula nodes: Docling
  reads formula text only with enrichment on — see BACKLOG. The library
  was reparsed (the JATS) and rebuilt: Micromachines 140 nodes with the
  equation, Synthese 316 → 285. Karim's next paper set: thirty ELAC PDFs
  through the window, then `audit --lib` on them.
- **2026-09-11, later** — Karim: paragraphs split across page breaks in the
  Synthese PDF (mid-citation), and "link in-text citations to the
  reference list so we see which chunks lean on which papers". Both built:
  `_continues` in `tree.py` joins a split paragraph on the same or next
  page; `citations.py` + `refs`/`citations` tables + the `refs` op + the
  window's "→ n" / "[n] ← m" badges. Ground truth on the Micromachines
  JATS: 71 pairs in the XML, 72 found, all 61 entries cited. The
  **archive**: `karimghabra/litrag-archive` (private; "Private backup of
  the literature libraries") is cloned beside this repo at
  `C:/Users/ihave/Documents/liteRAG/repo-review/litrag-archive` (561 MB):
  looped-ligament 52 PDF + 37 XML, succinylated-collagen 33 PDF + 114 XML —
  the same files revision 1 collected. All 236 were ingested into the
  matching libraries under `~/.protracker/library` for testing; papers
  already parsed there were kept, then everything rebuilt so the new rows
  (citations, repairs) exist for all. Figures for XML papers are a backlog
  item (Europe PMC serves them by name).
- **2026-09-11, evening** — Karim: "so many of these papers have garbage
  nodes, no titles, show no methods even though they clearly have one,
  store the author and their affiliations all separately … a harness and
  e2e test would help a lot, especially using playwright". Built
  `harness.py` (corpus measure with a baseline gate) and the Playwright
  suite (`app/tests/e2e`); the harness pointed at the causes and the fixes
  followed: JATS `<label>` folding (the big one — Wiley/Elsevier bodies had
  been filing under Abstract), the first-page title picker with PDF
  metadata as arbiter, typed front matter (`meta` nodes), deduplication,
  running heads. Corpus before → after: PDF titles 41 → 74 of 76; JATS
  methods 33 → 52 of 151 (149 with methods or a review's skeleton); audit
  errors 94 → 52; front matter max 27 → 12 nodes. Baselines live in the
  scratch directory for now — keep them beside the libraries
  (`~/.protracker/library/harness.json`), never in the repository (a list
  of DOIs is a reading list).
- **2026-09-11, night** — Karim's next round, on doi:10.1002/jbm.a.32783
  (Wiley 2010): "1 to 10^6" was Docling reading "×" as "-" and setting the
  exponent apart ("1 - 10 6"); "pH 1/4 7.4" was the Symbol font's "=" as
  "¼" (82 times in 8 Wiley PDFs); the paragraph did not continue over the
  page because the running head sat between its halves in the item list.
  All three fixed (`glyphs.py`; stitching that bridges figures and running
  heads). His idea of a local model judging adjacent pairs is built:
  `judge.py`, Ollama qwen3:14b with thinking off (0.15 s a pair after a
  20 s load; qwen2.5:7b got the seeding-density pair wrong), verdicts as
  rows in `judgments`. 1,112 candidate pairs in the corpus before the
  bridging fix. His question "can we fine-tune Docling?" — answered in
  BACKLOG: possible, not the lever; the failures were the text layer's and
  the tree builder's, not the layout model's; page coverage in the harness
  is the number to watch before touching the model. The judge over both
  libraries: 743 pairs asked, 46 verdicts of "one paragraph", 11 applied
  once a sentence ending in a citation, a heading called text and a
  licence line were kept from it; 3.3 minutes. The harness's new
  `dropped_lines` — sentences in the text layer and in no node, with
  running heads, the title, table cells and figure text excluded — is the
  honest count for "not grabbing all the paragraphs": 156 in 34 of 76
  PDFs; page coverage 0.86 mean. Glyph residue after `glyphs.py`: 12.
- **2026-09-11, late** — Karim: "underwhelming … we need a deterministic
  ingestor … it needs to actually get these things right." Built
  `recover.py`: the PDF's text layer (pypdfium2, page text in reading
  order + character boxes → lines with boxes) read back against Docling's
  boxes: lines no box holds → paragraphs in place; boxes missing lines →
  rebuilt; empty formulas → their box's text; body blocks labelled footer
  → kept; table footnotes → paragraphs; indent and last-line width on
  every block. Geometry now decides continuations (712 joins) and the
  model is asked 4 times across the corpus. Corpus: errors 50 → 22,
  dropped sentences 156 → 83, clean PDFs 66 → 71, citation links +21
  papers. Lessons: pdfium's `get_rect` rectangles are runs in stream
  order, not lines (use `get_text_range` + `get_charbox`); pdfium marks a
  line-end hyphen with U+FFFE; a line's centre can sit a hair outside its
  box, so "free" must also mean "its words are in no box's text"; a short
  last line must never overrule the words (a figure may have cut the
  column). The judge earns little now; the page earns most.
- **2026-09-11, night** — Karim: "find 15 systemic defects … rank them by
  impact and get to work, I want them all fixed." Three probes over the
  corpus (`probe_defects*.py`, in the scratch directory) found them; the
  two worst were the recovery pass's own: it read only an item's first box,
  so the second page of every paragraph Docling carried over a page break
  (722) came back as a duplicate block (508 of 518 "recovered") and the
  geometry rule then glued those tails to wrong heads (267 joins across a
  full stop) — and pdfium's line breaks run a paragraph's first lines
  together, so first-line indents read as zero. Both fixed (`recover.py`
  reads every box; rows are cut by baseline from the character boxes),
  then: superscripts marked ("10^7", "applications.^17") not fused; Wiley's
  rotated sidebar unglued from the body blocks Docling attached to it; the
  Symbol font's control codes and the oldest Wiley files' digit-symbols
  (`glyphs.py`, 113 → 0); split ligatures mended by the paper's own words
  (1,658 → 2); JATS `<element-citation>`s rendered and Wiley's a)/b)
  sub-references merged before Docling reads the file (57 papers
  reparsed; JATS citations 21.5k → 25.9k); an inferred References section
  for Wiley PDFs with no heading, ACS bullets stripped, entry numbers from
  the printed numbers (PDF citations 3.1k → 4.9k, 61 of 66 papers
  linked); front matter after the abstract's heading, "A B S T R A C T" as
  text, numbered "Summary" as a closing section, comma-list author lines,
  empty wrapper sections dropped; unstructured tables from the layer;
  Europe PMC title lookup for hash keys (3 of 9 found). Corpus, day start
  → now: errors 94 → 21, clean 165 → 212 of 227, dropped sentences 156 →
  75, citation links 24.2k → 30.8k. Both libraries rebuilt with the new
  rows. Lessons: the harness's "dropped sentences" was flattered by the
  duplicates (53 then, 75 now is the honest number — see BACKLOG); a
  hyphen's glyph box sits at mid-height and looks raised, and pdfium
  gives some glyphs a sliver of a box, so a superscript must be a digit
  with a real box; Docling's list items carry their number in `marker`,
  not in `text`; Bash heredocs on this machine mangle backslashes and
  non-ASCII (write such files with the editor tools). The three papers
  already filed under a hash keep their keys until dropped and read
  again.
- **2026-09-11, later that night** — Karim: "I want all the papers to audit
  clean … can you fix these at their source?" The 21 errors in 15 papers
  were: 5 the audit's own (a colon before a list or a heading; an equation
  written as prose in the next paragraph), 8 formulas Docling drops from a
  JATS (LaTeX-document `<tex-math>`, `<alternatives>`, markup-only
  formulas, a formula alone in a `<p>`), 2 equations the files carry only
  as images, 6 fragments and stray symbols. All fixed at the source
  (`render_formulas` in `jats_prep.py`, the audit's colon rule, four
  stitching rules); the image-only equations stand as formula nodes that
  say so and grade as a warning. 227 of 227 clean; nothing else in the
  harness moved. Seven JATS papers reparsed, both libraries rebuilt.
- **2026-09-11, the held-out check** — Karim: "Did you tune this so it
  would only work on this set of papers? … Start trying other Europe PMC
  articles." Honest answer given first: the mechanisms are general, the
  glyph table and the vocabularies are induced from the corpus, a few
  thresholds are in absolute points. Then 164 papers from Europe PMC in 24
  unrelated fields (`fetch_heldout.py`, `ingest_heldout.py` in the scratch
  directory; libraries `held-out-xml` and `held-out-pdf` under
  `~/.protracker/library`, delete when done). First run: titles 154/162,
  three JATS papers read as nothing, 9 papers with a reference list and no
  links, 36 empty list items in one review. Every one traced to a mechanism
  (see CHANGELOG) and fixed; second run 162/162 titles, 160/162 clean,
  links in all but two. The pilot corpus gate stayed green. What the
  held-out set did not show: any glyph-table misfire (residue 0 on 35
  PDFs from other publishers) — the context gates hold, though the table
  is still a table. Left: two ACS Omega XMLs whose in-text citation
  markers are not in the XML at all (unlinkable), the Lancet report's
  magazine layout (22 dropped lines), a Nature Communications table Docling
  drew without cells.
- **2026-09-12, early** — Karim: "keep ingesting more papers and try to
  generalize our approach more." A second held-out set: 204 Europe PMC
  papers, 30 topics, 2000 onward (`fetch_heldout2.py`; libraries
  `held-out-2-xml` 200 and `held-out-2-pdf` 141). Generalisation done
  first: every point threshold became a multiple of the paper's body line
  height, with identical numbers on every corpus. Then what the set showed:
  Europe PMC's processing instructions read as text by Docling, methods
  headings in five more phrasings (plus a keyword fallback), an abstract
  above the title thrown away as a label, author lines and licence lines
  taken for titles, RSC titles below the banner, a truncated "10.1073/pnas"
  DOI key, a race in the window's reference list. Two PNAS PDFs crash
  Docling's layout stage with an access violation (exit 0xC0000005) — the
  worker dies with them; the ingest driver skips "pnas" for now. Every
  gate green except one pilot paper whose abstract-side citing paragraphs
  are front matter now (39 → 25 links; the corpus total rose). The
  harness's compare keyed papers by key alone and mixed a DOI's XML with
  its PDF — fixed. The held-out sets are the guard now: run all three
  before any claim.
- **2026-09-12, morning** — Karim: "it feels like we're ingesting more
  papers, recognizing the patterns within them, and just adding their
  pattern into our checker … a truly novel paper won't be caught … an
  embedder: embed the headings, see they all mean methods, still
  deterministic." Right, and built: `lanes.py`. First the measurement —
  the 985 distinct top-level headings across the three corpora embedded
  with nomic-embed-text (14 s, bit-identical on a second pass): the
  embedder agreed with the vocabulary on 395 of 413 headings it decided,
  and of the 572 it called `other`, the nearest lanes were exactly the
  tail ("Results/Discussion", "Strengths and limitations", "Objectives",
  "Data Collection and Outcome Assessment", Wiley's "3 | Results" — that
  last one a normaliser bug, fixed). Thresholds from that data: cosine ≥
  0.75 and a margin ≥ 0.08 over the next lane; `abstract` and
  `references` excluded (the embedder puts "Graphical abstract" and
  "Highlights" near abstract, "Article history" near references — the
  vocabulary names those exactly). Verdicts live in
  `~/.protracker/library/lanes.sqlite` (9,000 rows after one pass over
  the corpora, 270 named). Two things learned wiring it in: a lane found
  by meaning must not promote a subsection ("Statistical analysis" under
  Methods) to top level, so `infer_level` asks the vocabulary alone; and
  the word rule I had added the night before ("a heading containing
  'methods' is methods") had mislabelled eleven reviews' topical headings
  — the embedder leaves them unassigned, so the pilot's methods count
  fell and is now right. Next candidates for the same treatment: the
  front-matter kinds (`_front_kind`), the run-in labels, the reference-
  entry shapes.

- **2026-09-11** — Revision 2 built on branch
  `claude/tissue-engineering-literature-rag-srz9ja`: `parser/` (Docling
  worker, tree, store, 17 tests), `app/` (Electron window, protocol tests,
  headless smoke run), documents rewritten. Fixtures are the two real
  papers: NAR 2011 (PMC3258128, `doi:10.1093/nar/gkr715`) and Micromachines
  2024 (PMC11278924, `doi:10.3390/mi15070851`, the ELAC crosslinking paper,
  as PDF and as JATS). Not yet run on Karim's machine: `uv sync --project
  parser`, `npm --prefix app install`, `npm run app`, then thirty ELAC PDFs
  through the window to find the next layout failures. His pilot library
  `looped-ligament` under `~/.protracker/library` has revision 1's
  `lit.sqlite` beside where the app will write `store.sqlite`; the two do
  not collide.

- **2026-09-03** — Initial commit. Not yet run on the user's machine: the
  first local session is `npm link`, `ollama pull qwen3:14b`, `lit doctor`,
  `lit init "Looped Ligament"`, the two ELAC searches, `lit refresh`, then
  `lit wanted` for the PDFs to collect and `lit config --extract ollama`
  + `lit extract` once Ollama is up. Then the bench questions with
  `--trace`, and the model-stage entities should show up as seeds.
