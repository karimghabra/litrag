# Backlog

Where wants wait until they are built. Strike entries that get built; add
wants as they are voiced.

## Named priorities

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
- **Wire the retrieval loop to the tree store** — paragraph nodes as
  chunks with ancestry prefixed, embedded once, partitioned by role; the
  miner and the model stage over `nodes`; the graph walk over the same rows;
  then `lit query` reads `store.sqlite`. Port the CLI's verbs one at a time
  and strike them from `src/`.
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
- **Collect mode** (Karim, 2026-09-03) — the one step of the loop that
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
