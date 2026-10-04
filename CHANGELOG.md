# Changelog

## Unreleased

- **The library as a graph, and citation rounds** (Karim, 2026-10-04: "a second round of searches,
  based on the citations in the first round … a graphical representation of the literature which
  connects papers together, much like the graph in something like obsidian … ordered searches,
  and searches of the corpus by author … these queries should be able to prompt further rounds
  of ingestion"). `graph.py` keeps paper-to-paper citations as rows (`cites`: between works, a
  paper held or a candidate, `origin` refs or europepmc), every work's authors as rows
  (`authors`, with family name, initials, ORCID and a `person` key that joins one person across
  works) and a `works` view over papers and candidates alike (year, first publication date,
  first author, `round`, `cited_here`, `cites_here`). A reference naming a paper held is a
  citation the moment both are read. The `round` op asks Europe PMC what each paper read since
  the last round cites (its `/references`; with `citations`, its `/citations` too), looks every
  identified work up twenty to a query, and files it as a candidate of the next round
  (`candidates.round`, `published`, `author_list` — new columns), never fetched; an entry naming
  no identifier is counted, not guessed. The `graph` op returns the picture; `sql` now runs on a
  `query_only` handle (the regex was the only guard) after bringing those rows up to date. A new
  **Graph** tab draws the papers and the candidates several of them cite as a force-directed
  graph — by round, year or held-or-not, neighbours lit on hover, a work's authors, citing and
  cited works a click away — beside a SQL pane with ready questions (held oldest first, the next
  round, by an author, authors here, who cites whom, rounds) whose candidate rows can be ticked
  and fetched and read: the next round. An author opens their other works here, or a Europe PMC
  search for them. On two papers, a round filed 117 works (46 with open XML) in nine seconds.
- **Open PDFs from NLM's PMC Cloud Service** (Karim, 2026-10-04: "yes, add the PMC Cloud
  Service"; one more host for invariant 1, sent a PMCID and nothing else). NCBI retired its OA
  web service and FTP packages in August 2026 and named the `pmc-oa-opendata` bucket the
  successor; EBI's bulk area, the only PDF source until now, misses many papers it holds (the
  Advanced Healthcare Materials paper among them). `fetch` now asks it for every PMCID with no
  open XML, and for the PDF beside an XML for its figures, before the bulk area: the article's
  versions are listed, the newest one's JSON names its PDF and that PDF's MD5, and the PDF is
  kept only if the MD5 holds. An author manuscript there has XML and text and no PDF, said so in
  the candidate's error. `source` is `pmc-cloud`; `LITRAG_PMC_CLOUD_URL` points it elsewhere
  (the tests and the end-to-end fixture do, so nothing in them reaches NLM).
- **An XML paper's figures, from a PDF of it** (Karim, 2026-10-04: "if we only ever retrieve an
  xml … we end up missing a bunch of figures?"). JATS names its figures and holds none, so the
  XML stays the paper and a PDF of the same paper is kept beside it for its charts
  (`papers.figures_file`): each page printing a figure's caption is read — an image there whole,
  at its own resolution, the rest with the page's text layer — and every plot pinned to the
  caption under it, "Figure 2." to the XML's figure 2 (`charts.figure_label`; a sentence that
  begins "Figure 2 shows" is no caption; a rebuild finds the figure again by its number). The PDF
  comes from a fetch (EBI's open-access PDF beside the XML, where that area has it), from a PDF
  dropped for a paper already read as XML (kept for its figures, no longer set aside), or from
  **Collect PDFs**, which now lists the XML papers whose figures want one after the papers with
  no copy (`wanted` → `figures`). On the Advanced Healthcare Materials paper read as XML, its
  PDF gave 12 of 24 plots, 127 values, its two-panel gene-expression figure with every series
  named.
- **Figures read into numbers** (Karim, 2026-10-04: "ingesting figures, and converting them from
  data in a visual format, to one in a numerical format"). A PDF's figures are cut from their
  pages and their charts read (`charts.py`, `figures.py`): each bar and point's value, its error
  bar's ends, its series, its category or x, the axis's title, unit and scale, the panel's
  letter and title. A vector figure is rendered at 600 dpi and read with the PDF's own text
  layer; an image at its own resolution with OCR — RapidOCR on the models its package ships,
  run by `onnxruntime` (new dependency; nothing is fetched). A y axis must calibrate from three
  tick labels within 1 % of its range, else the plot is `unread` with the reason and no value;
  a frame with no numbers beside it is no chart. Bars (grouped by their fills repeating,
  outlined, pale), markers (overlapping ones parted by colour), error bars up and down; linear
  and log axes. Rows `charts` and `chart_values`; ingest's `figures` stage
  (`LITRAG_FIGURES=off` skips it), ops `figures` (a library's PDFs, queued) and `charts`;
  rebuilds and merges keep them. The Papers tab shows a picture's plots as tables with a CSV
  each (**Read the figures** for papers read before); Query shows a cited figure's numbers, the
  named panel first, and a caption hit its own figure's. Not yet: curves without markers, box
  plots, horizontal bars, XML papers' figures.

From a finding to the method that produced it, followed further — and a way to know how often
it is right (Karim, 2026-10-04: "Implement these").

- **Link strength.** `measured_by` edges keep their evidence and gain a score that orders them:
  a pointer 1.0, terms 0.80–0.95, a caption 0.70–0.80, resemblance 0.60–0.70, each higher in its
  band the more marks it rests on. The same edges as before; a library read before needs
  `rebuild` for the scores (until then its terms edges stay at 0.9).
- **Query hydration, both ways.** A finding's methods come strongest first, at most three, each
  with **the paragraph** inside it the finding rests on — the one its marks name, else the one
  sharing its rarer words, else the first — instead of the subsection's first 1,500 characters.
  Statistics and materials (by canonical heading) go apart, under "Also used", so they never
  take a method's place; a finding with only those still gets its figure's methods, then its
  section's. A hit inside a methods subsection lists **the findings its method measured**.
- **Methods described elsewhere** (`lineage.py`). "As previously described [14]", in a closed
  vocabulary of cues, is followed to the entry it cites and, when the library holds that paper
  (by DOI, then PMID, then title), to its own method — the subsection the sentence's marks name,
  else its methods section; when it does not, the entry is named with the candidate that would
  fetch it. On each hydrated method and on a methods hit (`described_in`); `python -m
  litrag_parser.lineage --lib DIR` surveys a library. Pure reads, no model, about a millisecond.
- **The truth set.** **Label links** on the Papers tab, and **Label** under a finding's "Measured
  by": per finding, which methods it was measured by, or none, and the paragraph if it is
  known (number keys, N, Enter, S). Ops `label_queue`, `label`, `labels`, `truth`; table
  `link_labels`, kept through rebuilds and merges. `truth` scores the edges against the labels —
  precision per evidence, recall, misses, false links — and hydration's paragraph against the
  method's first; `python -m litrag_parser.truth --lib DIR --measure | --export | --import`.
  Resemblance stays off until it scores 0.9 there.
- **The local model labels, a person audits** (Karim, 2026-10-04: a person need not label a
  hundred findings). **Let the model label** in the panel (op `model_label`, `python -m
  litrag_parser.labeller`) has the local model (`qwen3:14b`, or `LITRAG_LABEL_MODEL`, through
  Ollama on 127.0.0.1) label the findings the queue would offer, by the rule the panel now shows
  beside the question; its answers go to `model_labels`, apart from a person's. The queue offers
  them first, never saying what the model answered, so a person's labels on them are its audit;
  `truth` reports the agreement and every disagreement, and once 25 are audited at 0.9 agreement,
  measures the edges again with the model's labels for the findings no person labelled. Merges
  carry them; running it twice asks nothing twice.

## 0.3.2 — 2026-10-04

Installable from a release, author manuscripts from NCBI, and torch chosen by name.

**Upgrading a checkout on Windows with an NVIDIA card:** run `uv sync --project parser --extra
cu130` once — a plain `uv sync` now takes CPU torch there (below).

- **Installable from a release** (Karim, 2026-09-30: "a bat script that installs dependencies,
  followed by the software itself, which is relatively small"). Each published release gets
  `litrag-<version>-win-x64.zip` (~135 MB) and `litrag-<version>-linux-x64.tar.gz` (~110 MB):
  the unpacked app — `app.asar` is 3.9 MB, the rest is Electron — with the parser's source in
  `resources/parser`, beside `install.cmd`/`install.ps1` or `install.sh`. The script installs
  for this user, without admin, into `%LOCALAPPDATA%\litrag` (`~/.local/share/litrag`): uv, uv's
  own Python 3.12, the parser's environment (`-Torch auto|cpu|cu130`, `auto` being cu130 where
  `nvidia-smi` runs), Docling's models if asked (`-PrefetchModels`), Ollama with
  `nomic-embed-text` (`-SkipOllama` to leave it), and a Start Menu entry. Running it again
  updates, keeping the environment; `uninstall.cmd` removes it and never a library.
  `.github/workflows/release.yml` builds both archives, installs each on a clean runner, starts
  it, uninstalls it, and only then attaches it (electron-builder, `app/electron-builder.yml`,
  `app/scripts/release.mjs`).
- **The window starts an installed litrag's worker straight from its environment**
  (`%LOCALAPPDATA%\litrag\venv\Scripts\litrag-parser.exe`, `…/venv/bin/litrag-parser`, or
  `$LITRAG_VENV`), without uv; in a checkout it finds uv where its installers put it even when
  PATH lacks it, and runs an existing environment with `--no-sync`, so the torch it was synced
  with stays. `LITRAG_PARSER` takes a JSON array or a file path, so paths with spaces work
  (`app/src/main/launch.ts`, pure and tested). When the worker isn't running, a band under the
  header says why — no environment, no uv, the program's own error — instead of "worker did not
  answer".
- **Torch chosen by name**: `uv sync --project parser --extra cpu` (PyTorch's CPU build, a
  ~1.4 GB environment) or `--extra cu130` (CUDA 13, NVIDIA driver R580+); they conflict. With
  neither, torch is PyPI's — on Windows now CPU-only, where it used to be CUDA 13: uv cannot
  keep that default beside a `cpu` extra, so **a Windows checkout with an NVIDIA card syncs once
  with `--extra cu130`**. uv remembers no extra and a plain `uv run` syncs back to PyPI's torch,
  so the npm scripts go through `bin/parser-run.js`, which passes the extra the environment's
  torch has, and the docs' commands carry `--no-sync`. CI tests on `cpu` with `--locked`.
- **Author manuscripts as XML, from NCBI** (Karim, 2026-09-30: one more host, an identifier out
  and the article in). Europe PMC's REST service serves full text only for the open-access
  subset and answers 500 for an NIH author manuscript, which PMC holds and NCBI's E-utilities
  give out. `fetch` now goes Europe PMC's XML → NCBI's `efetch` by PMCID → the bulk area's PDF
  → `needs-pdf`; the article is taken out of its `<pmc-articleset>` byte for byte and must carry
  a body (NCBI answers a publisher's closed deposit with an error, or with its front matter
  alone — both seen live the same day). `has_xml` counts an author manuscript, so the Search tab
  says "open XML" for it; `LITRAG_NCBI_URL` points it elsewhere (the end-to-end fixture does, so
  no PMCID in the suite reaches NCBI); `LITRAG_NCBI_EMAIL` and `LITRAG_NCBI_API_KEY` go with
  each request only when set. On the live search `"electrochemically aligned collagen" AND
  genipin`, 19 of 22 hits now have XML to fetch, against 5 before; the real window fetched one
  from Europe PMC and two from NCBI, and read all three into trees with their methods.
- A candidate marked `needs-pdf` can be fetched again from the Search tab, so a route that came
  later can find what an earlier fetch did not.
- `wanted`, and so the collect window, goes most-cited first, as `lit wanted` does (#1).
- **The `lit` CLI, three fixes stranded on `collect-mode`** (PR #24): a search hit with nothing
  to file under is skipped and counted, not fatal (#12); the model's answer is streamed, so a long
  schema-constrained generation no longer dies on the client's headers timeout (#13); a number's
  " m m" is read as µm and its " 1 C" as °C in pdf.js text, after the padding runs are collapsed
  and never inside letter-spaced display text.
- Since 0.3.1 (merged with PR #23): the **Collect PDFs** window opens by itself when a fetch
  leaves papers with no open copy; every paper card says what kind of paper it is; an author a
  search names is never hidden behind "et al."; the candidates panel shows this search's
  candidates; the query suggestions read a library from before the studio, whose searches are
  bare strings.

### From `claude/ingestion-generalization` — not yet measured (PR #25)

Work from `claude/ingestion-generalization` (2026-09-17/18) that main never received. **Not yet
measured on main** — the numbers below are the branch's own, taken on its reader and on sets
mostly from the publishers the rules were written from; main's before/after goes here once
`pairs.py`, `paper_type --measure` and `gate:ingestion` have been run (NOTES.md, 2026-09-30).

- **Citations, in the styles publishers print** (`citations.py`). A run of brackets is one marker
  ("[7]–[11]", "[12],14,21,[40]"); a caret superscript ("tendons^2,^3", "energy.^3−9") is read,
  and where a paper carries carets only those are superscripts; a name with a capital or a digit
  inside it (BaTiO3, SiO2) is not a citation; ranges with a true minus, spaced dashes, or an en
  dash that reached the text layer as "e"; a statistic's "F (1, 13)", a figure's number and the
  front matter's affiliations are not markers; entries run together in one block are cut at the
  printed number (`repairs.split_references`). The branch: held-out citation agreement 0.672 →
  0.723.
- **The type's shape answers** (`paper_type.py`). A results heading beside a methods or a
  discussion is research; short prose with neither lane is an editorial; an abstract and no
  results is a review, a methodology section notwithstanding. Letters and editorials decide on
  the shape alone; "Expression of Concern" in a title is a correction. A tree with no prose at
  all stays `other` (main's own guard). The branch: 0.786 → 0.927 on 248 labelled papers, 0.738
  → 0.820 on libraries never inspected; editorial precision 1.000 → 0.846.
- **A reference list spaced out by the layout model is still found** (`tree.py`, `_tight`):
  Wiley's "E.    Peled  ," and Nature's "1 . Collins, F . S." now match the entry patterns. The
  rest of that branch commit — carrying a run over blocks that look like an entry's tail — is
  held back: it filed a discussion citing by author and year as references.

## 0.3.1 — 2026-09-24

The reader's third round on publishers it never saw, and the XML read as its file has it.

- **Notes kept apart.** A block set smaller on every line than the body, opening with a capital,
  is a note (a table note, a legend, a licence): the paragraph it interrupted stays open for its
  lowercase tail. List items continue over a column or page break; pages of tables between two
  halves of a paragraph no longer keep them apart.
- **A JATS abstract in its parts, a bold paragraph as a heading.** Docling's JATS backend
  flattens a structured abstract into "Label: …" runs; they are split back at the labels. A short
  bold-only `<p>` is the subheading it is typeset as.
- One chunk in the right lane: tuned pairs 93.9% → 94.5% (157 papers better, none worse); DEV
  without its abstract book 89.2% → 90.3%; the reserved VAL 86.4% → 87.2% — all of VAL's gain
  from the XML side: the reader's new rules leave VAL where it was.

## 0.3.0 — 2026-09-23

**The studio.** The window becomes an app over projects, not one library's papers: five tabs
over one project at a time, and retrieval that answers from the trees.

- **Projects** — every project (one library each) as a card with what it holds; a new project
  with a description of what its literature is about; several merged into one, a paper held
  twice filed once and its rows derived again from the saved readings (`projects.py`).
- **Search** — Europe PMC from the worker (`acquire.py`): every hit kept as a candidate, the
  JATS full text fetched first, the bulk area's open PDF second, the rest listed with their
  publisher's page to download by hand and drop on the window, where the PDF is filed against
  its candidate. Queries drafted from the project's description by the local model
  (`suggest.py`) — suggestions only.
- **Types** — each kind of paper's canonical structure and any paper drawn onto it, a line from
  each printed section to its slot coloured by the mechanism that placed it (`canonical.py`);
  the Papers tab gains the same as a **Canonical** face beside the printed tree.
- **Query** — chunkless retrieval (`retrieve.py`): paragraph nodes embedded once with their
  headings (`search_document:`), words from `nodes_fts` and meaning from the new `vectors`
  table fused by RRF, and every hit hydrated from its tree — the headings above it, the
  paragraphs either side, the methods a finding was measured by, the figures and references it
  cites. A paper's passages are embedded as it is saved. On 14 proxy questions the answer is in
  the top three for 14 with its context, against 8 for `lit query` (`bench.py`).
- **The reader, judged in chunks** (`chunks.py`): an XML twin's paragraph arriving as one node
  in the right lane. Paragraphs across columns, pages and headings (a column break measured on
  the wrong column in `recover.py`, among others), where the abstract ends and how deep a
  heading sits, soft hyphens, and a JATS file's own nesting: one chunk in the right lane on the
  487 tuned pairs 92.1% → 93.9%, on the campaign's DEV split 85.5% → 89.2% (its abstract book
  left out), on the reserved VAL 84.5% → 86.4%.
- **The harness** — `npm run e2e:studio` drives the real window through every tab with Europe
  PMC stood in on 127.0.0.1, two real PDFs through Docling and the real embedder; 10 of 10, and
  the older suite 9 of 9. An independent review's ten defects are fixed and pinned
  (`test_review_fixes.py`).

The campaign's measurements and its EXAM score (ledger line 37) ship with this release; the
notes below, written before it, are the reader work it also contains.

### Before the studio

Meaning everywhere a list used to be, and a scorer for the one question
meaning cannot answer.

- **The publisher's furniture is never a chunk of the body.** Counted in chunks — a chunk
  being a paragraph node, the unit retrieval hands back — the reading of 127 novel papers
  produced 6,375 chunks for the 5,808 the papers have, and 219 of its own carried text no
  paper holds: a licence sentence, a date line, an editor's name, an imprint, an abbreviation
  list. MDPI sets these down the left of its first page and the layout model reads them
  between the introduction's paragraphs, where the front-matter boundary — which only ever
  looked *before* the body began — could not reach them. Six rules, each gated on the 199
  pairs and the three corpora, take **junk chunks from 219 to 108** and the reading's own
  chunk count from 6,375 to 6,268, with `faithful` on the 199 rising from 0.9726 to 0.9730 on
  the strength of one paper and no paper worse anywhere:
  - `tree._late_front`: a line whose *shape* is unmistakably the publisher's is front matter
    wherever the layout read it, on the first two pages, even after the body has begun. The
    embedder is not asked once the body is open — only the shape counts — and a block holding
    a sentence that carries on in lowercase is never one.
  - three of those shapes carry their own length rather than the 25-word cap: a licence
    sentence and a publisher's citation line are the publisher's up to 120 words **and never
    cite a reference**, which is what separates them from a paper whose subject is licensing;
    an imprint (`_COPYRIGHT_LINE`: "© The Author(s) 2026. Published by Oxford University
    Press…") opens a line and never a sentence of a paper.
  - `_front_kind` learns the editor's line ("Academic Editors: Steven C. Cook and Simona
    Sagona") and the bare date line ("Available online 6"), which `_late_front` already trusted
    but no rule could name — the two halves of the pass have to know the same shapes or the
    gate opens on a line the namer then calls `other`. Reading the ledger again after the first
    pass closed three more: `_LICENCE` now matches the sentence *as the layout model cuts it*
    ("International License, which permits any non-commercial use, sharing, …" — it spelled
    `noncommercial` without the hyphen the journals print), an author list or an institution's
    address joins the shapes the short-line cap admits, and Cureus's "Categories:" line is its
    keywords under another name. Five more junk chunks, no paper moved on the 199, and
    precision there up 0.97615 → 0.97630.
  - `tree._back_ends`: an abbreviation list printed on the first page is furniture, not the
    paper's back matter. Scientific Reports sets one beside the abstract and prints no
    "Introduction", so eight introduction paragraphs — 600 words — were read as back matter.
    Modelled on `_message_ends`: the list's entries are a few words each, the prose that
    follows runs past forty and cites, and no methods, results or discussion section is open.
    A real Abbreviations section at the end is untouched.
  - `tree`'s invented parent is a last resort, not a first one: when "2.2" arrives while
    "1 INTRODUCTION" is open, the reader now looks for a top-level section the author numbered
    "2" and goes back to it, standing in `2. (heading not detected)` only when there is none. A
    two-column page read right column first puts "2 METHODS" and "2.1" above "1 INTRODUCTION",
    and every later subsection was filed `other` under an invented parent. The author numbered
    both the section and its subsection, so this is the author's own word for where the prose
    belongs, not a resemblance. On the 199 pairs it moved exactly one paper, upward by 0.113
    (10.3389/fepid.2026.1813211, 0.865 → 0.979), taking faithful by words to 0.9730 and well
    matched to 185; on the novel set it took one paper's wrong-laned chunks from 5 to 1.
  - a heading that repeats the paper's own title is its banner, not a section; and a date line
    heads no section **at any page** — of the front-matter shapes read as a heading past the
    second page, the witness finds the date line among the paper's own headings 0 times in 9,
    against an affiliation 21 times in 31 (MDPI's "Institutional Review Board Statement") and
    an author line 2 in 6, so only the date line is taken.

  Out of 5,808 chunks the novel set now arrives 5,195 (89.4%) as one chunk in the right lane
  and 5,427 (93.4%) in the right lane at all, with 79 landing outside the prose (was 87) and
  47 missing. Those two numbers are about *placement*; asked instead whether the paper's prose
  reaches the tree at all — any node, any type — the reading holds **0.9995** of the novel
  set's 721,848 prose words and 0.9997 of the 199 tuned pairs' 1,075,647. The 47 missing chunks
  are about 360 words between them, and 43 of the 47 are in the PDF's own text layer: they are
  run-in labels the XML holds as paragraphs and the reading reads as headings, not lost text.

- `parser/litrag_parser/review.py` (new, `LITRAG_REVIEW=off` by default): the reader's own
  log read back by a local model. It is shown the reading — every section with its lane and
  where that lane came from, every paragraph with the lane it inherited — and answers with
  repairs: `relabel`, `not_a_section`, `split`. By Karim's decision of 2026-09-19 a verdict
  fills a silence and never overrules a rule, so only a `relabel` is applied, only where the
  section's own heading names no lane and it holds none (`_silent`), only towards a content
  lane, and only in a paper that reads as research — a review's sections are topical whatever
  they read like. Every answer is a row in `reviews`, keyed by the log and the model but not by
  the policy, so a policy is weighed again against answers already stored without asking a
  model twice; a rebuild replays it and an XML is never judged.
  **Measured on 54 novel papers, and it does not close the gap.** Under that policy one lane
  was filled in 54 papers and the mean faithful moved −0.0009. Weighing the 335 repairs the
  policy refused against the XML: 57 would have hurt (a review's topical sections laned by
  their sense), 89 were no change, 22 name a section the XML does not have, and 7 would have
  helped — every one of those in the class the invariant forbids, a rule that had already
  spoken in a research paper, against 2 in the same class that would have hurt. Of its 74
  "this is no heading" claims, 50 are wrong, including three real "Methods", "Results" and
  "Introduction" headings: the log shows a section whose prose sits in its subsections as
  having "no paragraphs of its own", and the model reads that as furniture. The pass ships off,
  its rows accumulate evidence, and the prompt flaw is written down (BACKLOG).
- `pairs.py`: three numbers that name the residue instead of totalling it.
  **`placed`** is the share of the XML's prose words landing in the PDF
  section that matches the XML's own section, so a lane that is right under
  the wrong heading no longer counts as right (novel papers: placed 0.931
  against faithful 0.960). **`lane_only`** splits the rest by direction — the
  paper's own lane where the file gives none, against a lane in the file and
  none of the paper's own — so a journal's convention is visibly not a
  reading error (of the novel set's 16,812 such words: 52 per cent two
  lanes, 25 the paper's own lane, 23 the file's). **`split_reasons`** says
  what stands between the pieces of a paragraph that arrived cut: over both
  corpora a caption read between the halves is 40 per cent of them, nothing
  at all on one page a third, and a page break — the one case the boundary
  scorer was built for — a tenth.
- `tree.py`, `_refuse_front_furniture`: a line of the paper's furniture that
  the layout model read as a section heading is refused even where prose
  follows it, and the prose it would have swallowed is the body's. The
  evidence is the line's own shape — `_front_kind` with the vocabulary
  alone, never the embedder, whose wrong verdict would file a section's
  prose under the section before — plus a page's folio ("7 of 9") anywhere,
  and the journal's own name from the record (`_journal_name`, either side
  abbreviated: "Chem Sci" for "Chemical Science"), with the short lines the
  pages repeat standing in where a library holds no record. An affiliation
  or correspondence line is refused wherever it was read, since RSC prints
  its affiliations after the introduction's first lines; a notice or the
  journal's name only before the body begins, which is now read from the
  vocabulary and the author's numbering alone, never the embedder. One
  refused heading no longer suppresses the built-heading pre-pass
  (`structure.build_headings`), and `typography.restyle` keeps a page-one
  heading the running heads repeat only when it reads like a title — four
  words or more — so a three-word banner is furniture. Every refusal is a
  note (`heading-refused`) and a notice node: no text is dropped. Two papers
  of the novel set read better (0.777 → 0.944, 0.705 → 0.787), one 0.008
  worse as a consequence of two correct refusals; the 199 tuned pairs are
  unchanged to the digit.
- `library.py`, `worker.py`, `harness.py`: `build_tree(record={"journal": …})`
  — what the library knows of a paper reaches the reader, and every reader
  passes it, `pairs` through `harness.read_paper` as much as the worker.
- `tree.py`, `_reading_order`: a page read out of order in one column, put back.
  MDPI sets its reference list at the foot of the page and the layout model
  reads it before the text above it, so a conclusion filed under
  "References". Only within one column of one page, only where a heading
  stands in what was read early, and on a page of two columns only where
  both runs stand in the same one; a box too small to hold its block's own
  text (a rotated sidebar) is not trusted. Fifteen papers better, none
  worse.
- `tree.py`, `_recurring_furniture`: a running head is what recurs at the
  same height on three pages; the same words at three different heights are
  the paper's own, and only the occurrences that stand together are dropped.
  Diabetes Care prints "RESULTS" in its visual abstract, again in its
  structured abstract and over the section itself, and both body headings
  had been dropped as furniture (that paper: faithful 0.245 → 0.830).
- `tree.py`, `_mark_abstract_parts`: a structured abstract printed as
  sections — "OBJECTIVE", "RESEARCH DESIGN AND METHODS", "RESULTS",
  "CONCLUSIONS" — is the abstract, not the body: three or more of its part
  names in a row on the first pages, each over short prose that cites
  nothing, are laned `abstract` under the abstract itself (built where the
  paper printed no heading over them), and the citing prose after them opens
  the introduction.
- `typography.py`: a line of capitals is a heading in a paper that sets its
  headings in capitals in the body's own face, marked by nothing else
  (ASTMH): three or more such headings found by the layout model, and a
  block it read as text is relabelled whole — never cut in two.
- `structure.py`, `lane_sections`: a content lane is applied only in a paper
  whose own headings name its methods or its results. A review names
  neither, and its topical sections stay `other`, as its XML keeps them; the
  verdict is stored and noted either way. (An Advanced Science review:
  0.697 → 0.983.)
- Together, on the 199 pairs: faithful by words 0.964 → 0.973, mean 0.959 →
  0.967, well matched 180 → 183, paragraphs intact 0.928 → 0.929, headings
  found 0.882 → 0.884, precision unchanged; 19 papers better, none worse.
- `typography.py`, `depth_by_type`: each heading's depth from its look —
  top-level when set at least as prominently as the paper's core sections
  in every way the page shows (a flat capital's height, capitals, weight,
  an upright face, the same face), nested when set less prominently;
  nothing said for a scan's one-face text layer, numbered subsections set
  like the top level, another face of the same size, figure labels,
  running heads, the reference list's region, back statements after the
  body; unnumbered headings stay below a numbered top level; a number the
  top level does not use, set below it, is a list's; a paper with no core
  section whose headings are all set one way has one level (an editorial's
  topical sections, `depth_by_type_one_level`). `infer_level` takes it
  (`_typo_level`) over its own guess; a fused or merged heading's second
  half is its first's subsection. On the 199 pairs: faithful by words 0.832
  → 0.938 (the oracle for perfect depth: 0.935), well matched 126 → 158.
- `typography.py`: a heading that is the journal's running head or a line of
  furniture is filed as furniture ("Journal of Hand Surgery Global Online"),
  never on the first page, where it is the title.
- `tree.py`: a printed heading is never the child of a heading the reader
  built; front matter ends at the paper's first printed heading after its
  prose has begun; a front-matter box (keywords, highlights, article info,
  a lay abstract, an author summary) does not keep the prose read after it,
  which joins the introduction already read or opens one; a key-message box
  (BMJ's "What is already known on this topic", BMJ Open's strengths and
  limitations, Diabetes Care's article highlights) keeps its bullets, and
  the prose that cites after them returns to the introduction — only while
  no methods, results or discussion section is open; an abstract read
  after the introduction's heading gives back the prose after it; a heading
  that says "Abstract" is the abstract wherever it was read; citation
  shapes "(1, 2)." and "(Author 2024)" open the introduction as "[1]" did.
  Together with depth by type: faithful by words 0.832 → 0.964, well
  matched 126 → 180 of 199, depth agreement 0.886 → 0.942; one paper reads
  worse than before, its XML flat where the page nests.
- `glyphs.py`: ligatures a font drew with a glyph of its own — presentation
  forms, a private-use glyph (RSC's "identi\ue103cation"), a "fi" read as its
  "f" (Hindawi's "identifed"), a ligature kept with its left fragment
  (Wiley's "specifi c") — undone where the result is a known word.
- `edges.py`: a methods section whose text mostly stands before its first
  subheading keeps those paragraphs as methods of their own.
- `parser/litrag_parser/typography.py` (new), run from `recover_from_pdf`
  before the tree is built and again on `rebuild`: the page's type as a
  witness for headings. Every line of the text layer with its runs of
  font, weight and size (pdfium); the body's style; a line set apart from
  it — bold, italic, larger, or on its own row under a deep numbering —
  as a heading. A run-in heading is cut from the front of its paragraph
  ("2.1. Non Surgical Approach. For small tears …" → a heading and a
  paragraph), a heading fused into a paragraph is cut out after the
  sentence before it, a heading no box holds comes back (bold, larger or
  numbered only), a heading's split letters are spelt as the row prints
  them ("I NTRODUCTION"). Nothing is cut before the body's first heading
  (the abstract's labels and the keywords are set the same way), a
  back-matter word inside the body is a table's note, a series of bold
  names (an author list) is left alone, a reference entry is never cut.
  Counted in repairs as `run_in_headings`, `unfused_headings`,
  `recovered_headings`, `retexted_headings`. On the 64 pairs: the XML's
  headings found 0.82 → 0.89, faithful 0.80 → 0.81, reference lists and
  citation links up (with the change to `_entries_follow` below).
- `tree.py`: a heading printed run in at a paragraph's front (`_runin`)
  is a subsection of the section it stands in, whatever its name — PNAS's
  "Ethics Statement." and "Statistics." under Materials and Methods had
  become top-level sections by their names and taken the methods' text
  with them.
- `edges.py`: a methods section whose text mostly stands before its first
  subheading — one late run-in subsection in a long methods section — keeps
  those paragraphs as methods of their own, not a shared preamble that no
  finding can link to.
- `tree.py`: `_entries_follow` looks past a whole block of back-matter
  statements, not eight items: MDPI sets "Funding" to "Conflicts of
  Interest" between the reference entries, and once those were headings
  the list broke at the first of them (reference lists 0.95 → 0.09 on
  one paper) — now the block stays inside the list as Frontiers' single
  statements already did.
- `parser/litrag_parser/outline.py` (new), `outlines` table: the outline
  judge — a local model through Ollama reads the whole PDF as the reader
  built it (headings marked, paragraphs numbered, the reference list and
  reference-shaped paragraphs left out, a window sized to the paper,
  thinking off) and returns its outline as JSON. Taken from it: a lane for a
  section the rules left unnamed and unnumbered — in a review, a section
  the model puts outside the one the reader nested it under becomes a
  topical section, `other`, whatever the model calls it; in a research
  paper (the judge is told the type first) a subsection keeps its section's
  lane and only a section the reader could not place takes the model's —
  and a built heading where it says a section starts inside another, placed
  beside the section its numbering matches or after the top-level section
  it ends, laned as the reader lanes a heading, never by the model's word,
  and never a heading the reader has; not taken: the depth, or any lane a
  heading or a numbering settled (a note instead). Measured on the 64
  PDF/XML pairs with six local models (NOTES.md: Qwen 3 14B 0.80 to 0.91,
  lane agreement 0.88 to 0.96). Rows replayed by
  `rebuild`; asked on `ingest`/`reparse` only with `LITRAG_OUTLINE=on` or
  `outline: true`; `LITRAG_OUTLINE_MODEL` picks the model; an XML is never
  judged. `python -m litrag_parser.outline --pdf-lib DIR --xml-lib DIR
  --model M` scores a model on the PDF/XML pairs, faithful before and after.
- `tree.py`: a back-matter statement the vocabulary knows ("Data
  availability statement", "Funding") read between the entries of a
  reference list stays inside it, as an unknown one already did, and the
  entries after it are entries again: Frontiers sets those statements in
  the left column under the start of the list, and one paper's 93 entries
  had filed as acknowledgements (found when the outline judge was shown
  them and took the paper for a bibliography).
- `parser/litrag_parser/pairs.py` (new): two readings of one paper compared
  — the tree read from a PDF against the tree read from the publisher's
  JATS XML, from two libraries that hold the same DOIs. Text is located by
  four-word shingles of letters and counted in words: recall, *faithful*
  (present and in a paragraph of the same lane), precision, paragraphs
  intact, split, merged or missing, headings found, spurious and at the
  right depth, the reference list, the citation links, the captions, the
  title. `--show KEY` says where a paper's text landed and which headings
  went missing. 199 pairs over three corpora: recall 0.99, faithful 0.78 to
  0.86, precision 0.96 to 0.98 — the text is there, the lane is what goes
  wrong.
- `parser/litrag_parser/confidence.py` (new), `papers.confidence`,
  `confidence_detail`: how far a reading can be trusted, from the reading
  alone. Eleven checks, each a plain measurement of the tree with a limit
  read off the pairs (the share of the prose in the abstract, the back
  matter, the reference list, the largest lane; missing lanes for the type;
  repeated text; odd headings; cut paragraphs; an unsettled type), combined
  as graded penalties with their reasons in words. Scored at every ingest
  and rebuild, in the `tree` event, the harness report and the window.
  `--calibrate` scores the score against saved pairs: at 0.9 or more, 105 of
  129 readings matched their XML well; under 0.5, 25 of 30 were seriously
  off. The reader's older self-measurements (coverage, dropped lines, glyph
  residue, audit errors) predict none of it.
- The window's paper list sorts (as added, format, type, title, year,
  confidence lowest first) and narrows by chips to PDFs or XML, one type of
  paper, or one band of confidence (`app/src/renderer/papers.ts`, tested
  apart from the DOM and in the real window); each card shows its reading's
  confidence, the reasons on hover and in the tree pane's summary.
- `tree.py`, three repairs the pairs found on their first day: a block that
  carries its paragraph twice — a copy cut short and then the whole, or the
  whole and then its beginning again — says it once (`unrepeat`); a lane's
  heading fused with the subheading under it ("Results and discussion
  Contrasting glacier mass balance …") is two headings
  (`split_fused_heading`); and an XML's own section depth stands
  (`infer_level(stated=True)`): 453 headings in 145 of 513 XML papers had
  been nested under whichever section stood open, and whole review bodies
  read as `introduction`.
- `harness.read_paper`: one paper read again from its saved Docling
  document the way a rebuild reads it, shared by the harness and the pairs.
- `worker.pick_doi`: a PDF is filed under its own DOI, not the one its
  dataset or code carries at Zenodo, figshare, OSF, Dryad, Mendeley Data or
  Dataverse, printed above it on the first page; a repository's DOI is used
  only when it is all there is. (A paper already filed keeps its key.)
- `headings.py`: "observation" is a spelling of Results (ASM's article
  type calls its body that) and "author summary" of Highlights (PLOS's lay
  summary), both found when the XML's stated depth brought them back to the
  top level with no lane.
- `pairs` and the harness say in capitals when the embedder did not answer:
  every heading no rule names then reads `other`, and the numbers are those
  of a reading without it.
- `PIPELINE.md` (new): the pipeline on one page — what is current, opt-in,
  experimental or deprecated (the `lit` CLI in `src/`), a paper from filing
  to saved rows, reparse against rebuild, and every `LITRAG_*` switch with
  its default. `README.md`, `AGENT.md` and `CLAUDE.md` point to it.
- `parser/litrag_parser/meaning.py` (new): the one oracle every question of
  resemblance goes to — a *kind* is named groups of example texts, a
  threshold and a margin; the nearest group names a text when it is near
  enough and clearly nearer than the next, else `other`. Verdicts are rows in
  `lanes.sqlite` (`verdicts`, keyed by kind, text and a model string that
  hashes the kind's examples and thresholds); the old `lanes` table is copied
  in once and renamed. Batched embedding, a retry after a minute when Ollama
  fails, a reply with the wrong number of vectors treated as no reply.
  `lanes.py` is the process-wide handle (`configure_from_env`, one guard for
  the worker, the harness and the judge; the worker configures after
  `--root=` so the tests no longer write into the real root).
- Six questions moved off their lists onto the oracle, each asked only where
  the rules had no word (`tree.py`): a line of front matter's kind (`front`),
  a run-in label the list does not know (`label`, the one verdict that vetoes
  a geometry join, counted as `label_veto`), a figure's short legend
  (`figtext`, adds and never removes), a reference entry in an unknown shape
  (`refentry`), which open paragraph a displaced tail or a cut caption
  belongs to (`which`), and a journal's name set as a first-page heading with
  another heading right under it (a notice, not a section). A displaced tail
  no longer rejoins a front-matter line: a keywords line, a date label, an
  author or licence line that ends without a full stop is not a head, with
  or without the oracle (the corpus gate caught a tail glued to "KEYWORDS
  …", eight citations gone with it).
- `parser/litrag_parser/structure.py` (new): lanes from content where the
  headings are silent. Seven lane centroids and a position prior computed
  once from every labelled paragraph of six libraries
  (`data/block_lanes.json`, numbers only) score a section's paragraphs; a
  top-level section whose heading named nothing takes methods, results or
  references when the mean over three or more is clearly of one lane; a
  named heading is never overridden, its disagreement is a note. A paper
  with no heading at all gets headings built by a Viterbi pass over its
  blocks (any combination of lanes, the paper's own order, only back matter
  after the references), labelled `built`; the window shows `[built]`.
  Measured library-out before it decided anything (NOTES.md): the example
  paragraphs alone were near chance, the centroids name a section wrongly
  about four times in a hundred at the margin used, and on the pilot corpus
  they lane nothing an author left unnamed.
- `parser/litrag_parser/boundary.py` (new): whether B continues A, by
  likelihood — Qwen2.5-0.5B through transformers on the CPU scores the first
  words of B after the last of A against alone. Calibrated on 600 pairs cut
  from the corpora's own paragraphs in the judge's shape (precision 0.92,
  recall 0.92 paper-out at 1.05; 1.5 shipped, where precision is 0.97) and checked on the 71 pairs the generative judge had
  ruled on, where it would join five of 69 keeps: off unless
  `LITRAG_BOUNDARY=on`. `Judge` takes a `scorer` and asks it before the
  model; verdicts are `judgments` rows either way. `_judge_candidate` no
  longer treats a zero-width space after a full stop as an unfinished
  sentence.
- `LITRAG_VOCABULARY=off` (an experiment, never the default): the heading
  vocabulary names nothing and the embedder names every heading alone,
  abstract and references included. Heading by heading it agrees with the
  vocabulary on every lane retrieval searches (methods 98 %, the rest 99–100 %)
  and leaves most of what it misses as `other`; NOTES.md has what it costs at
  the paper level.
- `parser/litrag_parser/edges.py` (new) and an `edges` table: a finding (a
  results paragraph, or a discussion paragraph that cites a figure) is
  linked to the methods subsection that produced it — by a pointer in its
  text, else by terms only that subsection owns, else through the caption of
  the figure it cites, else by resemblance when the nearest candidate
  clearly wins (off unless `LITRAG_EDGES_SIMILARITY=on`) — and a finding no
  evidence places stays unlinked. Paragraphs
  are linked to the figures and tables they name. Written at ingest and
  rebuild; an `edges` op and a "Measured by" / "Findings measured here" box
  in the window's detail pane, each edge a click to the other node; the
  harness counts findings, links by evidence and unlinked; `python -m
  litrag_parser.edges --measure` reports how often terms and resemblance
  agree with the pointers when the pointers are hidden.
- `Tree.notes` and `audit.py`: `built-heading` (info) on every heading the
  reader built; `lane-disagreement` and `lane-suggested` (info) from the
  notes; the worker writes each note as an `events` row.
- `harness.py`: per top-level section the heading, its lane and its label
  are recorded; `compare` gains `lane_lost` (a named section whose lane went
  to `other`, or a built heading no longer built) and it joins the gate;
  the report says what each kind named and whether the embedder was
  reachable. `library.py`: `parsed_papers` and `safe_key`, replacing three
  copies of each.
- `parser/litrag_parser/paper_type.py` (new), `papers.type`, `subtype`,
  `type_source`, `type_detail`: what kind of paper each is — research,
  review, case report, letter, editorial, protocol, data descriptor,
  correction, other — and its subtype where a label states one (rct,
  clinical-trial, systematic-review, meta-analysis, case-series,
  brief-report, methods, perspective, erratum, …). One table (`LABELS`)
  maps every spelling every source uses to one canonical type: Europe
  PMC's publication types (MeSH's), the JATS `article-type` and the
  `<subject>` line, the title's own words, the label printed above the
  title; and says which labels name a kind and which are a publisher's
  default bucket ("research-article", "Journal Article", "Article"). The
  most trusted specific label decides, in the order record, file, subject,
  title, page; a default alone never makes a research paper — the shape
  must agree — and every disagreement, label against label or label
  against shape, is a `type-disagreement` note the audit shows. The shape
  (lanes present, a case heading, a letter's opening, a systematic review's
  headings, a data descriptor's, a protocol's future tense) decides on its
  own only for the types its rule was measured precise on (`SHAPE_DECIDES`:
  review, case report, data descriptor, and research by default and shape);
  the profile kind stays off unless `LITRAG_TYPE_PROFILE=on`. The shape's
  research rule needs a results lane, or Nature's order — the methods
  after the discussion, the results under the main text's own headings —
  or measurements (±, p-values, n =, means) in a tenth of the body's
  paragraphs: a review with a methodology section and no results (twelve
  in the corpora, "2. Methodology", "5. Extraction Methods") read as
  research before and is unread now, while a Nature or PNAS paper still
  reads as research. A protocol named in a subtitle ("…: protocol for an
  11-hospital multicenter randomized controlled trial", "…: The CROSSMIRV
  Trial Protocol") is a protocol before the trial rule reads the same
  title; Data in Brief's fixed headings ("Value of the Data", "Data
  Description") name a data descriptor beside Scientific Data's. Measured
  with the stated sources hidden, the shape alone is right 0.93 of the
  time (research at precision 0.87, from 0.76) and the whole cascade 0.90
  (from 0.85). The audit
  expects a methods section of a research paper, a case report and a
  protocol and not of a review, a letter or an editorial; the harness
  reports types, subtypes and disagreements; the paper card says the type,
  subtype and source. `python -m litrag_parser.paper_type --fetch` stores
  the record, `--measure` scores every source alone against the stated
  labels and the whole cascade with them hidden (NOTES.md has the table).
- `parser/litrag_parser/record.py` (new), `papers.authors`, `journal`,
  `year`: who wrote the paper, where and when. A JATS file's contributor
  group (names, affiliations resolved through their ids, the corresponding
  author; editors left out) and its journal and year are read on every
  parse and rebuild; a paper with a DOI or PMID gets Europe PMC's record
  once at ingest — author string, journal, year and publication types in
  the one call the type already made — and the file's word overrides the
  record's. The window shows the byline on the paper card and above the
  tree. Nothing is inferred: a PDF the record does not know keeps its front
  matter's `authors` and `affiliations` lines and empty columns.
- `parser/litrag_parser/headings.py` (new), `nodes.canonical`: headings
  canonicalised. The author's heading stays as written and beside it stands
  the catalogue's name for that kind of section — "Materials and methods",
  "Results and discussion", "Conclusions", "Case presentation", "Conflicts
  of interest", "Data availability", "Ethics", "Supplementary material",
  "Author contributions", "Funding", "Abbreviations", "Footnotes" and the
  rest, thirty names — from the spellings 514 XML files of the six libraries
  were found to use (`--harvest` counts every titled section with the lane
  the vocabulary, the file's `sec-type` or the parent gives it) and a few
  families. An exact spelling of a top-level name settles a heading's depth
  and, where the vocabulary was silent, its lane. Where the catalogue is
  silent the embedder answers against centroids learned from the harvest,
  one per lane and one per name (`data/headings.json`, made by
  `--make-centroids`, numbers only), measured library-out before they
  decided (`--measure`, NOTES.md): the lane prototypes — the per-name
  centroids grouped by lane, the best counting — replace the hand-picked
  example headings of the `heading` kind, at precision 1.0 on every lane
  library-out; a `back` verdict by meaning needs a margin of 0.12, since a
  review's "Available treatments" lies a little nearer "Data availability"
  than anything else and a real statement lies far nearer; and a heading
  that carries a body number takes no abstract, references or back lane by
  meaning at all — of 3,664 back-matter sections across the three corpora
  the one numbered heading was a review's "8. Regulatory and Ethical
  Considerations", which lies 0.82 from back matter and 0.12 clear of the
  next lane, a body section by its number. Built headings take the
  catalogue's names, the corpus's modal spellings ("Materials and methods",
  not "Methods"). The window shows the name after the heading when the two
  differ; the harness counts named sections.
\1 Docling
  reads the two-column page as banner, dates, the introduction's heading
  and first lines (the left column), the affiliation footnotes, the licence,
  then the title, the authors and the one-paragraph abstract — so the
  introduction's opening was dropped as a label above the title, its
  heading became a notice, the authors line (Vietnamese names with
  affiliation letters between them) was taken for the abstract, and every
  introduction paragraph after the abstract landed in it. Now a heading the
  vocabulary knows before the title leads the body with the prose under it,
  the long paragraphs after the abstract follow it, the lines above the
  title that the rules can name (dates, affiliations, correspondence) are
  kept as front matter, an affiliation is never an author line whatever its
  markers, a lone "a" is the article, a year is not a marker, a dates line
  is never the title, a licence line is never the head a displaced tail
  rejoins, and front-matter lines between a head and its tail no longer
  close the rejoin window. `repairs.reordered` counts the items led back.
  A citation set as a spaced superscript after the full stop ("(PL). 1 - 3
  These"), or marked by recover.py's caret ("mucins.^1"), now counts as one,
  so an introduction orphaned before its heading under a long abstract is
  given a built "Introduction" where the paragraphs start citing — in a
  paper with no abstract heading, and inside an "Abstract" section whose
  paragraphs go on into the body (JATS from PNAS, NEJM, OUP and the letters
  and editorials that print no heading at all: the held-out sets have
  forty-odd). What cites but is not the introduction is left where it is:
  a "Citation:" or "To cite this article" line at any length, an
  affiliation block whose numbering reads like a superscript ("China. 2
  Department of …"), an author line with its degrees, "Abstract: …".
  "Main" and "Main text" — the wrapper Nature's and OUP's JATS open the body
  with, whose own paragraphs are the introduction — are the introduction
  lane and stand top-level; their topical subsections inherit that lane
  until content lanes reach subsections (BACKLOG). When the unheaded
  stretch after the abstract runs on into results and discussion (Wiley's
  communications print nothing before "Experimental Section"), the block
  lanes cut it into runs with built headings (`structure.build_headings`
  over the stretch), the first run being the introduction whatever its
  paragraphs resemble; fewer than six blocks get the one built
  "Introduction". Wiley's footnote block of institutes is front matter at
  any length; "correspond" means "Corresponding author" or
  "Correspondence", not "correspondingly" in a results paragraph; a
  correspondence line needs an e-mail's shape, not a "@" in a formula
  ("Bi2WO6:Yb,Er@CuS@CS"); "The authors have no conflicts of interest",
  "Funding:" and the like are `funding` by rule.
- `parser/tests`: `conftest.py` (no oracle leaks between tests; the
  subprocess worker runs with `LITRAG_LANES=off`; a toy embedder for the
  tests that prove plumbing and replay), `test_meaning_sites.py`,
  `test_structure.py`, `test_boundary.py`, `test_edges.py`,
  `test_paper_type.py`, `test_record.py`; 138 tests.

Runs natively on Windows, on the GPU.

- `parser/`: torch and torchvision come from PyTorch's cu130 index on
  Windows (PyPI's Windows wheel is CPU-only); other platforms are
  unchanged. Docling reports `ready on cuda:0` on an RTX 5080.
- The worker no longer deadlocks on Windows: a thread blocked reading the
  stdin pipe stalls every DLL load in the process, so the first `import
  numpy` on the ingest thread never returned. stdin is now polled with
  `PeekNamedPipe` there (`request_lines`); other platforms keep the plain
  blocking read.
- A JATS paragraph with `<italic>` or `<sup>` in it is one paragraph again.
  Docling's JATS backend cuts such a paragraph into stripped runs in an
  `inline` group — "solution (", "w", "/", "v", ") prepared" — and the tree
  builder flattened the group into five nodes. Inline groups are now joined
  back, with the spaces Docling stripped restored at the boundaries that
  take one (`_join_inline` in `tree.py`); `rebuild` repairs a library
  without re-running Docling. On the Micromachines JATS: 187 nodes → 140,
  methods 50 → 23.
- MathML formulas in JATS survive. Docling's JATS backend reads a formula
  only from `<tex-math>` and Europe PMC's XML carries MathML alone, so the
  equation between "the equation below:" and "where …" vanished, and the
  inline symbols with it. `mathml.py` linearises each `<mml:math>` —
  `Crosslinking Degree=100×(1−A_x/W_x)/(A_{N_avg}/W_{N_avg})` — and hands
  Docling a `<tex-math>` beside it, before the bytes reach the converter.
  The file on disk is untouched. Needs a `reparse`, not a `rebuild`.
- Runs the layout model cuts loose from a PDF — an italic "p" as its own
  paragraph between "higher (" and "< 0.001) compared" — are stitched
  back into their sentence (`_stitch_fragments`); a list item or footnote
  whose text Docling put in a group beneath it gets that text.
- Decoration is not a figure: an uncaptioned picture that is tiny, or that
  sits at one spot on three or more pages (a journal's logo), is left out
  of the tree and counted in `dropped`, which the `tree` shape now carries.
  A Springer PDF went from 316 nodes to 285.
- The first header is the title only when the vocabulary does not know it:
  "Abstract" or "2. Methods" as the first header no longer becomes the
  paper's title and takes the abstract into front matter (from BACKLOG).
- `audit`: every node against its neighbours. `python -m litrag_parser.audit
  --lib <dir>` (or the worker's `audit` op) grades what reads wrong —
  fragments, sentences starting with "=", a colon with no equation after
  it, echoed headings, a running head taken for the title, uncaptioned tiny
  pictures, formulas Docling saw but did not read — as error, warn or info,
  with the node id and its text. The fixtures audit clean of errors, and
  the tests keep it so.
- The window shows an XML paper as a paper: with no page to draw, the page
  pane renders the tree as a document — headings, paragraphs, formulas,
  tables, figure placeholders with their captions — and a click on either
  side selects the node on both.
- The reader measured on the corpus, and fixed where the measure said.
  `harness.py` runs every parsed paper of a library through the tree
  builder, the linker and the audit (`npm run harness -- --lib DIR`), reports
  titles, methods, front matter, errors and citations per paper and for the
  corpus, and gates a change against a saved run. On the 227 papers of the
  two pilot libraries it found and the fixes below repaired:
  - Wiley's and Elsevier's JATS put the section number in `<label>` and
    Docling took the label as the heading, so "2." carried no role and the
    whole body filed under Abstract; `jats_prep.py` folds the label into the
    title ("2. Materials and Methods"). 19 papers regained their methods
    section; the JATS side went from 33 to 52 papers with methods and 149 of
    151 with methods or the skeleton of a review.
  - The title was the publisher's label ("PAPER", "Full length article",
    "RESEARCH ARTICLE") or the journal's name above the real title. The
    title is now picked among the first page's title-like lines — a Docling
    title over a header over text, never a generic label, a running line or
    the authors — with the PDF's own Title metadata deciding between
    candidates or standing in. 74 of 76 PDFs are titled, from 41.
  - Authors, affiliations, dates, correspondence and keywords were one
    paragraph node each; they are now typed `meta` nodes in the front
    matter, one per kind, and the lines above the title are dropped as the
    label they are. An abstract whose heading the layout model missed
    becomes an Abstract section. Elsevier's letter-spaced "a b s t r a c t"
    reads as a heading again.
  - The same paragraph twice (a column read twice) is deduplicated, and a
    running head with its page number recurring across pages is furniture.
  - Audit errors over the corpus: 94 to 52; papers whose front matter ran to
    27 nodes now have at most 12.
- An end-to-end suite under Playwright Test (`app/tests/e2e`, `npm run e2e`)
  drives the real window and worker: a library, an ingest, and what a
  person checks on screen — titles, methods, the tree, the page, the
  reading view, the citation badges, the audit. The JATS fixture in ~15 s
  without models; `LITRAG_E2E_PAPERS=<dir>` for a folder of real papers;
  `LITRAG_HEADLESS=1` opens the window hidden and rendered offscreen, so
  the suite runs with no screen. The suite found two things in the window:
  a node clicked while its PDF was still opening drew nothing (the page now
  waits for the open), and an entry reached from a citation sat in the
  folded References section (a selected node's ancestors now unfold).
- The PDF's text layer, read back where the layout model fell short
  (`recover.py`). A line no box holds — the top of a column after a page
  break, a paragraph beside a wide figure — comes back as a paragraph at
  its place; a body block the model called a page footer is kept; a box
  whose text lacks lines the layer has inside it is rebuilt from the
  layer, de-hyphenated; an empty `formula` takes the text inside its box;
  a table's footnote the cells do not carry becomes a paragraph. Every
  block learns its first-line indent and last-line width, and the join
  rules use them: in a paper that indents, a full last line before a
  flush-left first line is one paragraph, full stop or not, and a short
  last line keeps the judge quiet. On 76 PDFs: 518 blocks recovered, 69
  rebuilt, 51 equations read, 712 joins from geometry, 4 from the model;
  sentences in the text layer and in no node 156 → 53; audit errors over
  the corpus 50 → 22 (94 at the start of the day; 211 of 227 papers now
  audit clean, from 165); 21 papers gained citation links the recovered
  text carried. A table's footnotes, children of the table in Docling's
  structure that the walk never descended into, are nodes after it. Everything derives again on `rebuild`; the raw document on disk
  is untouched.
- A junk item — ")" , "|", a control character — is dropped and counted;
  a copyright line ("& 2012 Elsevier Ltd.") is furniture; a paper has one
  Front matter section; the audit no longer calls "Not applicable." under
  two headings an echo, nor a sentence starting "With" a missing equation.
- A local model reads the pairs the joining rules leave alone. `judge.py`
  asks Ollama (qwen3:14b, thinking off, ~0.15 s a pair) whether two
  adjacent blocks — one ending without a full stop, the next starting
  with a capital — are one paragraph, and files the verdict in
  `judgments` so `rebuild` reuses it without a model. `npm run judge --
  --lib DIR`, the worker's `judge` op, or `"judge": true` on an `ingest`.
  Only where the rules are silent; where they join, they join.
- A paragraph continues across what the page put between its halves — a
  figure, a table, a caption, a running head — not only across nothing.
  The example that showed it: "A cell seeding density of 1 - 10 6" on one
  page and "cells/mL-gel was used…" on the next, with the journal's
  running head between them.
- `glyphs.py` undoes what older Wiley and Elsevier fonts do to symbols in
  the text layer: "pH ¼ 7.4" → "pH = 7.4", "medium þ 10%" → "medium +
  10%", "1 - 10 6" → "1 × 10^6", a NUL for the minus of "Å⁻¹". 82 "¼"s and
  5 "þ"s in 8 PDFs of the corpus. Only in the contexts that make it safe;
  a quarter and an Icelandic name keep their letters.
- The harness measures page coverage for PDFs — the share of each page's
  words (pdfium's text layer) that reached a node on that page — and lists
  the pages under 60%, and — the sharper number — the sentences in the
  text layer that no node holds, with running heads, the title, table
  cells and figure text excluded: 156 in 34 of 76 PDFs, listed per paper
  with samples. It also counts the glyph residue the rules do not cover
  and the joins the judge made.
- In-text citations are linked to the reference list. Every entry in the
  references lane is a row in `refs` (number, first author, year, DOI, and
  from a JATS file its id, PMID and title); every marker a node's text
  carries — "[6,9,12]" in a numbered journal, "(Lyon, 2020; Fields and
  Levin 2022)" or "Levin (2019)" in an author–year one — is a row in
  `citations` tying the node to the entry (`citations.py`). Unlinkable
  beats mislinked: a marker that names no entry is left alone, and an
  author–year that fits two entries links both. On the Micromachines JATS,
  the XML's own `<xref>`s make 71 paragraph–entry pairs; the linker finds
  72 and every one of the 61 entries is cited. Superscript numbers are the
  third style: in a JATS they are bracketed before Docling reads it
  (`jats_prep.py`, which also carries the MathML step), and in a PDF, where
  the layout model glues them to the word before ("injury.1 Worldwide"),
  they are read only when the paper brackets nothing, glues plenty, and
  every number names an entry — "µm3" and "CD34" never become citations.
- ACS leaves its citation `<xref>`s empty (`<xref rid="ref14"
  ref-type="bibr"/>`) and lets a stylesheet print the number; the
  pre-processing fills in the entry's position in the reference list, and
  two of them joined by a dash become one range, "[5–7]".
- Nine JATS files of the archive were refused by Docling as "format not
  allowed": JATS 1.3+ opens with `<processing-meta table-model="xhtml">`,
  and Docling's sniffer takes the word for XHTML. `jats_prep.py` drops the
  element (it says nothing about the article) before Docling reads it.
- The worker's `refs` op lists a paper's entries with the nodes that cite
  each; the `tree` carries each node's `cites` and each entry's `ref_no`;
  the window shows "→ 3" on a citing node and "[12] ← 5" on an entry, and
  the detail pane lists them both ways, each a click away.
- A paragraph the layout model split at a page or column break — mid
  citation, "…(Fields et al., 2022, Friston et al.," then "2023, Fields,
  2024)-to optimize…" — is joined back (`_continues` in `tree.py`): an
  unfinished sentence followed by a lowercase word, a closing bracket, a
  bracket it opened, or a paragraph after a trailing comma or "and", on
  the same or the next page. Counted in the tree's `repairs`, as the
  stitched fragments are. Springer's "1 3" page mark, and any short text
  recurring on three pages, is dropped as furniture.
- The wire is UTF-8 on every platform: the worker reconfigures its stdio
  (Windows opened the pipe as cp1252, which cannot carry a Greek letter
  in a title) and the app sets `PYTHONUTF8=1` for it.

- Fifteen systemic defects found by probing the corpus, fixed in impact
  order (2026-09-11, night). The text layer first, since two of them were
  the recovery pass's own:
  - `recover.py` read only the first box of an item, so the second page of
    every paragraph Docling carried over a page break (722 in 76 PDFs) came
    back as a free block: 508 of the 518 "recovered" blocks were
    duplicates, and the geometry rule then glued those tails to the wrong
    heads (267 joins across a full stop). Every box of every item is read
    now; the recovered blocks are the real ones (9), and a paragraph's
    geometry comes from all its pages.
  - pdfium's line breaks run the first lines of a paragraph together, so a
    first-line indent read as zero and the join rule saw a continuation
    where the page shows a new paragraph. Rows are cut where the baseline
    moves (`_visual_rows`), from the character boxes.
  - A superscript is marked, never fused: the layer's rows carry "10^7",
    "cm^-1", "applications.^17" (a raised digit well above the baseline,
    with a sign before it when one is there); Docling's own "10 7 cells",
    "of 10 5" and "1 × 10 6" are closed up by `glyphs.py`. The citation
    linker reads "^17" and "applications. 17 While" alike. Wiley's rotated
    "Downloaded from …" sidebar, which Docling glues to the body blocks of
    the pages after it as one item, is unglued: each body piece is a block
    at its place, the sidebar is furniture.
  - Geometry has no word for two blocks far apart in one column, or when a
    run-in label ("Keywords", "Funding", "Conclusion:") opens the next; a
    lowercase tail never continues a finished sentence; a tail whose head
    sits a few items back — past a figure, a caption, a table's note — goes
    back to it (`_displaced_head`); a caption cut by its figure takes the
    tail that follows the figure; the end of a paragraph read twice is
    dropped. A table's note Docling glued to the paragraph before the
    table, or set as a paragraph under it, is the table's footnote.
  - The Symbol font's control codes read by their contexts: "37 \x0e C" →
    "37 °C", "p \x14 0.05" → "p ≤ 0.05", "\x15 5 cm" → "≥ 5 cm", "2.3 \x06
    0.3" → "±", "\x18 20 µm" → "~", "pH \x19 7-8" → "≈", the dot between
    Springer keywords, "Or\x13efice" → "Oréfice"; the oldest Wiley files'
    digits for symbols ("37 8 C", "0.59 6 0.06", "N 5 3", "1 3 PBS", "P \
    .05", "960 cm 2 1", "63 3 Leica"); Elsevier's digits set apart ("10,0 0
    0", "(20 03)"). 113 control codes in 22 PDFs, none left.
  - Ligatures the font subset split — "signi fi cantly", "were fi xed",
    "speci fi c", "sti ff ness", "cut o ff" — are mended by the paper's own
    words: a stray "fi"/"fl"/"ff" token joins both neighbours when the
    whole is a word seen elsewhere in the paper or the left piece is a
    fragment, the right one when the left is a word in its own right. 1,658
    in 17 Elsevier PDFs, 2 left. The layer's whole words replace Docling's
    text wherever the two agree letter for letter.
  - JATS reference lists: an `<element-citation>` (fields, no running text
    — Docling read 30 entries of 214) is rendered into a `<mixed-citation>`
    line before Docling reads the file, and a `<ref>` with two citations
    (Wiley's "a)", "b)") becomes one entry, so the numbers in the text name
    the right entries; entries align to the XML by `<label>`. Needs a
    `reparse` for papers already read; 57 of the archive's were. JATS
    citations 21,555 → 25,880.
  - Four Wiley PDFs reach Docling with no "References" heading: a run of
    eight or more entries — numbered, or a name and initials, each with a
    year — in the back part of the paper opens an inferred References
    section. ACS's "■ REFERENCES" is read past its bullet. Entry numbers
    come from the numbers the paper prints when they run in order, so a
    fragment of an entry no longer shifts every link after it; the list
    item's marker ("[12]") is kept in its text. PDF citations 3,126 →
    4,870 in 61 of 66 papers with a reference list, from 52.
  - Front matter no longer ends at the abstract's heading: keywords, a
    copyright line, the journal's home page, a correspondence address
    between the abstract and the introduction are `meta` nodes; Elsevier's
    "A B S T R A C T" set as text opens the abstract; a numbered "Summary"
    at the end is a closing section, not a second abstract; a JATS author
    line with no markers (a comma list of names) is `authors` (98 papers
    were `other`, none are). An empty wrapper section — PMC's "Associated
    Data", a bare "Declarations" — is dropped and counted (`dropped.empty`,
    123 in 88 papers).
  - A table Docling drew but could not structure gets its rows from the
    text layer, one cell per gap. A PDF with no DOI or PMCID on its first
    pages is asked for on Europe PMC by its title before it gets a hash key
    (`identified` event; `offline: true` skips it); three of the archive's
    nine hash-keyed PDFs are found.
  - Corpus, day start → now: audit errors 94 → 21; papers auditing clean
    165 → 212 of 227; sentences in a PDF's text layer and in no node 156 →
    75 (17 of them code listings in one supporting-information file);
    citation links 24,225 → 30,750; control codes 113 → 0; split ligatures
    1,658 → 2. `npm run check:all` green (42 + 4 + 74 tests), the headless
    e2e on the fixture (7) and on six real papers (6).

- Every paper of the two pilot libraries audits clean: 227 of 227, from 212
  (2026-09-11, night). The fifteen that did not, fixed at their sources:
  - The audit called a colon before a list or a heading a missing equation
    ("as follows:", "discussed below:") and did not see an equation written
    as prose in the next paragraph ("Tensile strength = Break load/…").
    The rule now needs formula wording before the colon and accepts a
    following line that is an equation or a list's first item.
  - Docling drops a JATS formula in four shapes, all now handed to it as a
    `<tex-math>` before it reads the file (`render_formulas` in
    `jats_prep.py`): Springer's and IOP's `<tex-math>` that is a whole LaTeX
    document (cut to its maths), `<alternatives>` (unwrapped), a formula
    that is only italic and sub/sup markup (set as a line: "ϕ_ex = ϕ_in ×
    e^(−kd)"), a display formula alone in a `<p>` (lifted out). A formula
    the file carries only as an image gets the line "[equation as image:
    <file>]" — a formula node that says what stands there — and the audit
    grades it a warning, `image-formula`, not an error. `<mfenced
    separators="">` no longer gets commas. A stray ">" before a
    reference's authors is dropped. Needs a `reparse`; the seven papers
    were.
  - Four stitching rules: a Greek letter or symbol cut from the word after
    it ("Δ" + "EI difference") goes back onto it; an axis label beside a
    figure ("-5") is junk; an equation the layout model read as text after
    one it read as a formula is a formula; the definitions cut in front of
    themselves ("= the measured …") are a duplicate; an entry split after
    its journal's abbreviation ("Adv. Mater." + ", 2400084.") is one entry.
  - Everything else unchanged: citations, titles, methods, dropped
    sentences as before; `npm run check:all` (42 + 4 + 78 tests), the
    headless e2e on the fixture (7) and on six real papers (6).

- The reader measured on papers that played no part in its rules: 164
  open-access papers from Europe PMC across 24 topics far from the pilot
  libraries (neuroscience, soil microbiomes, perovskites, vaccine trials,
  coral reefs, batteries…), 162 as JATS and 35 as PDFs, in two scratch
  libraries. What broke there, fixed at the source:
  - Docling's JATS backend reads a title's `.text` only: a title with
    inline markup was cut at the first tag ("Ultrafiltered Mulberry (",
    "Development of g-C"), and a section title that opens with `<italic>`
    crashed the backend into an empty document that reported success —
    three papers with nothing in them. `jats_prep.py` folds inline markup
    into every title; the worker now fails a reading that produced no text
    and no tables instead of filing an empty paper, and the audit grades an
    empty tree `empty-document`.
  - Numbers in parentheses — "(1)", "(3, 4)" — are the citation style of
    Frontiers, Science, NAR and JCI: a JATS `<xref>` inside the publisher's
    parentheses is rewritten with brackets before Docling reads it, and a
    PDF in that style is read under the same guard as superscripts (no
    brackets, five or more, most naming an entry). 34 held-out papers gained
    links; 9 had none.
  - A body whose paragraphs stand before any section (a letter, a case
    study) filed under the abstract; they are wrapped in a section titled
    "Main text". "Methods and dataset", "Experimental methodology",
    "Experimentation", "Proposed methodology" are methods. A figure's
    legend Docling nests under the figure without naming it a caption
    (Frontiers) is the picture's caption. A list item with no words (Wiley
    gives one per item, the words apart) is dropped. The title of a paper
    whose structured abstract stands above it on the first page (NEJM) is
    found past the abstract's labels. "FF" (a fill factor) is not a
    ligature.
  - Held-out, before → after: XML titles 154 → 162 of 162, methods 108 →
    116 (153 with a review's skeleton), clean 159 → 160 of 162 (the three
    left: two equations the file has only as images, one entry the
    publisher lists twice), citations 18,455 → 21,142 in 158 of 160 papers
    with a reference list; PDFs clean 34 of 35, citations 2,316 → 2,695,
    dropped sentences 100 → 54; glyph residue 18 → 0. The pilot corpus,
    gated against its last run: no losses, links gained in 7 papers.

- Generalised further on a second held-out set: 204 more Europe PMC papers
  from 30 topics, 2000 onward, 200 as JATS and 141 as PDFs (a fifth of
  the PDFs Europe PMC renders were a different set of publishers again).
  - Every threshold in points is now a multiple of the paper's own body
    line height, measured from its text layer (`_unit_of` in `recover.py`,
    `doc["_unit"]`): a sidebar is narrower than 1.6 lines, a first-line
    indent counts above 0.6, blocks join within 2.4, and so on. Identical
    numbers on every corpus, so nothing tuned to 10-point type was load-
    bearing, and a preprint set larger is read by the same rules.
  - Docling's JATS backend reads the data of a processing instruction as
    text, so Europe PMC's `<?cloudpmc-path …?>` inside every graphic came
    out as a paragraph of storage paths; they are dropped before it reads
    the file. An inline formula that is only an image is "[formula]".
  - Methods headings the vocabulary missed on real papers: "Research Design
    and Methods", "Participants and Methods", "STAR★Methods", "Method
    details", "Experimental Section/Methods", "Data and Methods"; and a
    fallback — a heading that carries the word methods, methodology or
    experimental section, with no results or discussion in it, is methods.
    Pilot methods 52 → 65 of 151 JATS and 58 → 64 of 76 PDFs; held-out 116 →
    120 and 170 → 178.
  - Prose of forty words or more above the title is never thrown away as a
    label (an abstract that Docling set before the title lost 41 sentences
    on one page); an author line, a licence sentence or a sentence of prose
    is never the title; a title set below the journal's banner and the
    citation line (RSC) is taken from where it stands; the PDF's Title
    metadata is matched on letters, so a lost hyphen does not lose it; a
    title may open with a gene name in lowercase. A figure's legend set as
    text under the figure is its caption. "Abstract:" under an Abstract
    heading no longer opens a second abstract.
  - The worker's DOI sniff wants a digit after the slash: "10.1073/pnas" at
    a line's end is the prefix of a DOI, not one. The harness compares a
    paper's XML and PDF apart.
  - The window keeps the old reference list until the new one arrives and
    redraws the selected node's detail when it does: a node clicked while
    the list was loading showed no links.
  - Held-out 2, first run → after: XML methods 170 → 178 of 200 (187 with a
    review's skeleton), clean 196 → 198; PDF titles 37 of 37 → 140 of 141,
    methods 126 of 141, clean 134 of 141, links in 130 of 139 with a
    reference list. Two PNAS PDFs crash Docling's layout stage (an access
    violation in the process, not a Python error) and are set aside — see
    BACKLOG.

- Headings named by meaning (2026-09-12). Karim: the vocabulary is a list of
  observed phrasings and a truly novel paper will not match it; an embedder
  would see that "Methods and materials", "STAR Methods" and "Experimental
  section" all mean methods, and stay deterministic. `lanes.py` does that: a
  heading the vocabulary in `facets.py` does not know is embedded with
  nomic-embed-text through Ollama alongside a few prototype headings per
  lane, and takes the lane whose prototypes lie nearest when the nearest is
  near enough (cosine ≥ 0.75) and clearly nearer than the next (margin ≥
  0.08); a review's topical heading is near nothing and stays `other`. The
  thresholds come from a measurement over the 985 distinct top-level headings
  of the three corpora (agreement with the vocabulary on 395 of the 413 it
  decided; the near-misses among the 572 it called `other` were exactly the
  tail). Verdicts are rows in `lanes.sqlite` beside the libraries, so
  `rebuild` needs no model, a second machine reproduces the tree, and a wrong
  lane is a row to delete; `LITRAG_LANES=off` turns it off; with Ollama down
  an unknown heading is `other`. Two rules around it: `abstract` and
  `references` are never named by meaning (the vocabulary names them
  exactly), and a lane found by meaning keeps the depth the page gave the
  heading — "Statistical analysis" under Methods is methods but not a
  top-level section, "Methods Coral core collection" (Docling merged the
  heading with its first subheading) at the page's top level is. Over the
  three corpora the embedder named 270 headings; it also undid eleven wrong
  lanes the previous night's word rule had given to reviews' topical headings
  ("4. Method for Detecting…"), so the pilot's methods count fell from 65 to
  56 of 151 JATS and is now right. Wiley's "3 | Results" is normalised.

## 0.2.0 — 2026-09-11

The tree, the app, and a second language.

- `parser/`: a Python worker (uv) that reads PDFs and JATS XML with Docling
  into one node tree — sections, paragraphs, tables with their cells,
  figures, captions — each node with its parent, depth, ancestry, role and
  provenance (page and box for a PDF), stored as rows in SQLite beside the
  raw Docling document. Speaks JSON lines over stdio: `hello`, `libraries`,
  `init`, `ingest`, `reparse`, `rebuild`, `papers`, `tree`, `node`,
  `section`, `events`, `sql`, `file`, `parse_json`.
- Facets: a node's role is the role of the top-level heading above it,
  inherited down; the vocabulary is finite and anything else is `other`.
  The tree builder repairs what the layout model gets wrong on real
  papers — flat heading levels, a heading merged into the list item below
  it, a heading echoed across a page break, a doubled heading — stands in
  a visibly untitled section where a heading was dropped, and files what
  comes before the first heading as front matter.
- `app/`: an Electron window that shows papers as they are ingested — filed,
  models loading, layout, tree, saved — the tree of a paper with its lanes,
  and the page a node came from with its box drawn on it; tables as grids;
  drag-and-drop PDFs; the worker's own log.
- Tests: 16 parser tests over two saved Docling documents (a PDF each from
  Nucleic Acids Research and Micromachines, and the latter's JATS), the
  app's protocol tests, and a headless smoke run under Xvfb. CI checks the
  parser, the app and the CLI.
- The `lit` CLI of 0.1.0 is unchanged and not yet connected to the tree
  store.

## 0.1.0 — 2026-09-03

The literature loop, moved out of Protracker into a repository of its own.

- `lit init`, `search`, `add`, `fetch`, `ingest`, `annotate`, `extract`,
  `refresh`, `snowball`, `wanted`, `status`, `papers`, `query`, `sql`,
  `entities`, `graph`, `config`, `doctor`, `libraries`, `where`.
- Europe PMC as the source: search, open-access JATS full text, reference
  lists, text-mined terms.
- JATS and PDF reading into sections; ~250-word chunks cut at sentences;
  a miner for every value with a unit.
- Embeddings on the CPU (`bge-small-en-v1.5`, with the BGE query
  instruction) or through Ollama.
- Retrieval: bm25 over words, cosine over meaning, and a personalized
  PageRank walk over papers, chunks and entities, fused by reciprocal rank;
  `--trace` shows the seeds and each hit's ranks.
- The model stage through Ollama: claims, materials, methods and named
  parameters as JSON rows against a schema, resumable per paper.
- Libraries tied to Protracker projects through `pt --json show` when `pt`
  is on the PATH; standalone otherwise.
- Forty-two tests over fixtures from Europe PMC and a fake Ollama.
