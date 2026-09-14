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
