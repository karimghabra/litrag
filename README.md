# litrag

One library of papers per project, read into trees you can see.

Drop a PDF on the window and watch it being read: filed by its DOI, laid
out by [Docling](https://github.com/docling-project/docling) into headings,
paragraphs, tables and figures, built into a tree whose every node knows
its section, its lane — methods, results, discussion — and the page and box
it came from. Click a node and the page opens with the box drawn on it.
Everything the reader learned is a row in SQLite you can `SELECT`. Nothing
leaves the machine.

It is the literature half of [Protracker](https://github.com/karimghabra/projtracker),
a lab notebook: the tracker records what you did, litrag holds what the
field already knows about it, and an assistant reads both. It stands on
its own too.

## Quick start

Needs Node 22+, Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```
git clone … && cd litrag
uv sync --project parser            Docling and its dependencies (torch: a few GB)
npm --prefix app install            Electron, pdf.js
npm run app                         the window
```

In the window: **New library**, then **Add papers…** or drop PDFs (or
Europe PMC JATS XML) anywhere. The first paper also downloads Docling's
layout and table models (~0.5 GB, once). A paper takes about fifteen
seconds on four CPU cores; a GPU is picked up automatically when torch
sees one — the log says `ready on cuda:0` (or `cpu`) as the first paper
opens.

On Windows, `uv sync` takes torch from PyTorch's CUDA 13 index
(`parser/pyproject.toml`), since the PyPI wheel there is CPU-only; that
needs an NVIDIA driver of R580 or newer. On an RTX 5080 the first paper
of a session takes ~30 s (CUDA warm-up) and every paper after it ~2 s.
Nothing else is Windows-specific: the same `uv sync`, `npm --prefix app
install`, `npm run app` from PowerShell or Git Bash.

Which code is the current pipeline and which is the deprecated `lit` CLI,
how a paper moves through it, and every switch: `PIPELINE.md`.

## What you see

| Pane | What it shows |
|---|---|
| Papers | each paper with its key, what kind of paper it is and who said so, the authors, journal and year the file or the record states, its status and live stage — filed · models · layout · tree · saved — then a bar of its lanes, and a flag when no methods section was found |
| Tree | the paper's sections nested as the paper meant them, every node coloured by lane, tables as `rows × cols`, chips to dim everything but one lane; the front matter as a few typed nodes — authors, affiliations, dates, correspondence, keywords |
| Page | the page a node came from with its box, every other node on the page faint; the node's ancestry, role, Docling label and text; a table's cells as a grid |
| Log | the worker's stages and Docling's own log lines, as they happen |

A JATS paper has no page to draw, so the page pane shows the tree as the
paper it came from — headings, paragraphs, formulas, tables, figure
placeholders with their captions — and a click on either side selects the
node on both.

## What the layout model misses, read back from the page

The layout model draws boxes and labels them; the words come from the
PDF's text layer. Where it draws no box — the top of a column after a page
break, a paragraph beside a wide figure — the words reach no node, and
where it calls a body block a footer they are thrown away. The text layer
has every line with its position, so `recover.py` reads it back before
the tree is built: a line no box holds comes back as a paragraph at its
place in reading order, a box whose text lacks lines the layer has inside
it is rebuilt from the layer, an equation the model saw but did not read
takes the text inside its box, and every block learns its geometry —
first-line indent, last-line width. The layer is read as rows cut where
the baseline moves (pdfium's own line breaks run a paragraph's first
lines together), with a raised digit marked as the superscript it is
("10^7", "cm^-1", "applications.^17") rather than fused into the number
before it, and its whole words replace Docling's split ligatures ("signi
fi cantly") wherever the two agree letter for letter. A paragraph Docling
carried over a page break is read on both of its pages; a rotated
"Downloaded from" sidebar glued to the pages after it is taken apart; a
table's note glued to the paragraph before it is the table's footnote; a
table Docling could not structure gets its rows from the layer.
Deterministic, local, and derived again on every `rebuild`; the raw
Docling document is untouched. On the pilot libraries: 61 equations
filled, 655 blocks given their whole words, and the sentences in a text
layer that reach no node down from 156 to 75 across 76 PDFs.

## What the layout model cuts, put back

The layout model never joins a paragraph it split at a page or column
break, and a PDF's fonts hand it symbols through the wrong table: "pH ¼
7.4" for "pH = 7.4", "1 - 10 6" for "1 × 10⁶". Neither is the layout
model's to fix, so the tree builder does: the rules in `tree.py` join the
clear continuations (a lowercase start, a bracket closed on the next page,
a trailing comma), even across a figure or a running head the page put
between the halves; `glyphs.py` undoes the font errors in the contexts
that make it safe.

Geometry decides most of what the words leave open: in a paper that
indents its paragraphs, a block whose last line runs the column's full
width followed by a block whose first line is flush left is one
paragraph, full stop or not; a block whose last line stops short ended
its paragraph. On the pilot libraries that is 712 joins from the page and
4 from a model. What neither the words nor the page can decide — a block
that stops without a full stop before one that starts with a capital — a
local model can, by reading the two ends. `judge.py` asks Ollama on this machine one short question per
such pair (qwen3:14b, thinking off, about 0.15 s each once loaded) and
files every verdict in `judgments`, so `rebuild` reuses them without a
model and a second run asks nothing twice. Unlinkable beats mislinked
here too: the judge is consulted only where the rules are silent.

```
npm run judge -- --lib ~/.protracker/library/looped-ligament            every parsed paper
npm run judge -- --lib … --key doi:10.1002/jbm.a.32783 --show           one paper, each verdict printed
npm run judge -- --lib … --dry-run                                      count the pairs, ask nothing
```

The worker does the same: `{"op":"judge","lib":…}` re-reads a library with
the model, and `ingest`/`reparse` with `"judge": true` (or `LITRAG_JUDGE=1`
in the app's environment) ask as each paper is read. Without Ollama the
paper is read as before and the log says so.

## What is decided by meaning

A node's role comes from the top-level heading above it, and the vocabulary in
`facets.py` names the lane of the headings everyone uses. Every new corpus
brought headings the list did not have — "Results/Discussion", "Strengths and
limitations", Wiley's "3 | Results" — and a list is never finished. So a
heading the vocabulary does not know is named by its meaning: `meaning.py`
embeds it with a local model (nomic-embed-text through Ollama, the same one
the CLI uses for search) alongside a few example headings per lane, and takes
the lane whose examples lie nearest when the nearest is near enough and
clearly nearer than the next. A review's topical heading ("Immune cells in the
aging ventricle") is near nothing and stays `other`: unassignable beats
misassigned, still.

The same oracle answers every other question of resemblance the reader used
to put to a list of phrases, each a *kind* with its own examples, threshold
and margin, and each asked only where the rules had no word:

- `front` — what a line of front matter is (authors, affiliations, dates,
  correspondence, keywords, funding, a notice) when the regexes say nothing;
  and a journal's name set as a heading on the first page, with another
  heading right under it, becomes a notice rather than a section.
- `label` — whether a block opens with a section label the run-in list does
  not know ("Associated data", "Level of evidence"), the one place a verdict
  vetoes a join the page's geometry would make: the failure is a visible
  split, never a silent merge. (It does not drop an empty section on a
  resemblance: PMC's two "Footnotes" groups showed that an empty section
  dropped between them turns a quiet tree into an echoed heading.)
- `figtext` — whether a short text under a figure is its legend; a verdict
  can adopt a legend of four words, and never takes one away.
- `refentry` — whether a line in the back of a paper is a reference entry in
  a shape no pattern knows, so a list with no heading is still found.
- `which` — which of several open paragraphs a displaced tail belongs to,
  and which of several cut captions.
- `block` — what a section's paragraphs are, from seven lane centroids
  computed once from every labelled paragraph of six libraries plus a prior
  on where in a paper each lane sits (`data/block_lanes.json`, numbers only,
  no text). A top-level section whose heading names nothing takes methods,
  results or references from its paragraphs when the mean over three or more
  is clearly of one lane; a section whose heading names a lane is never
  overridden, only noted for the audit when its paragraphs say otherwise. A
  paper that prints no heading at all gets one built for each run of blocks
  of one lane (a Viterbi pass with a fixed cost per switch; any combination
  of lanes in the paper's own order; nothing but back matter after the
  references), labelled `built` so the window says who wrote it.

An embedder is not a language model. The same text gives the same vector
every time, so every answer is a deterministic function of the text, the
examples and the model, and every answer is a row in `lanes.sqlite` beside
the libraries (`verdicts`: kind, text, model, name, score, margin) — `rebuild`
reads the rows and needs no model, a second machine reproduces the tree from
the rows, and a wrong answer is a row a person can read and delete. The model
string carries a hash of the kind's examples, threshold, margin and prefix,
so an edit to any of them re-asks instead of replaying a decision made under
another rule. Thresholds were set on the corpora (NOTES.md has the tables):
at a cosine of 0.78 the front kind misnames about one line in a hundred and
names a third; the label kind at 0.85 names nothing wrongly; the block
centroids, measured library-out, name a section's lane wrongly about four
times in a hundred at a margin of 0.08 and are silent on most sections.
`LITRAG_LANES=off` turns the oracle off; with Ollama down, every unknown text
is `other`, nothing is stored, and the worker says so.

Does it work without the vocabulary at all? `LITRAG_VOCABULARY=off` is the
experiment: no heading is named by the vocabulary, the embedder names every
one alone, abstract and references included. Over the 826 distinct top-level
headings of the six libraries (8,301 sections) the embedder alone agrees with
the vocabulary on every lane retrieval partitions on — abstract, introduction,
results, references 100 %, discussion 99.7 %, methods 98.1 % — and on 90 % of
the back matter, where what it misses ("Supporting information", "Ethics
statement", "Keywords") falls to `other` rather than to a wrong lane. Its one
wrong lane is "Graphical abstract" as abstract (21 sections), which an example
in the `back` group would cure and the vocabulary cures today. It is an
experiment, never the default: the vocabulary is free and certain where it
matches, and the depth rule (a heading the vocabulary knows is top-level)
leans on it. NOTES.md has the paper-level numbers.

One question is not one of resemblance: whether a block continues the one
before it, where the words and the page are both silent. Two paragraphs of
one methods section lie as near each other whether or not they are one
paragraph. `boundary.py` answers it by likelihood: a small language model
(Qwen2.5-0.5B through transformers, fetched once, run on the CPU) scores how
probable the first words of B are after the last words of A against how
probable they are alone, and a difference above 1.5 nats per token is a
join. On 600 pairs cut from the corpora's own paragraphs, in the shape the
rules leave open, the scorer finds 92 of 100 joins at a precision of 0.92
(paper-out; at the shipped bar, 71 of 100 at 0.97); on the 71 pairs the
generative judge had ruled on it would also join five of 69 that are not
continuations, so it is off unless `LITRAG_BOUNDARY=on`. Its verdicts are
rows in `judgments` like the judge's, and a rebuild replays them.

## What kind of paper it is, and who wrote it

A review has no methods section and a letter no abstract, so before the
reader judges a paper's shape it asks what kind of paper it is
(`paper_type.py`): research, review, case report, letter, editorial,
protocol, data descriptor, correction, or other — and a subtype where a
label states one (a randomised trial, a systematic review, a case series, a
brief report). Every source speaks its own vocabulary, so one table maps
every spelling seen to one canonical type: Europe PMC's publication types
(MeSH's, for a MEDLINE paper), the JATS file's `article-type` and its subject
line, the title's own words ("… a systematic review and meta-analysis",
"Case report:", "Protocol for a randomised …", "Erratum:"), the label the
publisher printed above the title. The table also says which labels *name* a
kind and which are the publisher's default bucket — `research-article`,
"Journal Article", "Article" — which names nothing. The most trusted specific
label decides (the record's, then the file's, the subject line's, the title's,
the page's), `papers.type_source` says which, and every disagreement between
two labels, or between a label and the paper's shape, is a note the audit
shows; nothing is overridden in silence. A default alone never makes a
research paper: the shape must agree — a methods lane and a results lane,
or, when no heading says results, the methods after the discussion as
Nature sets them, or measurements (±, p-values, n =, means) reported in a
tenth of the body's paragraphs, which a review with a methodology section
never has — else the paper is `other` with the default named. The shape —
the lanes the tree has and their order, what the body's paragraphs report,
a case heading, a letter's opening, a systematic review's own headings, a
data descriptor's (Scientific Data's and Data in Brief's), a protocol's
future tense — decides by itself only where its rule was measured precise
on the papers the labels do settle (NOTES.md): reviews, case reports and
data descriptors, and research when a default confirms it; the rest of its
readings are notes. The
audit expects a methods section of a research paper and not of a review, and
the harness reports a table by type and subtype.

Who wrote it, where and when comes the same way (`record.py`): a JATS file's
contributor group — names, affiliations resolved through their ids, the
corresponding author, editors left out — and its journal and year, read on
every parse and rebuild; else Europe PMC's record, in the one call the type
already makes, with the author string as it gives it. The file's word
overrides the record's; nothing is inferred, and a PDF the record does not
know keeps its front matter's `authors` and `affiliations` lines and empty
columns (`papers.authors` as JSON, `journal`, `year`). The window shows the
byline on the paper card and above the tree.

The front matter itself is what the page carries above and around the
title, typed line by line: authors, affiliations, dates, correspondence,
keywords, funding, and the publisher's notices; the rest of the banner — the
journal's home page, "Cite this:", a DOI line, the licence — is left out and
counted in `dropped`. RSC's first page taught the reader that the layout
model may read the introduction's heading and first lines *before* the
title block: a heading the vocabulary knows above the title now leads the
body, and the paragraphs that follow the one-paragraph abstract go with it.

## Headings canonicalised

Every XML publisher formats its sections differently, and a PDF's headings are
whatever the layout model read, so beside the author's heading — kept as
written — each section carries the one name the catalogue gives that kind of
section (`headings.py`, `nodes.canonical`): "Materials and methods" for
"2. Experimental", "Conflicts of interest" for "Declaration of competing
interest", "Conclusions" for "5. Summary and outlook". The catalogue is the
spellings 514 XML files were found to use, harvested from their own sections
(`python -m litrag_parser.headings --harvest`), and a few families; where the
vocabulary was silent, an exact spelling settles a heading's lane ("Case
presentation" is results, a competing interests statement is back matter),
an exact spelling of two words or more settles its depth, and a family
settles a lane only in the body ("Limitations of the present study" is
discussion; "Reference materials" is not references). A name stands beside
a section only when its lane is the section's own. Where the catalogue is
silent, the
embedder answers against centroids learned from the harvest — one per lane
and one per canonical name (`data/headings.json`, numbers only) — and is
taken only when near enough and clearly nearer than the next, measured
library-out before it decided anything, and never as abstract, references
or back matter for a heading that carries a body number (no numbered
section in three corpora was any of those; a review's "8. Regulatory and
Ethical Considerations" is a body section, lane or no lane); a heading that
names nothing keeps no name. Built headings take the catalogue's names, which are the corpus's
modal spellings. The window shows the name after the heading when the two
differ.

## From a finding to the method that produced it

A query that tests a hypothesis finds results and then wants the methods
behind them — not the methods section, but the paragraphs about mechanical
testing when the finding is a modulus. `edges.py` draws that link inside
each paper and the store keeps it as rows (`edges`): a finding is a results
paragraph (or a discussion paragraph that cites a figure), the candidates
are the paper's own methods subsections, a closed list of five to fifteen,
and three kinds of evidence are tried in order, the one that decided written
on the edge. A pointer in the finding ("see Section 2.3") decides alone.
Then terms only one subsection owns — words and word pairs that occur in
that subsection and in no other, counted per paper with no model, so
"compressive modulus" names mechanical testing and "calcein" names the
live/dead assay; a paragraph that reports a modulus and a swelling ratio
gets two edges, because it rested on two methods. Then the caption of the
figure the finding cites, read the same way, which is how a terse finding
still arrives. Last, resemblance from the embedder, only when the nearest
candidate clearly beats the next, stored as a verdict so a rebuild replays
it, and only when asked for (`LITRAG_EDGES_SIMILARITY=on`), because it has
not passed its gate. A finding no evidence can place stays unlinked, and the
count says so.
Paragraphs are also linked to the figures and tables they name
(`cites_figure`), and the window shows every edge on a selected node,
"Measured by" and "Findings measured here", each a click away.

On the six libraries (765 papers) two findings in three are linked, most
by a term only one subsection owns, one in eight through a caption.
Measured against the findings whose pointer names their method, with the
pointer hidden, the terms named the pointed section eleven times in
thirteen and resemblance three in five, on thirteen pointers in all: a
truth set too small to trust either alone, which is why every edge carries
the evidence that made it, the window shows it, and resemblance is off by
default. NOTES.md has the numbers.

## Citations

Every entry in a paper's reference list is a row (`refs`: number, first
author, year, DOI, and from JATS its id, PMID and title), and every in-text
marker — `[6,9,12]`, `(Lyon, 2020)`, `Levin (2019)` — is a row tying the
node to the entry (`citations`). A node shows "→ 3" when it cites three
entries and an entry "[12] ← 5" when five nodes cite it; the detail pane
lists both, each a click away. `select n.role, r.first_author, r.year,
r.doi from citations c join nodes n using(node_id) join refs r on
r.paper=c.paper and r.ref_no=c.ref_no` is the shape of the question this
answers: which chunk leans on which paper.

## The harness and the end-to-end suite

Two ways to judge the reader on papers rather than on a fixture:

```
npm run harness -- --lib ~/.protracker/library/looped-ligament --json ~/.protracker/library/harness.json
npm run harness -- --lib ~/.protracker/library/looped-ligament --baseline ~/.protracker/library/harness.json --gate
npm run e2e                                   the real window on the JATS fixture, ~15 s
LITRAG_E2E_PAPERS=~/papers npm run e2e        the real window on a folder of PDFs and XML, on the GPU
LITRAG_HEADLESS=1 npm run e2e                 the same with the window hidden and rendered offscreen
```

The harness runs every parsed paper of a library through the tree builder,
the citation linker and the audit — from the saved Docling documents, no
models, seconds for two hundred papers — and reports per paper what a
person checks first: is the title the title, was a methods section found
(or is it a review), how many nodes is the front matter, how many nodes
read wrong, how many citations linked. Then the corpus in one table, the
worst papers first, and against a saved run, what got better and what got
worse; `--gate` fails on a lost title, a lost methods section or more
errors, so a change to the reader is judged on the corpus. `--show <key>`
prints one paper's sections, front matter and findings. For a PDF the
harness also compares each page's words (pdfium's text layer) with the
words that reached a node on that page — page coverage — and lists the
pages under 60%: where the layout model dropped a block, or a table came
through without its text. Keep the saved
runs beside the libraries, not in the repository.

The end-to-end suite (`app/tests/e2e`, Playwright Test) launches the real
window and the real worker, makes a library, ingests papers, and checks
what the window shows: every paper parsed, titled, with its methods
found; no one-letter nodes; the front matter a few typed nodes; the page
drawn (PDF) or the paper laid out (XML); citations linked both ways; the
audit clean on the fixture. `npm run e2e:report` opens the last report with
traces and screenshots of anything that failed.

## Does the tree make sense?

Docling and the tree builder both get things wrong in ways a person spots
at once: a paragraph that is one letter, a sentence starting with "=", an
equation missing between "the equation below:" and "where …", a journal's
logo filed as a figure on every page. The audit walks every node against
its neighbours and names those, graded error / warn / info:

```
uv run --project parser python -m litrag_parser.audit --lib ~/.protracker/library/looped-ligament
uv run --project parser python -m litrag_parser.audit parser/tests/fixtures/*.docling.json --errors
```

The worker answers the same to an `audit` op. The fixtures audit clean of
errors and `pytest` keeps them so; run it over a library after any change
to the reader, and turn what it finds into a fixture and a rule.

## Where things live

```
$LITRAG_ROOT   (else $PROTRACKER_LIBRARY, else ~/.protracker/library)
├── models/docling/        Docling's models, if pre-fetched here
└── <library>/
    ├── library.json       id, name, project id
    ├── store.sqlite       papers · pages · nodes · nodes_fts · events
    ├── papers/            the files, named by key: doi_10.1093_nar_gkr715.pdf
    ├── parsed/            the raw Docling document per paper, never edited
    └── inbox/             drop files here instead, if you prefer
```

`sqlite3 store.sqlite "select role, count(*) from nodes group by role"` is a
fine way to look at a library; so is the worker's `sql` op.

## The worker on its own

```
uv run --project parser litrag-parser --root=/path/to/root
{"id":"1","op":"init","name":"Looped Ligament"}
{"id":"2","op":"ingest","lib":"looped-ligament","paths":["/path/to/paper.pdf"]}
{"id":"3","op":"tree","lib":"looped-ligament","key":"doi:10.3390/mi15070851"}
```

One JSON line per request on stdin; events on stdout with the same `id`.
`AGENT.md` lists every op and shape.

## The `lit` CLI

`src/` holds the earlier retrieval loop — Europe PMC search and fetch,
chunks, embeddings, hybrid retrieval with a graph walk, `lit query`, `lit
sql`. It still runs (`npm run lit -- help`) against its own store and is
not yet wired to the tree; `DESIGN.md` says how it will be. It is
deprecated: kept until its verbs are ported to the tree store
(`BACKLOG.md`), and `PIPELINE.md` says what to use instead.

## The documents

| File | What it is |
|---|---|
| `PIPELINE.md` | The pipeline on one page: what is current and what is deprecated, a paper step by step, reparse against rebuild, every switch. |
| `DESIGN.md` | The decisions: the tree and the app (revision 2), and the retrieval loop they will feed (revision 1). |
| `AGENT.md` | How an assistant drives the worker and the CLI — ops, shapes, conduct. |
| `NOTES.md` | The assistant's notebook: the libraries, what worked, standing decisions. |
| `BACKLOG.md` | Where wants wait until they are built. |
| `CHANGELOG.md` | What each version changed. |
| `CLAUDE.md` | The invariants a change to this code must keep. |

`npm run check:all` runs everything: the CLI's typecheck and tests, the
app's typecheck, tests and build, the parser's pytest. The parser tests run
over saved Docling documents and never touch the network or the models.
(The JATS fixture is regenerated from its XML through the worker's own
`jats_stream` whenever that changes; Docling reads JATS in a second, with
no models.)
