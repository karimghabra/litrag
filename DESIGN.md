# litrag — the design

Two revisions, one direction. **Revision 2 (2026-09-11)** is what is being
built now: papers read into trees by Docling, watched from a desktop
window, stored as rows. **Revision 1 (2026-09-03)** is the retrieval loop
that will feed on those rows — Europe PMC, chunks, embeddings, the graph
walk — as it was built in the `lit` CLI; its store is separate for now and
its decisions still hold where they are not overruled below.

---

# Revision 2 — the tree, the app, two languages

## R2.1 What changed, and why

Karim, 2026-09-11, on the revision-1 code: *"I am not at all attached to
the current implementation of litrag."* And on the shape he wants: *"an app
with a GUI that lets me actually see how papers are being ingested, how
trees are built"*, in Electron, with Docling doing the parsing, and *"why
can't we use more than one language?"*

So three decisions of revision 1 are overruled:

- **One language** is gone. Python owns parsing and the store; TypeScript
  owns the window. They meet on one wire, JSON lines over stdio, and nowhere
  else. The installer promise of "no Python" goes with it; `uv` makes the
  Python side one command to set up.
- **pdf.js text plus heading regexes** is gone as the reader of PDFs.
  Docling's layout model reads the page as a page — columns, tables,
  captions, running heads — and every element it returns carries a page and
  a box. JATS goes through the same converter, so both formats land in one
  document shape.
- **Sections and 250-word chunks** are gone as the unit of structure. The
  unit is a **node** in a tree: document › section › subsection › paragraph
  | table | figure › caption. A chunk for retrieval, when revision 1's loop
  is wired to this store, will be a paragraph node with its ancestry, not a
  window cut through flat text.

What stays: local, rows first, idempotent filing, every answer JSON, the
assistant reads rows and never fabricates, and the retrieval ideas of
revision 1 — hybrid ranking with a graph walk, the miner, the model stage —
which now have a better substrate to run on.

## R2.2 The tree

```
document                                      the paper; its title
├── section (level 1, role=methods)           "2. Materials and Methods"
│   ├── section (level 2, role=methods)       "2.4. Quantification of Crosslinking Degree…"
│   │   ├── paragraph                         text · page 4 · box
│   │   ├── formula
│   │   └── table 5×3 (cells)  ─ caption
│   └── …
├── section (level 1, role=results)           "3. Experimental Results"
└── section (level 1, role=references)        each reference a node, folded by default
```

Every node: `node_id` (the paper key, then `#section-2#paragraph-4`),
`parent`, `ordinal`, `depth`, `type`, Docling's `label` verbatim, `level`
for sections, `role`, `heading`, `ancestry` (the headings above it, top
down), `text`, `page`, `bbox` in PDF points with the origin top-left,
`self_ref` back into the raw Docling document, and for a table its cells.
The raw Docling document is written to `parsed/<key>.docling.json` before
any row is, and never edited: `rebuild` derives the rows again from it when
the tree builder improves, without running the models.

**Role** is the role of the *top-level* section a node sits under,
inherited all the way down, so a paragraph under "2.4" is methods whatever
"2.4" is called. The vocabulary is finite (`facets.py`): abstract,
introduction, methods, results, results-discussion (a combined section is
its own lane, not both), discussion (conclusions included), references,
back (funding, contributions, availability, supplements), and `other` for
everything that matches nothing. A wrong lane is invisible at query time; a
missing one is a gap you can see. `has_methods` is stored per paper so a
review is told apart from a parse failure by looking, not guessing.

## R2.3 What the layout model gets wrong, and what the tree builder does

Found on the first two PDFs, kept as fixtures and tests:

| Docling gave | The builder does |
|---|---|
| Every heading at level 1 | Numbering decides depth when present ("2.4." is depth 2); a heading that names a lane is level 1; any other heading beneath an open top-level section is its child |
| The title labelled a section header | The first header before the body, in a document with no title item, is the title |
| "Experimental Results 3.1. Quantification…" as one list item — the heading merged into the subheading below it | Split into two headers when the first half names a lane; the whole results section had been filed under methods |
| "3.2 …" arriving while "2. Methods" is open and no "3." seen | A section titled "3. (heading not detected)", role `other`, so the gap is visible rather than the results being methods |
| "4. Discussion" twice across a page break | The second continues the first |
| "3.4. Simulation Results 3.4. Simulation Results" | Halved |
| Authors, affiliations, dates and copyright as loose paragraphs before the first heading | Filed under a "Front matter" section, role `other`, so nothing said there is lost and nothing is mistaken for the body |
| No levels even with `heading_hierarchy_options` on | Ignored; the rules above are the hierarchy |

Docling's own strengths hold: multi-column reading order, tables as cells
with captions attached, figures with captions, running heads labelled and
dropped, page and box on everything. JATS from Europe PMC lacks the DOCTYPE
Docling's detector keys on; the worker adds one and the JATS backend does
the rest in milliseconds, with no page geometry because XML has none.

## R2.4 The worker

`parser/litrag_parser/worker.py`. One process per window, JSON lines over
stdio. Reads are answered at once on the main thread; `ingest`, `reparse`
and `rebuild` queue onto one ingest thread, so the window browses trees
while Docling is busy. Docling is imported on first use — it takes seconds
and pulls torch — so `hello` and `papers` are instant.

Ingest, per file: hash it; sniff its DOI and PMCID (from the first pages of
a PDF through pdfium, from `article-id` in JATS); file it once — DOI, then
PMID, then hash, a hash-only stub giving way when its DOI turns up; copy it
to `papers/<key>.<ext>`; then stages `opening` (page count), `models`
(first time only), `layout` (Docling, with a heartbeat every 1.5 s), `tree`,
`saved` — each also a row in `events`, so the history survives the window.
Docling's own log lines are forwarded as `log` events. A paper already read
is kept when its DOI arrives again as another file; `reread` replaces it.

The store (`store.py`): `papers`, `pages`, `nodes` with an FTS5 index over
node text, `events`. Reads: `tree` (nested), `node`, `siblings`,
`section(key, role)`, `sql` (one SELECT, read-only) — the traversal verbs
of the query path, as rows.

## R2.5 The window

`app/`: Electron 38, TypeScript, esbuild, no framework. The main process
spawns the worker (`uv run --project parser litrag-parser`, or
`LITRAG_PARSER`) and relays every event to the renderer over one IPC
channel; the renderer asks for reads over another and gets the worker's
answer back. Three panes and a log: papers with their live stage and lane
bar; the tree with lane colours, chips to dim all but one lane, references
folded; the page rendered by pdf.js with the selected node's box drawn and
every other node on the page faint, and the node's ancestry, role, text or
cells beneath. Drop PDFs anywhere. The window holds no state the worker
does not: close it, reopen it, everything is rows.

## R2.6 Next, in order

1. **Spot-check thirty papers** in the window — the point of building it
   first. Every layout failure becomes a fixture and a rule, as the first
   two did.
2. **Europe PMC in the window**: search, stage, fetch JATS — revision 1's
   source, driven from the app.
3. **Wire revision 1's loop to the tree**: paragraph nodes as chunks with
   ancestry prefixed, embedded once and partitioned by role; the miner and
   the model stage over `nodes`; the graph walk over the same rows.
4. **Node summaries** from the model stage, PageIndex-style, so an
   assistant navigates a chosen paper by reading rows, not by calling a
   model per hop.
5. **Sparse extraction with evidence and node id**, the canonical-key map
   as a table; **paper profiles** for the cross-facet question; **authors
   with ORCID and citations** for lineage.

The bench-question set stays first among equals: none of 3–5 is judged
without it.

## R3 What the reader will know next: the type of a paper, a vector per node, and the edges between a finding and its method

Written 2026-09-13, after the oracle of meaning landed (R2, `meaning.py`) and
after two things Karim said in one afternoon: a query that tests a hypothesis
must find the results and then jump to the methods that produced them; and to
read a paper well the reader should first know what kind of paper it is. This
is the plan for both, in the order each step earns the next, with the
measurement each must pass before it is allowed to decide anything. Nothing
here is built yet.

**What the vocabulary-off experiment settled first.** With
`LITRAG_VOCABULARY=off` the embedder alone names every heading (examples for
abstract and references added, wider margins on those two). Heading by
heading it agrees with the vocabulary on every lane retrieval searches:
abstract, introduction, results, references 100 %; discussion 99.7 %; methods
98.1 %; back matter 90 %, and what it misses there falls to `other`, not to a
wrong lane. At the paper level, over the three corpora, it loses the methods lane on
ten papers in 765 ("Methods of literature search", "Extraction Methods", and
the "2. Experimental" family before it was given as an example) and names
thirteen closing sections the vocabulary had missed ("Future perspectives",
"Challenges and perspectives"). One paper lost
its citations, not to a lane but to a linker fragility the run exposed (a
prose subsection nested under References; BACKLOG). So: the approach
generalises; the vocabulary stays as the free, certain first pass; and the
examples, not new patterns, are how the reader is told what a lane means.

### R3.1 The type of a paper

A column, `papers.type`, with `type_source` beside it. Types: `research`,
`review`, `letter`, `editorial` (opinion, commentary, perspective),
`case-report`, `protocol` (a methods paper), `data` (a data descriptor),
`correction`, and `other`. Decided by a cascade, each step only where the one
before is silent, every verdict a row with its source:

1. **The file says.** JATS `article-type` on the root element. Exact, free,
   present on every XML paper.
2. **The record says.** Europe PMC's `pubTypeList`, in the identity call the
   worker already makes for a DOI. Stored with the paper when it comes back.
3. **The page says.** The first-page label the front kind already captures as
   a `notice` ("ORIGINAL RESEARCH ARTICLE", "REVIEW", "Letter to the
   Editor"), read by a `type-label` kind: examples per type, threshold,
   margin, verdict stored.
4. **The paper's shape says.** A `profile` kind over a short text: the
   title, the first sentences of the abstract, the top-level headings in
   order. Examples per type are profiles, not phrases.
5. **A model reads it**, only as the judge is asked today (opt-in), verdict
   stored.
6. `other`.

Measured before step 4 writes anything: steps 1 and 2 label most of the 765
papers for free; the profile kind runs on them with the label hidden and the
confusion table is written to NOTES.md. Gate: precision ≥ 0.95 per type on
the labelled papers. The harness gains a per-type table (research papers
with methods found, reviews with topical sections, letters with no
abstract) and the audit's `no-methods` becomes an error for `research` and
nothing for `review`.

Built 2026-09-14 (`paper_type.py`), and rebuilt the same day once the
measurement showed the defaults masquerading as labels: every source's
vocabulary now goes through one table that says which labels name a kind
and which are a publisher's default bucket; the record's MeSH publication
types, the file's article-type, the subject line, the title and the printed
label are trusted in that order; a subtype is kept where a label states one;
a default never decides alone — the shape must agree, and the shape decides
on its own only for the types its rules measured precise (reviews 69 of 69
named, case reports 9 of 9, data descriptors 5 of 5; research 84 of 97, so
research by shape needs a default label beside it); a research paper without
a results heading is read by its order (Nature's methods after the
discussion) or by the measurements its body reports, and a review with a
methodology section is unread rather than misread; every disagreement is a
note.
Step 5 is not built; the profile kind is off unless `LITRAG_TYPE_PROFILE=on`.
NOTES.md has the tables. The record's one call brings the authors, journal and year
as well (`record.py`; `papers.authors`, `journal`, `year`), and a JATS
file's own contributor group overrides it. The measurement is in NOTES.md.

### R3.2 Headings read with the type in mind

*Status, 2026-09-14:* the first half is built another way — not one heading
kind per type but one catalogue of canonical section names for every type
(`headings.py`, `nodes.canonical`), learned from the XML libraries' own
sections and measured library-out (NOTES.md); "Case presentation" is a
results section by the catalogue's word, and a review's topical headings
keep no name, which is honest. What remains of R3.2 is the type
conditioning itself: the audit's expectations per type beyond `no-methods`,
and a per-type answer to what a topical heading is.


One heading kind per type, the same oracle: `heading@review` has a `topical`
lane so "5. Cellulose-based hydrogels for tissue engineering" is a right
answer instead of `other`; `heading@case-report` knows "Case presentation" is
its results; `heading@data` knows "Experimental design, materials and
methods" is methods; `heading@research` is today's kind. The vocabulary still
runs first. Content lanes (`structure.py`) run only for `research`, `letter`
and `case-report` and never for `review`, which is where every false lane of
the block kind came from. Built headings follow the type's expected run: a
letter is one body, a case report is presentation then discussion, a research
article is IMRaD, as a prior for the Viterbi pass rather than an assumption.

Measured: the vocabulary-off agreement per type, and the corpus gate's
`lane_lost` bucket, which now watches every named section.

### R3.3 A vector per node

A `vectors` table: node id, model, the vector. Filled at ingest and on
rebuild with the same local embedder (`search_document:` prefix), one batch
per paper. About 150 nodes a paper, so the six libraries take one pass of
half an hour once, and a rebuild replays the rows rather than embedding
again. Everything after this reads vectors from the store and never embeds at
query time.

### R3.4 Edges: a finding and the method that produced it

An `edges` table: from node, to node, kind, evidence, detail, score. Every
edge is a row a person can read and delete; a rebuild replays them. The first
kinds:

- `measured_by`: a finding (a results paragraph, tied to the figure or table
  it cites) to the method subsection or caption that produced it. Candidates
  are the paper's own methods subsections (the level-two headings under
  methods, or its paragraphs when there are none) and its captions, a closed
  list of five to fifteen. Three kinds of evidence, in order of trust,
  recorded on the edge: an explicit pointer ("see Section 2.3", a caption
  naming the assay, a JATS cross-reference); shared terms (the measurement,
  the instrument, the assay, the cell line, in both and in no other
  candidate); similarity from the stored vectors, taken only when the nearest
  clearly beats the next. A finding no evidence can place stays unlinked and
  says so.
- `cites_figure`: a paragraph to the figure or table it names.
- `cites`: a paragraph to a reference entry (the `citations` table, read as
  edges), and `cited_by` across papers when the entry names a paper the
  library holds.

Two cases designed for: methods-last formats, where the lane holds and "the
nearest preceding section" would not; and findings whose method lives only
in a caption or the supplement, where "linked to the caption" is the right
answer.

Measured before similarity may create an edge on its own: the papers with
explicit pointers and cross-references are the ground truth; precision per
evidence kind is written to NOTES.md; similarity-only edges are allowed when
they agree with the pointers on those papers at ≥ 0.9.

### R3.5 The query path

A `query` op on the worker, and the `lit` CLI pointed at the tree store:
retrieve nodes (FTS5 words and stored vectors, hybrid as the CLI does over
chunks today), keep or boost by lane and type, follow edges, and answer as
JSON: the hits, each with its lane, its paper's type, the methods it was
`measured_by`, and what it cites. "Results of primary research on X, with
their methods" is one query; "what was measured with this assay" is the same
edges walked the other way. Same rows, same answer, on any machine.

### R3.6 How far a reading can be trusted

*Built 2026-09-17.* Karim: "we need to design some kind of confidence metric
that we can use to screen well-matched papers … especially easily if we have
both the XML and the PDF of a particular paper." The XML is the nearest thing
to the truth about a PDF's tree, so the design has two halves. `pairs.py`
reads a paper from both formats and measures the distance: text located by
four-word shingles of letters, presence counted in words (a word hyphenated
at a line's end cost four shingles and three to eight points of recall, all
noise), *faithful* = present and in a paragraph of the same lane, precision
the other way round, paragraphs intact, split, merged or missing, headings,
references, citations, captions. `confidence.py` then has to predict that
distance from the PDF's reading alone. The first measurement decided the
shape of the second half: on 168 pairs none of the reader's existing
self-measurements correlated with agreement (page coverage −0.02, dropped
lines −0.12, glyph residue −0.06, audit errors −0.04), because what goes
wrong is not what they watch — the text is there (recall 0.99) and is filed
under the wrong lane. So the signals are aimed at the failures the pairs
named, each a plain measurement with a limit read off the pairs, combined as
graded penalties with the reasons kept in words; no model, nothing learned
that cannot be read in `CHECKS`. Measured on 199 pairs (three corpora; the
limits mostly read from one, the other two agreeing): the score ranks a
well-matched paper above another 0.79 of the time and separates the
seriously mismatched at 0.83; at 0.9 or more, 105 of 129 are well matched and
11 seriously off; under 0.5, 25 of 30 are seriously off. What it does not
see is a partial disagreement — a fifth of the text under a neighbouring
lane — and that is the next iteration's work (NOTES.md).

The comparison is also the sharpest test the reader has had: on its first
day it found a block that carries its paragraph twice (repaired:
`unrepeat`), a lane's heading fused with the subheading under it (repaired:
`split_fused_heading`), and that the reader had been ignoring the depth an
XML states — 453 headings in 145 of 513 XML papers nested under whichever
section stood open, whole review bodies read as `introduction` (repaired:
`infer_level(stated=True)`). The PDF side of that last defect — a review's
unnumbered headings, whose depth only the typography says — is open
(BACKLOG.md).

### Order, and what each step is gated on

| step | builds on | gate before it decides |
|---|---|---|
| R3.1 type | the identity call, the front kind | precision ≥ 0.95 per type on labelled papers |
| R3.2 typed headings | R3.1 | vocabulary-off agreement per type; no `lane_lost` on the corpora |
| R3.3 vectors | the store | rebuild replays; the six libraries embedded once |
| R3.4 edges | R3.2, R3.3 | precision per evidence kind on cross-referenced papers |
| R3.5 query | R3.4 | the e2e suite: a results hit shows its method |

Cross-cutting, as in R2: every verdict a row; the harness extended with
each step's table; two fresh-context reviews of the code; the corpus gate on
all three corpora before anything ships; docs and NOTES.md with the numbers.

---

# Revision 1 — the retrieval loop (2026-09-03)

The knowledge base beside the notebook. Protracker records what Karim does;
the literature loop holds what the field already knows about it, one library
per project, so that the assistant working a project is informed by the
papers as well as by the record. His frame, from the backlog: *the system that
combines the structured data with the intelligence* — the tracker is the
structured half of his own work; this is the structured half of everyone
else's.

This document records the decisions and the plan. It began life inside the
Protracker repository as `LITERATURE.md` and moved here with the code on
2026-09-03; Protracker's backlog holds the original ask and its design
stakes, and the vault's **Literature Loop** project holds the steps.

## 1. Decisions

**1.1 A separate tool, in its own repository.** The tracker's invariants
stand untouched: the vault stays plain text, canonical, small, and holds no
papers. litrag has its own store, its own CLI (`lit`) and its own tests, and
touches Protracker in one place — `lit init` asks `pt` what a project ref
is, when `pt` is on the PATH. Nothing in the tracker knows it exists.

**1.2 It is a local service, in Node, with no Python.** The corpus is
copyrighted PDFs plus Karim's research direction: it never leaves the
machine. Everything runs in the same Node the app ships — SQLite through
`node:sqlite`, PDF text through `pdfjs-dist`, embeddings through
`@huggingface/transformers` (ONNX on the CPU; the model is fetched once and
cached beside the libraries). The installer's promise — no Python, no Rust,
no compilers — holds.

**1.2a The GPU does the reading.** Karim's machine has an RTX 5080, and
Ollama on it is the one thing beyond Node the loop asks for. The tool talks
to Ollama on `127.0.0.1` for two stages: embeddings, when a larger model
than the CPU one is worth having, and the *model stage* — reading each
section of each paper into claims, materials, methods and named parameters,
as JSON against a schema so the answer is rows or nothing. A library says
which backends it uses (`lit config <lib> --extract ollama --embed
ollama`); the default is the CPU embedder and the miner alone, so a machine
without a GPU still gets a working library, and the GPU adds the rows on
top.

**1.3 The store is SQLite: rows first, vectors beside them.** "What
crosslinker concentrations has anyone used on electrocompacted collagen" is a
query with columns in it. So the store is tables — papers, sections, chunks,
parameters, references — with an FTS5 index for words and float vectors for
meaning. `lit sql` runs read-only SQL against it; `lit query` runs the hybrid
retrieval. A vector database would be a second store with nothing the rows
cannot hold at this size (thousands of chunks, not millions).

**1.4 One library per project, keyed to the vault.** A library carries the
project's vault id (`n156`) in its manifest, so a rename does not orphan it,
and the Research tab and the assistant find a project's papers by the same
ref they use for its tasks. A library can also stand alone, for a set of
tasks that is not a project.

**1.5 The shared spine is an include, not a copy.** The method itself — ELAC
— belongs to every project. A library names the libraries it draws on
(`includes: ["elac-methodology"]`) and queries run across the union, each
hit labelled with the library it came from. One paper lives in one place.

**1.6 Ingest is a verb.** "Dynamic" means the library grows whenever a paper
lands — a search run again, a DOI added, a PDF dropped in the inbox — and the
ingest is idempotent: a paper is keyed by DOI, then PMID, then the hash of
its file, so running it twice changes nothing.

**1.7 Sources, in order of trust.** Europe PMC first: it searches MEDLINE and
PMC together, returns open-access full text as JATS XML (sections already
delimited — no PDF parsing), and has reference lists for snowballing.
Crossref for a DOI that Europe PMC does not know. Unpaywall for open-access
PDF locations, once Karim has given an email for its terms. Paywalled papers
are Karim's to fetch through Scripps: the tool lists what it could not get,
and the inbox takes what he brings back. Nothing scrapes a publisher.

**1.8 Paper text stays on the machine; the assistant reads rows.** The
extraction into rows is the local model's job (1.2a), so no paper text goes
to any API. The assistant — Claude, in a session — reads what `lit query`
and `lit sql` return: chunks with citations, and rows. A Claude-backed
extractor remains possible as a third backend for a machine with no GPU,
and stays off unless Karim turns it on.

**1.9 The app comes after the pipeline, and the pipeline is shaped for it.**
The Research tab is a later phase (§7). What it will need is already here:
libraries keyed to vault project ids, every verb answering in JSON, the
store readable by SELECT, and stages that report progress line by line —
which is what `lit serve` will stream to a tab.

**1.8 Resemblance is one question, asked one way.** Every place the reader
used to consult a list of phrases — the lane a heading names, what a line
of front matter is, whether a text under a figure is its legend, whether a
line is a reference entry, which open paragraph a tail belongs to, what a
section's paragraphs are — is the same question, "which of these does this
resemble", and `meaning.py` answers all of them the same way: a local
embedder, a few examples (or centroids) per answer, a threshold and a
margin, `other` when unsure, and every answer a row. The rules run first
where they exist and are cheap; a verdict adds where the rules were silent
and never overrides one that fired, with one documented exception (a label
the run-in list does not know vetoes a join the page would make, because a
split is visible and a merge is not). What a verdict may do is bounded by
what it costs to be wrong: it may name, adopt, choose among candidates, and
build a heading it labels as its own; it may not drop text or file prose
under another section. Whether one block continues another is not a question
of resemblance and goes to a likelihood scorer (`boundary.py`), which is off
until it passes on the real leftover pairs and not only the synthetic ones.
Every threshold is set by a measurement on the corpora, library-out where
the examples came from them, and the measurement is written down before the
verdict is allowed to decide anything.

## 2. Where things live

```
$PROTRACKER_LIBRARY  (default ~/.protracker/library — never inside the vault)
├── models/                      the embedding model, cached once
├── looped-ligament/
│   ├── library.json             { id, name, projectId, includes, queries, extract }
│   ├── lit.sqlite               the store
│   ├── papers/                  what the tool fetched: <key>.xml, <key>.pdf
│   └── inbox/                   what Karim drops in; ingest moves it to papers/
└── elac-methodology/ …
```

The library root is gitignored and outside the vault by default, so neither
the app's vault watcher nor the archive sync ever sees a PDF.

## 3. The schema

```
papers      key, doi, pmid, pmcid, title, year, journal, authors, abstract,
            source (europepmc|crossref|inbox), status
            (candidate → fetched → ingested | needs-pdf), file, sha256,
            cited_by_count, added_at, ingested_at
sections    paper, ordinal, heading, kind (abstract|intro|methods|results|
            discussion|other), text
chunks      id, paper, section, ordinal, page?, text, tokens
chunks_fts  FTS5 over chunks.text (bm25 for lexical recall)
vectors     chunk, model, dims, vec (float32 blob)
parameters  paper, section, chunk, value, unit, kind (concentration|time|
            temperature|voltage|current|ph|stress|length|…), sentence,
            entity? ("EDC concentration" — the model names it, the miner
            cannot), source (miner | ollama:<model>)
claims      paper, section, text, kind (finding|method|limitation|
            hypothesis|background), source          — the model stage
materials   paper, section, name, role, amount?, source
methods     paper, section, name, description, source
refs        paper, cited_doi?, cited_pmid?, cited_title, matched_paper?
entities    name, norm, kind (chemicals | gene-proteins | organisms |
            experimental-methods | material | method | …)
mentions    entity, paper, chunk?, count, source (europepmc | model)
queries     library's saved searches: source, query, last_run, hits
```

`papers.extracted_with` stamps the model that read a paper, so the model
stage resumes where it stopped and re-reads when the model changes.

`status` is the pipeline's state machine and `lit status` is its read.
`parameters` is what makes "what concentrations has anyone used" a `SELECT`.

## 4. The verbs

```
lit libraries                          every library, with its project and counts
lit init <project-ref|name> [--query Q] [--include LIB]
lit search <lib> "<query>" [--since YEAR] [--limit N]
                                       stage candidates from Europe PMC (no fetch)
lit add <lib> <doi|pmid|pmcid|file.pdf>  one paper, by hand
lit fetch <lib>                        full text for every candidate it can get;
                                       the rest become needs-pdf
lit ingest <lib>                       read, chunk, mine parameters, embed —
                                       for fetched papers and the inbox; idempotent
lit extract <lib> [--limit N]          the model stage through Ollama, resumable
lit annotate <lib>                     entity nodes from Europe PMC's text-mined terms
lit graph <lib> | lit entities <lib>   what the graph is made of
lit config <lib> --extract ollama --embed ollama [--ollama-chat M] [--ollama-embed M]
lit doctor [<lib>]                     root, model cache, Ollama and its models
lit status <lib>                       counts by status; what needs a PDF
lit wanted <lib> [--csv FILE]          the PDFs to collect, most-cited first, with doi.org links
lit query <lib> "<question>" [--limit N]
                                       hybrid retrieval: chunks with citations
lit sql <lib> "<select …>"             read-only SQL
lit snowball <lib> <paper>             stage the references of a paper
lit serve [--port 7411]                the same verbs over HTTP on 127.0.0.1
```

Every verb takes `--json`; the human form is for people. The pipeline is
`search → fetch → ingest → query`; `lit refresh <lib>` runs the saved queries,
fetches and ingests in one go, for the "new paper landed" case.

## 5. The pipeline, stage by stage

1. **Search** — Europe PMC `search` with the library's query; each hit becomes
   a `candidate` row (title, year, DOI, abstract, open-access flag). Dedupe by
   DOI, then PMID. Nothing is downloaded.
2. **Fetch** — for each candidate: open access → JATS XML from Europe PMC;
   else an OA PDF via Unpaywall if configured; else `needs-pdf`. A PDF in the
   inbox is matched to its candidate by DOI found in the text, or added as a
   new paper.
3. **Extract** — JATS → sections directly. PDF → pages of text → sections by
   heading heuristics (Abstract / Introduction / Methods / Results /
   Discussion / References), references dropped from the body.
4. **Chunk** — paragraphs grouped to ~250 words with a one-sentence overlap,
   each chunk remembering its section and page.
5. **Mine** — the local parameter miner: every number-with-unit in a sentence
   becomes a `parameters` row with the sentence around it. No model.
6. **Embed** — `bge-small-en-v1.5` (384 dims, quantised, CPU) over every chunk
   not yet embedded for this model. Vectors are float32 blobs.
7. **The model stage** (`lit extract`, GPU) — each section, in pieces of at
   most ~1200 words cut at sentences, to the chat model with the rows
   schema as Ollama's `format`, temperature 0, thinking off. Claims,
   materials, methods and named parameters land in their tables with the
   model's name as `source`; the miner's rows stay beside them.

## 6. Retrieval

`lit query` runs the question three ways and fuses the rankings by
reciprocal rank: through FTS5 (bm25 over the words), through the embedder
(cosine over the library's vectors, brute force — a few thousand 384-float
vectors take milliseconds; a question is embedded with the instruction BGE
v1.5 was trained to expect), and through the graph (§6a). It returns the
top chunks with paper, section, page and a citation string, and `--trace`
says which rankings held each and where. The assistant answers from those
and cites them; the tool never generates prose. Included libraries are
searched the same way and labelled.

### 6a. The graph layer

After HippoRAG 2 (Gutiérrez et al., ICML 2025), with the store's own rows
as the graph. Nodes are papers, chunks and entities; edges are a chunk
naming an entity (weighted by how often), a chunk belonging to a paper,
and a paper citing another the library holds. The entities come from two
places, neither a cost: **Europe PMC's text-mined terms** for every
open-access paper (`lit annotate` — chemicals, proteins, organisms,
experimental methods, with the section each sits in), and the **model
stage's materials and methods** when it has run. An entity named in more
than 60% of the papers is left out of the graph — "collagen" in a collagen
library links everything to everything and ranks nothing.

A question seeds a personalized PageRank at the entities it names (weight
1 each) and at its ten nearest passages (weight 0.05 each, HippoRAG 2's
passage weight, so entities lead and passages follow); damping 0.5, thirty
rounds. Chunks rank by where the walk settles. What this adds over words
and meaning: a passage that shares the question's *things* — genipin,
ethanol, a cell line — without sharing its words, and passages in papers
the best hits cite. What it cannot do: reach a passage no entity or
citation touches; those are the two other rankings' job, which is why all
three are fused rather than one chosen.

The graph is built from the tables at query time, in memory. There is no
second store, nothing to rebuild, and `lit graph` shows what it is made of.

## 6b. Compared with the lab's own pipeline

Dr. D'Lima's scripts (September 2026) run the same loop in Python: PubMed
E-utilities → PMCIDs and abstracts to CSV → PMC full-text XML per PMCID →
JATS to JSON → a FAISS store built with `bge-large-en-v1.5` on the CPU →
dense retrieval into LM Studio (Mistral Small). Stage for stage:

| | the lab's scripts | the literature loop |
|---|---|---|
| Source | PubMed via Entrez, with MeSH terms and publication types | Europe PMC (search, full text, terms) — the same PMC XML, one API |
| Identity | PMCID | DOI, then PMID, then file hash; a paper is filed once |
| Chunks | 450 tokens target, 800 max, 80 overlap; conclusions kept whole; methods grouped by adjacent paragraphs; figure captions their own chunk type | ~250 words, one-sentence overlap; captions inside their section |
| Embeddings | bge-large (1024-d), FAISS flat inner product, BGE query instruction | bge-small (384-d) or Ollama, SQLite blobs, BGE query instruction — since this comparison |
| Retrieval | dense only; top 14 → 5; ≥0.20 similarity; ≤3 chunks per section; 4,200-token budget | bm25 + dense + graph, reciprocal rank fusion |
| Updates | append new chunks by content hash; a changed chunk forces a rebuild (FAISS cannot replace a vector) | rows and vectors per paper are replaced in one transaction; `--reread` re-reads everything |
| Rows | none — chunks and metadata | parameters (miner), claims/materials/methods (model), entities, refs; SQL |
| Answering | Mistral Small in LM Studio, `[S1]` labels, saved as Markdown | the assistant, from cited chunks; no prose generated by the tool |
| Paywalled papers | not handled | listed as needs-pdf; inbox |

Borrowed from his: the BGE query instruction (a real gap — bge v1.5 wants
it and ours did not send it), publication types for review-versus-article
(his open TODO, `pub_type` now), and the case for section-aware chunk
sizes — his 450-token target with whole conclusions is worth an A/B
against ours on the bench questions. Kept from ours: hybrid retrieval, the
SQL store with provenance, idempotent updates, and no Python.

## 7. The Research tab

Sidebar entry **Research**. It opens on a project picker (the vault's
projects, with a paper count beside those that have a library). A project
with a library shows its papers — title, year, journal, status, with a search
box that runs `lit query` and shows hits under their papers — and a way to
add: a search against Europe PMC with tick-to-stage, a DOI box, and the
inbox folder. A project without one shows one thing: **Create a library for
<project>**, with a seed query prefilled from the project's name and
description.

The renderer talks to `lit serve` over HTTP on localhost; Electron's main
process starts the service on demand and stops it with the app. The e2e
suite runs in a browser tab with no service, so the tab renders against a
fake transport there, as the sync surfaces already do.

This is the second phase. The first is the pipeline behind it, because the
tab is only worth building once a library answers a real question.

## 8. The assistant

**Decided, 2026-09-03:** the assistant lives on Karim's machine as a local
session and drives `lit` there, the way it drives `pt`. The corpus, the
GPU and the questions are in one place; nothing is exported for the cloud.
`AGENT.md` §7 is the assistant's manual for it and the `/literature` skill
is the manner of answering: passages in, citations out, gaps named as
gaps. `/harvest` will gain a step — an intention that names a paper or a
method becomes a `lit search` — when the harvest next runs against a
library.

### Setting up on the 5080

```
ollama pull qwen3:14b            the reader (~9 GB at 4-bit)
ollama pull nomic-embed-text     embeddings on the GPU (optional; bge-small on the CPU is the default)
npm run lit -- doctor            root, model cache, Ollama and its models
npm run lit -- config looped-ligament --extract ollama [--embed ollama]
npm run lit -- extract looped-ligament     the model stage; resumable, about a minute a paper
npm run lit -- status looped-ligament      claims · materials · methods counted
```

Then the same bench questions with `--trace`: the entities from the model
stage should show up as seeds, and the graph ranking should start earning
its place.

## 9. Phases

1. **This session — the general workflow.** `src/` with the schema,
   Europe PMC source, JATS and PDF extraction, chunking, the parameter miner,
   local embeddings, hybrid query, `lit sql`, and the CLI; unit tests with
   fixtures and a fake embedder; a pilot library for Looped Ligament from real
   open-access papers; one bench question answered with citations.
2. **Prove and refine.** Karim brings the paywalled PDFs through the inbox;
   the schema and the miner get tuned on what the questions actually need.
3. **The Research tab** and `lit serve`, with the fake transport for tests.
4. **The assistant's verbs**: `/literature`, the harvest step, the cloud
   question settled.
5. **A Claude extractor**, only if a machine without the GPU ever needs it.

## 10. Open, for Karim

- **Unpaywall email.** Their terms want a contact email per request; with it
  the tool finds open-access PDFs for papers Europe PMC has only as abstracts.
- **Which local models.** Defaults are `qwen3:14b` for reading and
  `nomic-embed-text` for embeddings, both comfortable in 16 GB. Worth a
  bake-off on ten papers: `gemma3:12b` and `qwen3:32b` (q4) are the other
  candidates for reading; `bge-m3` or `mxbai-embed-large` for embeddings.
- **The pilot corpus.** Looped Ligament, seeded with "electrochemically
  aligned collagen" and "electrocompaction collagen" — the first thing to
  confirm at the bench is whether the query set is the right one.
