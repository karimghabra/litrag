# Backlog

Where wants wait until they are built. Strike entries that get built; add
wants as they are voiced.

## Named priorities

- **Label the finding→method truth set** (Karim, 2026-10-04) — ~100 findings across 20–25 papers
  in **Label links** (Papers tab), drawn by `label_queue` across papers and publishers, linked
  and unlinked mixed; then `python -m litrag_parser.truth --lib … --measure`. It decides three
  things waiting on it: whether similarity (`LITRAG_EDGES_SIMILARITY=on`) earns its place (gate:
  precision ≥ 0.9 on the labels), whether hydration's paragraph beats the method's first, and
  which evidence kind drops weak marks. Labels survive rebuilds and merges; `--export`/`--import`
  carry them between machines. Still not built after it: the parameter miner over a method's
  chosen paragraph, and following "as previously described" further than one paper.
- **Measure the port from `claude/ingestion-generalization`** (2026-09-30) — citations
  (e55e3d9), the type (b97151c, 80ec4d0) and `_tight` (a6c7353) are on `main` since PR #25,
  with tests but no corpus numbers: the corpora were not where the port was done, and it was
  merged before they were taken. NOTES.md (2026-09-30) says what each commit can move and which
  command reads it; the before is a362248. Release on the numbers, not on the branch's.
- **R3: the type of a paper, a vector per node, edges from a finding to its
  method** (Karim, 2026-09-13) — the plan is DESIGN.md § R3, in the order
  each step earns the next: type from the file, the record, the page, then
  the paper's shape; headings read with the type in mind; a `vectors` table;
  `measured_by` edges with their evidence; a `query` op that walks them. Each
  step gated on a measurement written to NOTES.md before it decides.
- **Spot-check thirty papers in the window** (revision 2's first job) —
  every layout failure becomes a fixture in `parser/tests/fixtures` and a
  rule in `tree.py`. Watch for: headings the layout model drops or merges;
  tables split across pages; captions attached to the wrong figure;
  supplementary sections.
- **Europe PMC in the window** — search, tick-to-stage, fetch JATS into the
  library; `src/sources/europepmc.ts` already does the calls, the worker
  should learn `search` and `fetch` ops (Python `httpx`) so the window has
  one wire.
- **Wire the retrieval loop to the tree store** — *search and hydration done
  2026-09-23* (`retrieve.py`, the `query` op, the Query tab: paragraph nodes
  embedded once with their headings, words and meaning fused, every hit
  hydrated with its neighbours and its methods; on 14 proxy questions the
  answer is in the top 3 for 14 with its context, against 8 for `lit query`).
  Left: the graph walk (HippoRAG's personalised PageRank over `refs` and
  `edges`), the miner and the model stage over `nodes`, and Karim's own bench
  questions; then strike `src/chunk.ts`, `sections.ts`, `pdf.ts`.
- **Node summaries** (PageIndex's idea) — one line per section from the
  model stage, stored on the node, so an assistant navigates a chosen paper
  by reading rows. `lit toc <paper>`.

- **Exclude review articles from profiling** (Karim, 2026-09-07) — Skip
  reviews when selecting papers to profile, including systematic reviews and
  meta-analyses. Their summaries of other studies must not become profiles
  of experiments attributed to the review itself. Keep reviews available for
  reading, retrieval, and reference discovery. Show them as excluded from
  profiling rather than pending or failed, and distinguish existing review
  profiles from primary-study profiles in comparisons.
- **Preserve completed profiles; do not reprofile automatically** (Karim,
  2026-09-07) — A paper that has already been profiled should reuse its saved
  profile. Normal ingestion, refresh, application upgrades, restarts, and
  model changes must not silently queue completed papers for profiling again.
  Process only papers without a completed profile and resume unfinished work.
  Reprofiling must be an explicit user action, with the affected papers shown
  before it starts. Preserve profile data and completion markers across
  migrations; keep profiling status distinct from extraction and embedding.
- ~~**Collect mode**~~ (Karim, 2026-09-03) — *built: the studio's Collect
  PDFs window (`app/src/main/collect.ts`, e2e test 4), walking `wanted`
  most-cited first; #1 closed 2026-09-30.* The one step of the loop that
  needs a screen: an in-app browser (in Protracker's Research tab, or a
  small window of litrag's own) that walks the `lit wanted` list, opens
  each DOI through the institution's proxy, lets the user sign in once and
  click the PDF, catches the download into the inbox as `<key>.pdf`, and
  moves on. Electron: a WebContentsView on a persistent session partition,
  `session.on('will-download')`, the PDF plugin off so inline viewers hand
  the file over. The click stays the user's. A few days, with a Playwright
  test against a fake publisher page.
- **A bake-off of local models** on ten papers for the model stage:
  `qwen3:14b` (default), `gemma3:12b`, `qwen3:32b` at 4-bit; for
  embeddings `nomic-embed-text` (default), `bge-m3`, `mxbai-embed-large`.
  Judged on the rows they produce for the same sections, by hand.
- **A test set of bench questions** with known answers, LitQA2-style —
  ten from the user's own papers — so retrieval changes are measured, not
  eyeballed. Retrieval-only first (is the right chunk in the top five),
  then answers.

## Found reviewing revision 2 (2026-09-11)

Each one wants a fixture or a test before its fix, as the spot-check does.

- **DOIs are filed as printed** (reproduced) — `file_paper` and the PDF
  sniff keep a DOI's case, so `10.3390/MI…` and `10.3390/mi…` file one
  paper twice. The CLI already lowercases and strips `doi.org/`
  (`normalizeDoi` in `src/db.ts`); the parser should do the same, before
  the retrieval loop joins the two stores on keys.
- **A PDF and its JATS in one drop: the last one wins** (reproduced) — the
  PDF is still `queued` when the XML is filed, so it is not "already read";
  both are parsed, the XML's rows replace the PDF's, the paper ends with
  `pages = 0` and no boxes, the PDF sits orphaned in `papers/`, and its raw
  Docling document is overwritten — against invariant 3. File a paper's
  second format as a sibling, or prefer the PDF for geometry and keep one
  raw document per format.
- **A crashed worker is never restarted** — `main.ts` starts a worker only
  while `worker` is null, and it stays set after the child exits; every
  request then fails "worker is not running" until the app is relaunched.
  Restart on exit (with a backoff) or check the child, not the object.
- **Closing the window mid-parse leaves the paper `parsing` for good** —
  `quit` ends the worker with `os._exit`, and nothing resets the status on
  the next start, so the card's progress bar never stops. On `ready`, set
  `parsing` rows back to `queued`.
- **The PDF sniff takes the first DOI on pages 1–2** — a cited paper's DOI
  printed before the paper's own files it under another paper's key, and if
  that paper is already in the library the new PDF is silently kept as it.
  Check the DOI against the title Docling reads, or prefer a `doi.org/`
  link in the header or footer.
- Smaller: the wire is the worker's stdout, so any library's `print`
  corrupts a line (write events to a dup of the fd and point `sys.stdout`
  at stderr); `run_select`'s docstring claims a read-only handle it does
  not have (the subquery wrap is what keeps it safe); a hash stub that
  gives way leaves its files in `papers/` and `parsed/`; a file dropped in
  the inbox that is "seen before, kept" stays in the inbox.

## Borrowed ideas, not yet built

- **Rerank and summarise before answering** (PaperQA2's "RCS"): the model
  scores each retrieved chunk 0–10 for the question and writes a short
  summary; only the top summaries reach the answerer. Their largest
  precision gain; one Ollama call per chunk on the GPU.
- **Verification questions in the model stage** (ChatExtract): after the
  rows come back, ask the model per row "is this value stated in this
  text?" and drop the noes.
- **Sentence offsets on model rows**, so every claim can be audited back
  to the exact sentence, as the 2026 schema-constrained biomedical
  extraction paper does. `parameters.sentence` already carries the miner's
  sentence; the model's rows carry `context` in the model's words.
- **Forward citations** via Europe PMC's `citations` endpoint; `snowball`
  only walks backward today.
- **Section-aware chunk sizes** as in the lab's own scripts: conclusions
  kept whole, methods grouped by adjacent paragraphs, ~450-token target.
  Worth an A/B on the test set before copying.
- **`lit ask`**: the fully offline answerer — send the retrieved chunks to
  Ollama and print the answer with `[S1]` labels, as the lab's LM Studio
  script does — for a corpus that should never reach a cloud assistant.
- **Synonym edges** between entity nodes by name embedding (HippoRAG 2's
  synonym edges): "EDC" and "carbodiimide" are one node's worth of meaning.

## Observed, not urgent

- **The installer, left for later** (2026-09-30):
  - `app/tests/smoke.mjs` still answers a `prompt()` for New project, which the Projects tab
    replaced with a form, so it only passes against a root that already holds a library.
  - An install into a root other than the default (`LITRAG_INSTALL_ROOT`) gets a Start Menu
    entry that does not carry `LITRAG_VENV`, so the window looks in the default root; the
    Linux `.desktop` entry does carry it.
  - No macOS build: a Mac runs from source.
  - The archives are unsigned, so Windows warns about a file from the internet once.
- **An inferred reference list can open inside the discussion** (found 2026-09-30, porting
  9df932b) — `_REF_ENTRY`'s "Surname, Name" alternative takes "However, Smith and colleagues
  reported in 2019 …" for an entry, and `_infer_references`' run passes over three blocks that
  are not entries as long as one follows: in an author–year paper with no References heading,
  the last three paragraphs of a discussion before the list are filed as references. The
  branch's `_BIB_TAIL` carry makes that unbounded, which is why it is not on main; carrying a
  column-cut entry's tail wants a test that tells a tail from a sentence first (a tail is short
  and has no verb; a discussion paragraph is neither). Unmeasured how often it happens.
- **A heading-less letter or editorial links no citations, and its length is not seen** (found
  2026-09-30) — main files the prose of a paper with no headings as front-matter `meta`, which
  `citations.py` does not read for markers and `shape_of` does not count as words, so the
  editorial rule's 3,000-word ceiling passes a long heading-less paper as short.
- **Sentences still in the text layer and in no node** (after the
  fifteen-defect round, 2026-09-11 night): 75 in 25 of 76 PDFs, from 156
  at the start of the day. (The 53 measured before the round was flattered:
  the recovery pass then duplicated the second page of every spanning
  paragraph, and the duplicates covered lines by accident.) What is left:
  code and command listings in a supporting-information PDF (17; not prose,
  by design), figure legends set inside a paragraph's box that repeat the
  paragraph's terms, table rows the layer reads as prose, and a few lines
  inside figure boxes. `npm run harness -- --worst 20` lists them with
  samples.
- **Sentences the layout model drops** (before recover.py; the measure that led to it,
  2026-09-11): 156 sentences in 34 of the 76 PDFs are in pdfium's text
  layer and in no node — after running heads, the title, table cells and
  figure text are excluded. Samples: "gelatin demonstrated a higher spare
  respiratory capacity compared to…" (adma.202416260 p24), "SORP,
  Becton-Dickinson) to select for cells that were CD44+" (jbm.b.34279 p4),
  a boxed glossary (survophthal p2). Two remedies to try, in order: recover
  the text layer's lines that fall inside a picture's box when they read as
  prose (Docling files a text column under a wide figure), and only then
  the layout model itself. `npm run harness -- --worst 20` lists them with
  their pages; `--show <key>` prints the samples.
- **Fine-tuning Docling** (Karim asked, 2026-09-11) — possible but not the
  lever for what the corpus shows. Docling's layout model (`docling-layout-
  heron`, an RT-DETR detector) and TableFormer have open weights and can be
  fine-tuned on DocLayNet-style page annotations; that is days of labelling
  and training, and it would only move the failures that are the layout
  model's: dropped blocks (measure them first — the harness's page
  coverage) and mislabelled headings. The failures seen so far were not
  its: symbols mangled by a PDF's fonts (the text layer's, fixed by
  `glyphs.py`), paragraphs split at page breaks (nobody's — Docling does
  not join; the rules and the judge do), JATS labels and processing-meta
  (Docling's XML backend, fixed before it reads). Next levers in order: (1)
  keep the glyph table growing from the harness's residue count; (2) OCR
  for PDFs whose text layer is bad — Docling's `do_ocr` with full-page OCR
  on pages with a high residue, RapidOCR on the GPU, a few seconds a page;
  (3) fine-tune the layout model only when coverage shows it dropping
  blocks on a kind of page the corpus has many of.
- **The judge's misses** — qwen3:14b kept "Empirically plausible models of
  amoeboid chemotaxis" and "( K ≈ 2 ) (Sect. 5) and …" apart (the rules
  join it, so no harm) and qwen2.5:7b split the seeding-density pair;
  verdicts are rows, so a second opinion is a `--model` away and a wrong
  one is a `DELETE`. A test set of fifty labelled pairs from the corpus
  would let models be compared rather than trusted.

- **What the harness still lists** (2026-09-11 night, 227 papers): 6 PDFs
  with no methods section and no review skeleton — two are
  supporting-information documents keyed by hash, the rest have headings
  the layout model did not label; 2 PDFs untitled (both SI documents with
  no first-page title); 0 audit errors — every paper audits clean, the two
  equations the files carry only as images (advs.202518807,
  s41598-026-52575-8) stand as formula nodes that say so and grade as the
  warning `image-formula`; PDF front matter up to 14 nodes where IOP
  prints related articles on the first page and where keywords, dates and
  notices now file there rather than under the abstract. Run `npm run
  harness -- --lib <dir> --worst 20` after any change to the reader.
- **What the held-out set left** (2026-09-11, 164 Europe PMC papers in
  other fields): ACS Omega XML carries no in-text citation markers at all
  (the numbers are in the PDF only), so those papers link nothing from the
  XML — the PDF, or Europe PMC's annotations, would be the source; the
  Lancet Countdown report's magazine layout drops 22 lines to figure boxes;
  a Nature Communications table Docling drew with no cells (its rows are in
  the layer, but the box is a table with a grid of empty cells); Sci Rep's
  reference list can carry the same entry twice (the audit's `echoed-node`
  is right). The glyph table's context gates held on 35 PDFs from other
  publishers (residue 0); decoding symbols from the PDF's own fonts is
  still the way to make it general rather than observed.
- **Lanes from content, the next step** (2026-09-13) — `structure.py` names
  a section from its paragraphs only for methods, results and references,
  only at a margin of 0.08, and on the pilot corpus that names nothing an
  author left unnamed: the sections it would have to catch ("2. Case
  Presentation", "1. Patient Selection") sit at margins under 0.01. What
  would move them: the language cues Figure 2 of the design note lists
  (past-tense procedure with units and vendors; figure and table references
  with statistics) as a second score beside the centroids, and the heading
  lines nearby; measure library-out as `calibrate_block2` did before any of
  it decides. The table-note openers (`_NOTE_START` in `recover.py`) are the
  one list not yet moved onto the oracle; they are cheap and rarely wrong.
- **A subsection inside the reference list zeroes the links** (2026-09-13,
  found by the vocabulary-off run) — when a heading the reader does not
  recognise ("Supporting information") nests under References as a level-2
  section, `citations.py` counts its paragraphs among the entries and every
  link is lost (47 → 0 on `doi:10.1002/jbm.b.35120`). The linker should take
  entries from the list items and entry-shaped paragraphs only, and a prose
  subsection under References should be a sibling, not an entry.
- **A keywords line read as a heading swallows the subsections after it**
  (2026-09-14) — Frontiers' "KEYWORDS" line is a `section_header` in
  Docling's reading, the vocabulary lanes it `back` and it stands top-level,
  so "1.2 Three models tested in this paper" and "1.3 …" nest under it
  instead of under "1 Introduction" (`doi:10.3389/fgene.2026.1864752`). A
  heading whose canonical name is a front-matter line (Keywords,
  Highlights, Graphical abstract) should become a `meta` line with the
  paragraph after it, not a section.
- **The block centroids on an unheaded stretch** (2026-09-14) — a Wiley
  communication prints no heading between the abstract and "Experimental
  Section"; the stretch (introduction, results, discussion) now goes to
  `structure.build_headings`, but the shipped centroids call its results
  paragraphs `references` (0.66–0.74, margins under 0.08) and its
  introduction `introduction` by 0.02, so nothing is sure and the whole
  stretch is one built "Introduction". Centroids from paragraphs of
  unheaded papers, or the type-conditioned lanes of R3.2, would let the
  cut happen; until then the built heading is honest about who wrote it
  and wrong about what the later paragraphs are.
- **Content lanes for the subsections under "Main"** (2026-09-14) — Nature's
  JATS opens the body with a "Main" section whose own paragraphs are the
  introduction and whose titled subsections ("Accuracy across complex
  types") are the results; the heading is now the introduction lane, so
  those subsections inherit `introduction`. `lane_sections` names only
  top-level sections; a level-2 section under an introduction-lane wrapper
  whose paragraphs are clearly results (or methods) should take that lane
  the same way, verdict stored. Forty-odd held-out JATS papers are shaped
  like this (PNAS, NEJM and OUP print the introduction untitled instead,
  and get a built heading where the paragraphs start citing). The same
  papers' results headings at top level ("GWAS meta-analysis", "Pathway
  analyses" in a Nature Genetics paper) are laned `methods` by meaning;
  the type's shape rule reads such a paper by its order (the methods
  last) rather than by those lanes, but the lanes themselves are wrong.
- **Heading depth, first** (2026-09-19, done the same day: `depth_by_type`,
  faithful 0.832 → 0.938 against an oracle of 0.935; NOTES.md) — the oracle (`measurements/
  2026-09-19/oracle_depth.py`: each PDF heading's depth taken from its XML
  twin) is worth 61 per cent of all the remaining loss: faithful by words
  0.832 → 0.935, well matched 126 → 152 of 199. The first probe of depth
  from the type gets 0.81 of unknown unnumbered headings right (the reader
  0.77); fix the probe's own faults (anchors only from exact top-level
  names, skip a bullet span, capital heights unrounded, rows matched by
  the heading's box), then wire it into `infer_level`, then a
  headings-only model call (the heading list with each row's style and
  first sentence, levels back) for the papers whose styles tie. Gate:
  depth agreement and faithful on the pairs, the oracle as the ceiling.
- **A PDF heading's depth from its typography** (2026-09-17, the pairs' first
  finding) — the layout model calls every heading level 1, so the reader
  nests any heading the vocabulary does not know under the section that
  stands open. For an unnumbered review that is every topical section
  under "Introduction": in 13 of the 199 pairs (three of the pilot's 31)
  the introduction holds 60 % or more of the body, and 11 of the 13 are
  seriously mismatched, where the XML says those sections are the paper's
  top level. The XML side is fixed (its stated depth stands); the
  PDF side needs the page. Measured the same day on the 958 unnumbered,
  unknown PDF headings that have an XML twin (136 top-level, 822 deeper):
  the box height does not tell them apart (both medians equal the height
  of the paper's known top-level headings, so a size rule would raise
  360 to 750 subsections to find 100 to 132 tops), nor does the left edge
  (94 tops and 745 subsections share it). Capitals do, where a paper uses
  them: with the known tops set in capitals, 7 of 7 unknown headings in
  capitals were top-level and 89 of 89 others were deeper — precise, and
  five in a hundred of the tops. The font itself is read now
  (`typography.py`, 2026-09-18): every line's runs of font, weight and
  size, the body's style, and the lines set apart from it — used for the
  headings the layout model misses, not yet for depth. What is left is
  the depth: cluster the styles of a paper's heading rows (size, weight,
  face) and let `infer_level` raise a heading set in the style of the
  paper's known top-level headings when the subsections' font differs.
  Gate on `pairs` (depth agrees, faithful on the reviews) and the harness.
  The confidence score flags these readings today ("the introduction
  holds" as its reason).
- **Author-manuscript XML for the pilot's own PDFs** (2026-09-17; *the host
  decided and the fetch built 2026-09-30*: Karim allowed NCBI, and `fetch`
  asks E-utilities' `efetch` by PMCID when Europe PMC's XML is not there) —
  of looped-ligament's 43 PDFs, 28 are in PMC (23 as NIH author
  manuscripts), and Europe PMC's REST service answers 500 for all of them.
  Left: fetch the 23 on Karim's machine and pair them with the PDFs he holds
  (`pairs.py`), which makes the pilot's hardest PDFs — Wiley, Elsevier,
  IOP — comparable with their XML. The five publisher deposits stay closed:
  NCBI will not give them out either.
- **More XML to fetch** (2026-09-30, asked by Karim; checked live on the
  pilot's DOIs):
  - *bioRxiv and medRxiv* — their API (`api.biorxiv.org/details/<server>/<doi>`)
    names a `jatsxml` for every preprint, open and without a login: the one
    open route not yet taken, for the preprints Europe PMC indexes without
    full text.
  - *The publishers' text-mining routes, behind the institution's licence* —
    Crossref names them per DOI (`link`, `intended-application:
    text-mining`): Elsevier's Article Retrieval API gives `text/xml` for
    Acta Biomaterialia and Biomaterials, most of the ELAC canon; Wiley and
    SAGE (Mary Ann Liebert) give `full-xml`. Each needs Karim's API key or
    TDM token and his institution's entitlement, and "no automated
    downloading behind a login" says no today: licensed text mining is the
    publishers' own sanctioned route, so it is his call, not a default.
  - Not worth a route: IOP (HTML and PDF only), ACS (no text-mining link),
    Unpaywall, CORE and Semantic Scholar (PDFs or plain text, no JATS).
- **Work stranded on unmerged branches** (found 2026-09-30; *the live fixes
  ported the same day*) — `collect-mode` (PR #2, closed unmerged on
  2026-09-11) held 20 commits to the `lit` CLI that never reached `main`; PR
  #24 ported the four that fixed live bugs (#12, #13, the µm/°C repair and
  its spacing guard). The 16 left are the work issues #3, #5, #9, #10, #11,
  #15, #16, #17 and #18 were closed on — `lit collect`, notes, profiles,
  sync, a format-aware inbox, reviews left out of queries — CLI verbs the
  studio has partly replaced; `reader-graph-verbs` (PR #7) holds one more.
  From `claude/ingestion-generalization` PR #25 ported the citation styles,
  the type's shape rules and `_tight` (measure them: the entry at the top);
  left there are the `_BIB_TAIL` carry (held back: it misfiled a discussion),
  `changes.py` (a page-by-page record of every change the reader makes) and
  its report pages. Karim to decide which of what is left to port and which
  to let go.
- **The paragraph classifier** (2026-09-17, Karim's idea) — train a small
  head on the embedder over the XML corpus's paragraphs, each labelled by
  the lane of the section it sits in, and validate it on the PDF side of
  the pairs, where every paragraph has an XML twin; the cheap, always-on
  companion to the outline judge, and a better signal for the confidence
  score than the nearest-centroid `block` kind that measured weak.
- **The outline judge's depth** (2026-09-17) — the model nests a flat
  review by sense where the journal's XML keeps it flat, so its depth is
  not taken; the font route (above) is still the way to a PDF heading's
  depth. And the judge's lane for a numbered subsection is a note only:
  where the numbering and the model disagree, a person should look.
- **Lanes by position in a research paper** (2026-09-19) — an unnamed
  top-level section between "Methods" and "Results" (BMC's
  "Neurophysiological measures") is `other` on both sides of the pairs,
  so its paragraphs link to no finding; by the IMRaD order it is methods.
  A rule for research papers only, applied to the XML and the PDF alike so
  the pairs do not move, measured on the finding→method links.
- **What the reading log would have to show** (2026-09-19, from `review.py`'s first run) —
  the log prints a section's own paragraphs, so a section whose prose sits in its subsections
  reads as empty and the model calls it no heading: 50 of its 74 structural claims are wrong
  that way, "2 Methods" and "2 Results" among them. Before that pass is measured again the log
  must show the subtree a section holds (its subsections' paragraph numbers, or a count), and
  the ops it may answer should be the ones a policy can act on. Until then `LITRAG_REVIEW=notes`
  is the only setting worth running, and its rows are evidence, not a reading.
- **The displaced-head join cannot be widened** (measured 2026-09-20, `joinreach.py`) — a tail
  that opens lowercase after a head that does not finish is the strongest join signal there is,
  and labelling every candidate by the paper's own XML on 63 novel papers it is still only
  0.457 right at the nearest unfinished head, 0.042 two heads back, 0.000 three back. A head
  ending in ";" is right 0.200 of the time (a bulleted list, or a formula's "where" clause),
  against 0.560 for the rest — the one tightening the numbers support, worth about 8 wrong
  candidates over 63 papers, and not yet taken. Reaching further back, or across more than one
  page, is refused at any threshold that keeps a silent merge rare.
- **A heading that reads as prose is usually a heading** (measured 2026-09-20, `proseheads.py`)
  — 161 of 214 such headings over three corpora are ones the XML has too. So RSC's author
  biographies, set in the heading font and cut across three blocks, still open sections and
  still swallow a review's introduction (1,182 words on 10.1039/d6ra02771g). The narrow shape
  that might reach them — two consecutive headings that read as one continuing sentence — is
  untested, and must be measured the same way before it is written.
- **A held-out set drawn by publisher, not by topic** (2026-09-20, from the generalisation
  measurement in NOTES) — every set so far was sampled by subject, and 92 per cent of the newest
  one turns out to come from a publisher the rules were already written against (150 of 163
  papers; 13 across 12 new prefixes). Since the rules key on layout conventions, that makes the
  aggregate a measure of transfer to new papers rather than new layouts, and it hides the one
  real weakness the split shows: **0.966 faithful on a fitted publisher against 0.906 on an
  unfitted one for an ordinary paper, and 0.933 against 0.699 for an editorial or letter.** The
  next set should be drawn the other way — pick thirty DOI prefixes none of the libraries hold,
  weight them towards editorials, letters and comments, and take whatever subjects come with
  them. `fetch_heldout4.py` needs only its query built from `PUBLISHER:`/prefix terms instead of
  topics, and the prefix list can come from the stores the way the measurement did.
- **The worker segfaults on the PDF path, and a paper can be filed with no tree** (found
  2026-09-20 ingesting held-out 4) — `litrag-parser` exits `3221225477` (0xC0000005, an access
  violation) part-way through a run of PDFs. On the first ingest of that set it emitted 315 of
  the expected 332 trees and died, leaving **17 PDFs with a `papers` row and nothing under it**.
  It is intermittent, not deterministic, and not a poison file: repairing those 17 by `reparse`
  crashed after one paper, then after one more, then read the remaining fifteen straight through
  and exited 0 — and each crash was on a different paper, every one of which parsed fine on a
  later attempt. The JATS path has not crashed. Two things follow. **The bug**: something in the
  Docling PDF pipeline does not reliably survive repeated documents in one process — worth a run
  under `faulthandler` and with
  `PYTHONFAULTHANDLER=1` to get the native frame, and worth checking whether `parse_one` differs
  between the ingest and reparse paths (`worker.py` `do_reparse` reads the stored copy from
  `papers_dir`, `do_ingest` the offered path). **The hole it leaves**: idempotency means a paper
  filed with no tree is *skipped* by a later ingest, so the failure is silent and permanent
  until someone counts nodes per paper. `library.py` or the harness should refuse to call a
  paper filed when its tree is empty, and the harness should report `papers with no nodes` as
  an error rather than leaving it to a hand-written query. The workaround that unblocked the
  fourth set is `repair_heldout4.py` beside the libraries: reparse, one worker per paper, until
  nothing is left empty.
- **The 333 cut paragraphs are the asymmetry, not a defect** (measured 2026-09-20,
  `cutkinds.py`) — classified by what opens the second half, the 135 cuts on the working half
  are: 94 (70%) a new sentence after a full stop, 19 an abstract split where no label the rules
  know opens the next half (and several of those are the reader being *right* — Frontiers prints
  "Methods Eighty-eight patients…" with no colon, which `_ABSTRACT_PART_WIDE` wants), 19 a real
  mid-sentence cut opening lowercase, and 3 where the reader is plainly right. The 94 are the
  measured price of `INDENTS_ENOUGH`: in a paper that indents nothing, a full stop at a line's
  end cannot be told from a paragraph's end, and trusting the full last line tripled silent
  merges (0.015 → 0.054) when it was tried. Both halves stay in the right lane and the right
  section, so a cut costs granularity, not placement. Before this number is attacked again the
  thing to find is a *third* signal for the unindented case — the line's own leading, or the
  first line's left edge against the block's, both of which `typography` can already see.
- **A table note read as body prose: 0.90, and refused at that** (measured 2026-09-20,
  `tablenotes.py`) — of the paragraphs the reading puts straight after a table on the same page,
  the XML holds 180 as prose and 27 not. Only one shape separates them: a footnote's marker
  opening the line ("*Statistical significance.", "†Category of care is assigned…"), right 10
  times in 11. Every other shape is worthless — a paragraph that mentions a statistic is real
  prose 50 times in 54, and a prose-shaped one 128 in 137. Three variants of the marker all
  land between 0.900 and 0.917, and the late `correspondence` shape was refused at 0.909 the
  same day, so this is refused for the same reason: about 10 blocks over 230 papers is not
  worth a rule that is wrong one time in ten. Docling's own `footnote` label is still honoured
  (`test_a_table_footnote_is_a_node_after_its_table`); this is only about the blocks it calls
  `text`. If a cheap signal ever separates them — the block's own font size against the table's,
  which `typography.look_of` already computes — that is where to look.
- **The 47 missing chunks are 360 words, and OCR would reach six of them** (measured
  2026-09-20, `missing_layer.py`, `ingested.py`) — the empty-text-layer hypothesis was wrong.
  Of the 47 chunks the novel set's reading does not hold, 43 have their text in the PDF's own
  text layer (pypdfium2) and only 4 do not; across the three tuned corpora, 14 against 8. What
  they have in common is not a missing text layer but shortness: they are run-in labels the XML
  holds as paragraphs and the reading reads as headings ("Body Weight Gain", "Filling Phase:"),
  and one-sentence graphical-abstract blurbs the PDF carries only as an image ("Micro laser
  powder bed fusion enables architected nickel shellular…"). All 47 together are about 360 of
  the novel set's 721,848 prose words, and asked whether each XML paragraph reaches the tree at
  all the reading holds 0.9995 of those words. So vision has no measured case left here: at
  most six chunks, and the rest is a question of a node's *type*, not of lost text. Whether a
  run-in label read as a heading should also be a paragraph is a real question for `units_of`
  and for retrieval, and it is not an ingestion gap.
- **Two deterministic joins the measurement refused** (2026-09-19) — (a) taking the gap a figure
  opens as the figure's, so a full last line joins a paragraph across a caption: paragraphs cut
  fell, but `intact` fell on all four corpora because a tail joined the next paragraph's head;
  (b) trusting a full last line in a paper that indents nothing (`tree.INDENTS_ENOUGH = 0`):
  silent merges rose three and a half times (0.015 → 0.054 of paragraphs on the novel set). Both
  reverted, both recorded in the code where the threshold lives. A cut leaves a mark in the tree
  and a merge does not, which is why the gate is asymmetric.
- **Cut paragraphs are reading order, not boundaries** (measured 2026-09-19,
  `pairs.split_reasons`) — of the paragraphs that arrive cut over several
  chunks, 40 per cent have a caption read between the halves, a third have
  nothing between them at all on one page, 11 per cent a heading, and only a
  tenth are the page break the boundary scorer (`boundary.py`) was built
  for. So the surplus chunks — 198 paragraphs into 445 on the novel set, 391
  into 878 on the tuned ones — are mostly `_stitch_fragments` being too
  cautious and captions read mid-paragraph, both deterministic. Do that
  before calibrating the scorer, and gate it on `paragraphs.intact/merged`,
  not on `faithful`: a cut chunk is already in the right lane.
- **A wrapper section the XML keeps and the page does not** (2026-09-19) —
  Cureus prints "Materials and methods", "Eligibility Criteria" and
  "Outcomes" and its XML keeps the whole body under one "Review" heading, so
  39 of one paper's chunks count as wrong-laned while the reading is the
  better one; an editorial's "Main text" read as an abstract is the same
  shape. `structure.dissolve_wrappers` (DESIGN R3.11, stage 2) is the rule,
  and `lane_only` already tells the convention from the error.
- **What is left after 2026-09-19** — 2.7 per cent of the abstract's and
  body's words over the 199 pairs (1,027,592), by where they go
  (`measurements/2026-09-19/scripts/residue2.py`): **1.04 per cent not held**
  by the PDF's reading where their paragraph lies (0.47 not found as words
  anywhere: formulas and superscripts written differently; the rest read in
  another place); **0.97 filed under another section** — no paper holds more
  than 971 words of it now, and each is its own first page or its own missed
  heading (a Nature paper's introduction read into its abstract, a Wiley
  paper whose "2 METHODS" the model never read, a Frontiers paper whose
  methods heading is a ghost); **0.24 kept in the front matter** (NEJM's
  structured abstract read as front-matter lines, an abstract Docling merged
  into the citation line above it); **0.20 filed in a reference list or a
  back statement** (a keywords block that runs on into the introduction,
  Wiley's and Frontiers' first pages); **0.16 read into a caption**; the rest
  in a heading, a table, a footnote. Beside the lanes: paragraphs split at
  breaks (5.4 per cent of the XML's) and captions read as prose (4.9 per
  cent). One paper reads worse than before depth by type: Hip & Pelvis's
  numbered methods subsections, set small under "MATERIALS AND METHODS" and
  nested so, which its XML keeps flat (0.979 → 0.916; the page is right).
- **The next small questions** (2026-09-18, Karim's direction: "smaller
  and more targeted … more than one agent call per paper") — the heading
  question is measured (`heading_q3.py`, scratch; the 14B finds run-ins
  at recall 0.80) and the page's type now answers most of it; what is left
  for a model, each one chunk and one constrained answer validated
  against the chunk and stored as a row: a run-in heading set in the
  body's own face (the residue after `typography.py`); the abstract's
  end — "at which paragraph does the body begin?" over the abstract
  section's few paragraphs, for the papers whose body stays in the
  abstract; a headings-only outline call (the heading list with each
  row's style, ~1–2k tokens) for the depth of unnumbered headings where
  the styles tie; "do these two paragraphs belong to one section?" where
  the paragraph classifier's lane changes with no heading between. Each
  is measured on the pairs before it is wired, as the heading question
  was.
- **The outline judge on by default** (2026-09-18) — it earned its keep on
  the 64 pairs (NOTES.md: Qwen 3 14B 0.80 to 0.91, eleven better, two
  worse; Qwen 3 8B 0.80 to 0.90, none worse) but stays opt-in: twenty
  seconds of the card per paper, and the corpora without an XML twin are
  unmeasured. The way in: rebuild one
  library with `LITRAG_OUTLINE=on`, run the harness against its baseline,
  read the built headings and the lanes it changed by hand (the notes name
  them), then decide. Two known losses to watch: a review whose XML really
  nests its topical sections under "Discussion" (the model's `other` is
  taken, since a review's nesting is usually the reader's guess), and the
  model's paragraph off by one at a section's start (a built heading holds
  its predecessor's last paragraph; caught where the reader has the
  heading, not where it printed it differently). And a review's disagreement
  notes are many (seven a paper): the model calls a review's topical
  sections results or methods; the notes are the model's habit, not
  information, and could be folded into one per paper.
- **What the confidence score does not see** (2026-09-17) — a partial
  disagreement: a fifth of the text under a neighbouring lane because one
  top-level heading was missed (11 of the 47 seriously mismatched pairs
  score 0.9 or more, faithful 0.58 to 0.79). Candidates: run-in headings
  ("Model architecture.") the PDF read as sentences; a lane far smaller
  than its type's usual share; the record's abstract against the tree's
  (Europe PMC returns `abstractText` with the record already fetched).
  Every candidate is measured with `confidence --calibrate` before it is a
  check.
- **Text layers that say everything twice, interleaved** (2026-09-17) — the
  fixture's own PDF (MDPI, pages 6 to 8) gives Docling blocks whose lines
  alternate between two copies of the paragraph; `unrepeat` mends a copy
  that follows another, not two woven together. Needs the lines' geometry
  (recover.py), or a Docling backend that does not duplicate
  (`claude/docling-bench` is measuring backends). The score's "text is
  there twice" check flags it.
- **A lane by meaning inside a review** (2026-09-17) — "Natural Materials" and
  "Synthetic Materials" in a review of tendon scaffolds lie near "Materials
  and methods" and take the methods lane; the type is known before the
  lanes are read, so R3.2's type-conditioned lanes would refuse it.
- **Docling dies in native code on some PDFs** (2026-09-17) — exit
  3221226356 (heap corruption) while reading `doi:10.14814/phy2.70063`
  mid-batch, and the same file read alone went through; with the PNAS
  access violation (0xC0000005) that makes two. The worker should read
  each PDF in a child it can lose, and requeue the rest.
- **What the shape cannot read** (2026-09-14) — case reports, letters and a
  guideline written as full research papers (four, four and one of the
  286 labelled papers), MeSH's "historical article" and "video-audio
  media" on research papers, a meta-analysis that is also an experiment:
  only a stated label settles these, and a PDF without a record gets
  `research`. The profile kind (title, first sentences, headings, against
  example profiles) measured 0.66 and ships off; a kind whose prototypes
  are the corpora's own labelled abstracts, measured library-out like the
  heading centroids, is the next thing to try before any generative judge.
- **RSC's sidebar text inside a paragraph** (2026-09-14) — the rotated
  "Open Access Article. Published on 01 September 2026. Downloaded on …"
  along an RSC page's margin is glued by the layout model to the block
  beside it ("Their photophysical behavior is generally Published on 01
  September 2026" on `doi:10.1039/d6ra07899k`, held-out-2-pdf). The
  sidebar ungluing in `recover.py` keys on Wiley's "Downloaded from" and
  does not see this one; the layer's rotated rows should be read apart
  wherever they stand, whatever they say.
- **The boundary scorer on real leftovers** (2026-09-13) — it passes the
  synthetic gate and fails the real one because the pairs the rules leave
  open are mostly not continuations at all (a funding line, an affiliation
  line, a sidebar). Two ways in: keep those out of `_judge_candidate` by
  shape (a line with "grant", "contract", a department: front matter, not
  prose), or label two hundred real leftover pairs by hand and calibrate on
  them. Until then `LITRAG_BOUNDARY=on` is the switch.
- **PNAS PDFs crash Docling** (2026-09-12): two of them
  (pnas.2601235123 and a second) kill the worker in the layout stage with
  an access violation (exit code 3221225477, 0xC0000005) — a native crash in
  pdfium or the layout model, not a Python exception, so nothing catches
  it and the paper stays `parsing`. Two remedies, in order: the app
  restarts a dead worker and marks the paper failed (the backlog's "a
  crashed worker is never restarted"); the worker converts each paper in a
  child process so a crash costs one paper, not the session. To reproduce:
  ingest the two files from the held-out-2 folder in the scratch
  directory. A third PNAS PDF parses, but Docling reads its first page as
  a jumble (columns interleaved) and no title can be found.
- **Equations that are only images** — a JATS `<disp-formula>` with a
  `<graphic>` and no maths. Europe PMC serves the image by name (see
  "Figures for XML papers"); fetching it opt-in would let the reading view
  show the equation where its placeholder stands, and a formula OCR could
  read it into text.

- **Figures for XML papers** (Karim, 2026-09-11) — a JATS names each figure's
  file (`<graphic xlink:href="micromachines-15-00851-g001"/>`) but carries
  no pixels; Europe PMC serves the file at
  `https://europepmc.org/articles/<PMCID>/bin/<name>.jpg`. Fetching them,
  opt-in, into the library's `figures/` beside the paper — a Europe PMC
  call, which the invariants allow — would let the reading view show the
  image where its placeholder stands.
- **Superscript citations with a space in the PDF text** (done, 2026-09-11
  night) — the text layer now marks a raised number ("applications.^17")
  and the linker reads that, the glued form and the spaced form
  ("skin. 1,2 More") under the same style detection; 61 of the 66 PDFs
  with a reference list link citations, from 52. The five that do not
  print no markers the text layer can see (two are scans).
- **Entries the reference list still miscounts** — a Wiley review's author
  biographies at the end fall under the inferred References section
  (no heading separates them) and unnumbered fragments of an entry are
  skipped rather than joined to the entry before them; both are visible in
  `refs` and harmless to the links, since the printed numbers now decide
  the entry numbers.
- Author–year linking uses the first surname and the year; "Levin, 2022"
  against 2022a and 2022b links both. Titles for PDF entries are not
  parsed; a JATS entry's title comes only when the XML has
  `<article-title>` (MDPI keeps one citation string). Crossref by DOI
  would fill both, one call per entry, opt-in.

- A PDF's equations come out as empty `formula` nodes: Docling sees the
  block but reads its text only with formula enrichment on
  (`do_formula_enrichment`, another model, more GPU time per paper). The
  audit grades them `unread-formula`. The JATS of the same paper carries
  the equation as text now (`mathml.py`), so prefer the XML where Europe
  PMC has it; turn enrichment on when a library of PDFs needs the maths.
- A PDF's front matter carries the publisher's labels — "Article",
  "ORIGINAL RESEARCH" — as paragraphs or as the title (the audit's
  `generic-title` and `stray-words`). A list of such labels per publisher,
  or a rule that a title is never one dictionary word, would file them as
  running heads.

- Docling's `page_header`/`page_footer` items are dropped from the tree;
  a journal's running head sometimes carries the DOI, which the PDF sniff
  already reads from page text, so nothing is lost yet.
- A PDF whose first two pages carry no DOI is asked for on Europe PMC by
  its title (the PDF's Title metadata, else the largest line of the first
  page) and keyed by the DOI or PMID when exactly one record's title is the
  same words (2026-09-11 night; `"offline": true` on the `ingest` skips
  the call). Of the archive's nine hash-keyed PDFs three are found this way
  (Biophys J 2002, Matrix 1989, Pharm Res 1995); the rest are
  supporting-information files and abstract compilations with no record.
  The three already filed under a hash keep it until they are dropped and
  read again — a `rekey` op would move their rows.
- **Tables Docling drew but could not structure** get their rows from the
  text layer now (one cell per gap); a table that has a grid with empty
  cells (a 35×4 with half its cells blank in one paper) is still shown as
  Docling read it — the same layer rows could fill the blanks where a cell's
  box is known.
- The worker converts one paper at a time. Docling can batch; on a GPU
  two at once would roughly halve wall time for a folder drop.
- Windows now takes torch from the cu130 index (`[tool.uv.sources]` in
  `parser/pyproject.toml`, 2026-09-11); Linux's PyPI wheel already carries
  CUDA. A driver older than R580 silently falls back to the CPU — the only
  sign is "ready on cpu" in the log, about 100 s of layout for a 14-page
  paper. The window could say so when a GPU is present but unused, and
  `hello` could carry the device before the first paper (it would cost the
  torch import up front).
- The first paper of a session costs ~30 s on the GPU (CUDA warm-up, model
  load) against ~2 s for each paper after it; the worker could warm the
  converter on a blank page as soon as the window opens.
- The `papers` op returns everything; a library of a thousand papers wants
  paging or a `since`.
- `reparse` reads every paper again with Docling; a `reparse --changed`
  that re-reads only papers parsed by an older Docling would be cheaper.

- `refresh` re-runs saved searches at limit 50 regardless of their
  original limit; a `--limit` per saved query would let a broad query stay
  narrow.
- A PDF from the inbox with no DOI on its first pages reads as
  `Untitled (<file>)`; a `lit rename <key> "<title>"` (or the model stage
  reading the title) would fix it without the app.
- Europe PMC search returns at most 100 per call; paging with
  `cursorMark` would let a saved query grow past that.
- The CPU embedder pulls ~450 MB of onnxruntime into `node_modules`. An
  install without it, for a machine that will always use Ollama, would be
  an optional dependency.

## Explicitly not wanted

- No automated downloading behind a login: publishers' terms forbid it and
  it gets campus IP ranges blocked. The tool catches what a person clicks.
- No second store: the graph is built from the tables at query time. A
  graph database would be a second thing to keep true.
- No prose from the tool. `query` returns passages with citations; the
  answer is the assistant's, or `lit ask`'s when that exists.

## Found by the publisher-drawn corpus (2026-09-21)

- **`quit` discards work that is only queued** — `quit` is `os._exit`, so an `ingest` or
  `rebuild` sent in the same breath is killed before the ingest thread has run it. The paper
  stays `queued` for good: `unread_papers` reports it and `reparse` rescues it, but the worker
  does not drain its queue. It is also a live trap for anyone scripting the worker — a test that
  fed both at once passed while measuring nothing. Drain the queue on `quit`, or refuse the op
  while work is outstanding and say so.
- **Short papers get no geometric furniture at all** (measured) — `_recurring_furniture` needs a
  line on three pages and 25 of 25 DEV papers of three pages or fewer had it fire not once,
  against 28.3 fires a paper at nine pages or more. Lowering it to two pages was priced and has
  **zero** candidates: Docling's own `page_header`/`page_footer` labels have already taken that
  text. So the gap on short papers is not this, and the next thing to try is not this either.
- **Editorials on a publisher never seen read 0.419** against 0.825 on a fitted one — far the
  largest gap of any kind, and seven of DEV's eleven are among its twenty worst papers. Letters
  are 88 per cent of them three pages or fewer. `NOTES` named the mechanism before there was a
  corpus: little text to anchor on and no convention to key off. Nothing in this round moved it.
- **The DOI a PDF prints can be truncated where the line broke** — `10.1302/2633-1462` for
  `10.1302/2633-1462.611.bjo-2025-0138.r1`, which then matches no record and no twin. `pick_doi`
  takes the first DOI whose suffix carries a digit; it could also refuse one that ends where a
  line does when the page continues with something a DOI may contain.
- **12 of DEV's 135 papers still have no witness** after the content-hash repair, and are named
  in every run rather than dropped. Worth a look before EXAM is drawn.
- **The rule-pricing method is unreliable for short strings** — a running head reading
  `Volker Kahlenberg et al. — K0.72Na1.71…` matches the XML's *contributor block*, so the removal
  scores wrong when it was right. Judging against prose in a named body lane removes most of it,
  not all. Rules over short strings carry that caveat.

## Found by the invariants (2026-09-21, Phase 4)

- **I10 looks at the right fault with the wrong granularity** (measured) — a line that recurs
  across pages, read as body prose, is the largest junk class the reader has, and the check for
  it fired on **one** paper in 123 and at no block the witness could judge. The reason is
  visible on the fixture: the running heads are there — `Micromachines 2024 , 15 , 851`,
  `x FOR PEER REVIEW 10 of 15` — but glued *inside* paragraphs rather than standing as nodes of
  their own, so a check comparing whole nodes cannot see them. I2 and I9 both trip over the same
  text from the other side. The check should compare *lines* within a node, not nodes. Price it
  before writing it: the candidate set must be built from the side the rule acts on.
- **I11's column heuristic is noise as written** — 869 firings on DEV, 1,141 more at places the
  witness cannot judge, and a lane-error lift of 1.02 [0.78, 1.29]. It splits a page at its
  mid-line and excludes blocks wider than three fifths of it, which reads front matter and
  full-width figures as column members. Either detect columns from the page's own gutter or drop
  the inversion half of the check and keep the page-coverage half, which is the useful one.
- **I1 cannot be both a target and a guard** — conservation fails on 123 of 123 DEV papers at
  the 0.999 floor T1 asks for, so it separates nothing: P(a paper reads below 0.90 | I1 fails) is
  0.146, which is DEV's own share. `PLAN.md`'s repair rule says "accept only if the total
  violation score falls and I1 still holds"; "still holds" has to mean *does not get worse*. The
  floor stays at 0.999 because that is the target, and a second, relative reading of it is what a
  repair guard needs.
- **A paragraph set like a heading is a question, not a repair** (measured) — `I12
  odd-one-out:paragraph-read-as-section` concentrates lane errors 5.85× [1.82, 17.88] inside the
  paper it fires in, over 60 papers and 41 publishers, keyed on nothing but the document's own
  typography. But against its literal claim — that the block is not a heading — it scores 0.4375
  against a base rate of 0.4699, a lift of 0.93. It finds a bad place and the heading there is
  usually real. Escalation should be handed these 80 places; demoting the heading should not.
- **The witness's headings are a noisy ground truth** — 1,507 of 3,207 headings the reading
  prints have no match in the XML twin (0.470), which cannot mean the reader invents half of
  them. The XML often does not carry subsection headings the PDF prints, and one supplement
  contributes 1,091 of the 1,507. Any rule priced against "the witness has no heading here"
  carries the `front:keywords` caveat.

## Found by Phase 5 (2026-09-21)

- **The reader has one mechanism for a heading's lane, wearing three hats** (measured) — the
  vocabulary's regexes, the catalogue of canonical spellings and the embedder on the heading
  disagree **zero times in 434 chances** on DEV. They are different code reading the same string,
  and the table and the centroids are harvested from the same corpus of canonical spellings, so a
  heading any of them knows is one they all know. Assertion by agreement therefore has nothing to
  resolve, and Phase 3's route table (vocabulary 0.891, catalogue 0.956, embedder 0.988) was
  measuring which route *got there first*, not which was right. A genuinely independent mechanism
  has to read something other than the heading: the paragraphs (the block classifier, below), the
  section's position in the order, the template's memory of what that class of heading was last
  time, or a second reader.
- **The block classifier is refused almost everywhere it speaks** (measured) — it reads a
  section's paragraphs, which makes it the only mechanism independent of the heading, and its
  margins run about **0.02** against a required **0.08**. So `lane_sections`'s note — "the heading
  names methods, the paragraphs read as results; the heading stands" — almost never fires, and the
  one opinion that could contradict a heading is silent before it can. Its threshold and margin
  are being derived from DEV; `sweep_blocks.py`.
- **`LITRAG_LANE_AGREEMENT` is built and off** — on a strong disagreement between a heading and
  its paragraphs the reader abstains and keeps both opinions in `guess`/`reasons` rather than
  letting the heading stand. It cannot be priced until the block classifier is allowed to speak,
  and it is off until it is.
- **Three of `lanes.sqlite`'s 125,739 rows in four are dead weight** — they were written under
  thresholds no longer in force, when the threshold was still part of the cache key, and nothing
  will ever read them again. A `--prune` that drops rows whose `rule` is not the current one, run
  on the campaign copy first, would take the file down by most of its size. Not urgent; the file
  is not large and deleting rows from a cache in someone's library is not a thing to do casually.
- **`Fingerprint.key()` repeats rarely** — twice over 114 DEV papers, even after being coarsened
  to page, columns, body and the faces that stand apart. Phase 7's "on a known fingerprint, reuse
  and then verify" will have to be keyed on `distance` under a threshold rather than on the key,
  and the threshold wants deriving the way the block classifier's is.
