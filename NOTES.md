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
