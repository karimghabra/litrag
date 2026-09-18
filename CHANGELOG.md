# Changelog

## Unreleased

Every modification the reader makes, on the page where it happened — and the
whole corpus read that way, so the reader can be checked rather than trusted.

- `parser/litrag_parser/changes.py` (new): the counters the reader always kept
  (`repairs["stitched"]`, `dropped["furniture"]`) now carry a record beside
  them — the page, the box, the text as it stood, the text as it stands, and
  the rule's own reason. `Repairs` is still a dict, so every counting site in
  the reader works untouched; a site that knows its context calls `note()` or,
  where the counter was already raised, `record()`. `tree.changes` is the log.
- `tree.py`, `recover.py`, `structure.py`: the drops (running heads, logos,
  copyright lines, an editor's block, junk), the rewrites (symbol fonts,
  a block that said its paragraph twice, a fused heading cut in two), the joins
  (across a page break, one the judge settled, a displaced head, a caption's
  tail), what the text layer put back (missed prose, stripped furniture,
  rebuilt blocks, attached runs, table notes and grids) and the shape decisions
  (a heading built, a lane read from the paragraphs, a name from the catalogue)
  all say where they acted. 99% of what the reader counts it can now place.
- `jats_prep.py`, `mathml.py`: `prepare_jats(raw, log=records)` reports every
  rewrite it makes to a publisher's XML before Docling reads it — bracketed
  citation markers, rendered reference entries, flattened titles, folded
  section labels, dropped processing instructions, linearised formulas. The
  bytes handed to Docling are unchanged whether or not a log is kept.
- `parser/litrag_parser/review.py` (new): one report per paper — every page
  drawn at the size it was printed, every node boxed in its lane's colour,
  every modification numbered where it happened and written out beside the
  page with its before and after — plus the type decision and the evidence
  behind it, the sections with their canonical names, the citations linked to
  their entries, and an index over any set of libraries. Nothing is written to
  a library: the trees are rebuilt in memory as `rebuild` reads them.
- `parser/litrag_parser/summary.py` (new): the corpus on one page — what was
  read, from which publishers, in which formats, what the reader did to it,
  and every measurement that has been run, linking into the per-paper reports.
  Each run renders as a table rather than as JSON: the two formats side by
  side with the counts behind every rate, the PDF scored against its own XML,
  each type source ranked by the accuracy it earns where it answers, the
  heading catalogue at each threshold, and the trust score against the error
  it exists to predict. A measurement that was never run leaves no section
  behind, so no number on the page is one the page invented.

Measured on the whole corpus, then fixed where the measurement pointed.

- 797 papers in seven libraries (284 PDF, 513 publisher JATS), twelve runs,
  none failed. The reader finds the right title on 0.989 of PDFs and 1.000 of
  XML, reads 0.968/0.992 with no audit error, and finds a methods section or
  correctly calls the paper a review 0.944/0.951 of the time.
- 199 of those papers exist in both formats, so the XML can score the PDF
  reading. Word recall 0.990 and paragraphs missing 0.001 — the text is not
  lost. Words filed under the right heading: **0.840 → 0.899**, after the
  depth fix below. Papers below 0.5 faithful: 22 → 10.
- The type decision: **0.786 → 0.927** on 248 papers whose stated type was
  hidden, and 0.738 → 0.820 on 61 papers in libraries never inspected while
  tuning. The `other` bucket went from 41 papers named and 1 right to 2 and 0.
- The trust score: AUC **0.788 → 0.835**, papers seriously mismatched 47 → 26,
  and papers scoring ≥0.9 while seriously mismatched 11 of 129 → 5 of 138.
  Still weaker at the top than at the bottom.
- The heading catalogue, scored with each library's own headings withheld:
  lane precision 1.000 / recall 0.993, canonical name 0.994 / 0.904.
- 15,872 of 18,120 counted modifications (88%) are placed on a page. The
  shortfall is one pass that counts reference entries one by one and records
  the list it built as a single change.

- In-text citations against the XML's own markup: **0.734 -> 0.851** pooled
  (0.808 -> 0.940, 0.672 -> 0.810, 0.732 -> 0.840), and reference-list length
  0.851/0.797/0.875 -> **0.970/0.939/0.945**, after seven printed citation
  styles that were being missed, six kinds of false positive that were not,
  and the reference list itself being found where the page scatters it.
  Papers with a broken list 33 -> 14; citations linked across the PDFs
  20,559 -> 22,250.

Named, not fixed: **34 of 199 papers have a truncated or absent reference
lane, costing 2,456 of the 3,589 missing citation links** — two of them hold
zero reference nodes against the XML's 192 and 294, so their markers are read
perfectly and link nowhere. That is the biggest remaining lever. The type
rules are right for biomedical literature and read an ML preprint as a review,
which this corpus cannot see; `other` recall is 0.0 and letter recall 0.5.

Meaning everywhere a list used to be, and a scorer for the one question
meaning cannot answer.

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
