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

## Install

Each [release](https://github.com/karimghabra/litrag/releases) carries the app ready to run
beside a script that installs what it needs: no administrator rights, nothing installed
beforehand (not even Python), one folder for this user.

**Windows** (10 or 11, 64-bit): download `litrag-<version>-win-x64.zip`, right-click it,
**Extract All**, and double-click `install.cmd` in the folder that makes (if Windows warns
about a file from the internet: **More info → Run anyway**). It copies the app to
`%LOCALAPPDATA%\litrag\app`; installs [uv](https://docs.astral.sh/uv/) beside it, and through
uv Python 3.12 and the parser's environment (Docling, torch) in `%LOCALAPPDATA%\litrag\venv`;
installs [Ollama](https://ollama.com) with winget if it is missing and pulls its embedder,
`nomic-embed-text`; and puts **litrag** in the Start Menu. Everything it does goes to
`%LOCALAPPDATA%\litrag\install.log`.

**Linux** (x86-64): download `litrag-<version>-linux-x64.tar.gz`, then

```
tar -xzf litrag-<version>-linux-x64.tar.gz
litrag-<version>-linux-x64/install.sh
```

The same steps, into `~/.local/share/litrag` (`$XDG_DATA_HOME/litrag`), with an entry in the
applications menu. Ollama's Linux installer needs root, so the script names it rather than
running it; with Ollama there, it pulls the embedder. There is no macOS build yet: there, run
from source (below).

What it downloads: the archive (~135 MB for Windows, ~110 MB for Linux), about 0.3 GB of
Python packages, and torch — the CPU build ~0.1–0.2 GB, the CUDA 13 build ~2 GB on Windows and
~3 GB on Linux with NVIDIA's libraries. The first paper then fetches Docling's models (~0.5 GB,
once); `nomic-embed-text` is ~0.3 GB.

GPU or CPU: the script takes the CUDA 13 build when `nvidia-smi` runs, which wants an NVIDIA
driver of R580 or newer (it warns when the driver is older), and the CPU build otherwise.
`install.cmd -Torch cpu` or `-Torch cu130` (`install.sh --torch cpu|cu130`) chooses.
`-SkipOllama` (`--skip-ollama`) leaves Ollama alone: papers are read without it, while Query,
and naming headings by meaning, need it. `-PrefetchModels` (`--prefetch-models`) fetches
Docling's layout and table models into `<libraries>/models/docling` at install time, so the
first paper needs no network.

To update, run the new release's install script: it replaces the app, brings the environment
in line with the new release, and keeps the rest. To uninstall, run
`%LOCALAPPDATA%\litrag\uninstall.cmd` (`~/.local/share/litrag/uninstall.sh`): it asks, then
removes the app, the environment, uv and the shortcut. Neither script creates, moves or deletes
a library — they live in `LITRAG_ROOT` (default `~/.protracker/library`) — and uninstalling
leaves them, and Ollama, as they were.

## From source (developers)

Needs Node 22+, Python 3.11+ and [uv](https://docs.astral.sh/uv/).
Query also needs [Ollama](https://ollama.com), with `ollama pull nomic-embed-text`.

```
git clone … && cd litrag
uv sync --project parser --extra cu130   Docling and its dependencies, CUDA torch (a few GB; --extra cpu without an NVIDIA card)
npm --prefix app install            Electron, pdf.js
npm run app                         the window
```

In the window: **Projects → New project** (a name, and a few lines on what
its literature is about), then **Search** Europe PMC and **Fetch & read**
what it finds — the XML where it is open, an open PDF where there is one,
and a list of the rest with their publisher's page, to download by hand and
drop anywhere on the window. Or **Papers → Add papers…**, or drop PDFs (or
Europe PMC JATS XML) anywhere. The first paper also downloads Docling's
layout and table models (~0.5 GB, once). A paper takes about fifteen
seconds on four CPU cores; a GPU is picked up automatically when torch
sees one — the log says `ready on cuda:0` (or `cpu`) as the first paper
opens.

Which torch is chosen by name (`parser/pyproject.toml`): `uv sync
--project parser --extra cu130` takes PyTorch's CUDA 13 build, which needs
an NVIDIA driver of R580 or newer; `--extra cpu` takes its CPU-only build,
an environment of ~1.4 GB where the CUDA one is ~6 GB. With neither, torch
is PyPI's: the CUDA build on Linux, CPU-only on Windows and macOS. So a
Windows machine with an NVIDIA card names `cu130`, and a Mac, which has no
CUDA build, gets PyPI's wheel under either extra. uv remembers no extra,
and a `uv run` without the one the environment was synced with syncs it
back to PyPI's torch (on Windows, CPU in place of CUDA). At a shell, give
`uv run` the same `--extra`, or `--no-sync`. The npm scripts that run
Python (`check:all`, `harness`, `audit`, `judge`, `gate:ingestion`) pass
the extra themselves, reading it off the torch the environment has, and
make a missing environment with `cpu`; `npm run app` starts the worker
with `--no-sync` once the environment exists. On an RTX 5080 the first paper of a
session takes ~30 s (CUDA warm-up) and every paper after it ~2 s. Nothing
else is Windows-specific: the same `uv sync`, `npm --prefix app install`,
`npm run app` from PowerShell or Git Bash.

Which code is the current pipeline and which is the deprecated `lit` CLI,
how a paper moves through it, and every switch: `PIPELINE.md`.

The release archives are `npm --prefix app run release -- win` (which builds on Linux too) and
`-- linux`, written to `app/release/`; `.github/workflows/release.yml` builds both for each
published release, installs each once on a clean runner, and attaches them.

## What you see

Six tabs over one project at a time, picked at the top:

| Tab | What it is for |
|---|---|
| Projects | every project as a card: papers by format and type, how many are read, the searches run, the candidates waiting for a PDF, the passages embedded; **New project**; **Merge libraries…** files every paper of several projects once in a new one |
| Search | a Europe PMC query (or one the local model drafts from the project's description); each hit with what can be had of it — open XML, an open PDF, nothing open — kept as a candidate; **Fetch & read** takes the XML first (Europe PMC's, else NCBI's for an NIH author manuscript), the PDF second (NLM's PMC Cloud Service, else EBI's bulk area), and lists the rest with their links |
| Papers | the three panes below; the tree pane's **Canonical** face re-hangs the paper under its type's structure, each section tagged with the mechanism that placed it |
| Types | the kinds of paper the project holds, each kind's canonical structure (its slots in order, how often its papers have each), and any paper drawn onto it: a line from each printed section to its slot, coloured by the vocabulary, the catalogue, the embedder, a built heading or the outline judge; the slots it lacks drawn empty |
| Query | a question, and the passages that answer it, each hydrated from its tree: the headings above it, the paragraphs either side, the methods a finding was measured by (the paragraph it rests on, where the method is described in another paper, statistics and materials apart), the findings a method measured, the figures and references it cites — a figure with the numbers read from its charts; **Open in the tree** lands on it |
| Graph | the papers and the citations between them, drawn as a graph with the works they cite most beside them; **Citation round** files what the papers cite (and what cites them) as the next round's candidates; SQL over `works`, `authors` and `cites`, its candidates fetched and read from the results |

The Papers tab:

| Pane | What it shows |
|---|---|
| Papers | each paper with its key, what kind of paper it is and who said so, the authors, journal and year the file or the record states, its status and live stage — filed · models · layout · tree · saved — how far its reading can be trusted (hover for why), then a bar of its lanes, and a flag when no methods section was found. Above the list: a sort (as added, format, type, title, year, confidence lowest first) and chips that narrow it to PDFs or XML, to one type of paper, or to one band of confidence |
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

The layer also knows the type. pdfium gives every glyph its font and
weight, so `typography.py` reads every line as runs of one style, names
the body's style (the face and size that hold most of the paper's
letters) and treats a line set apart from it — bold, italic, larger, or on
its own row under a deep numbering — as the heading it is. Four things the
layout model gets wrong come right that way, before the tree is built: a
heading printed run in at the start of its paragraph ("2.1. Non Surgical
Approach. For small tears …", "Materials. Human amniotic membranes …") is
cut into a heading of its own; a heading run into the paragraph before or
after it is cut out; a heading no box holds comes back (bold, larger or
numbered — an italic line alone is emphasis or a species name); and a
heading whose letters the layout model split ("T endon", "I NTRODUCTION")
is spelt as the row prints it. The abstract's labels ("Background:",
"Methods:") and the keywords, set the same way, are left alone: nothing is
cut before the body's first heading, and a back-matter word inside the
body ("Note:", "Abbreviations:" under a table) is a table's note, not the
paper's. Measured on the 64 PDF/XML pairs, the XML's headings found went
from 0.82 to 0.89 with no paper's text worse laned.

The type also says how deep a heading lies, which was the reader's largest
loss: a review's own sections read as the introduction's subsections, a
third of a paper filed under the wrong lane. The paper's core sections by
their own names (Introduction, Methods, Results, Discussion, Conclusions)
show how its top level is set; a heading set at least as prominently in
every way the page shows is top-level, one set less prominently is nested,
and where the page cannot tell — a scan's single font, subsections set like
the top level, another face of the same size — the reader's own rule
stands; an editorial whose topical headings are all set one way has one
level. A printed heading is never the child of one the reader built. With
the fixes that came with it (a keywords or highlights box, or a key-message
box's bullets, does not keep the introduction read after it; an abstract
read after the introduction's heading gives the prose after it back; front
matter ends at the paper's first heading after its prose; citations in
ASM's and PNAS's shapes; the ligatures Hindawi and RSC fonts lose), and
with the page's own order and words put first (a page the layout model read
out of order in one column, put back; a running head as what recurs at the
same height, so a section heading that also stands in a visual abstract is
no furniture; a structured abstract printed as sections read as the
abstract; a line of capitals as a heading where the paper sets its headings
so; a lane from content only where the paper's headings name its methods or
results), the PDFs' agreement with their XML went from 0.83 to 0.97 of the
words, and papers well matched from 126 to 183 of 199.
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

## How far a reading can be trusted

A PDF only shows what its XML states, so a paper held in both formats can be
read twice and the two trees compared (`pairs.py`): for every paragraph of
the XML, is its text in the PDF's reading at all (*recall*), is it in a
paragraph of the same lane (*faithful*), did it arrive in one piece; and the
other way round, how much of the PDF's prose does the XML hold (*precision*).
One DOI files once in a library, so the second format lives in a companion
library — `held-out-pdf` beside `held-out-xml`, `looped-ligament-pairs`
beside `looped-ligament`:

```
uv run --project parser --no-sync python -m litrag_parser.pairs --pdf-lib <library> --xml-lib <library> --json <outside the repo>/pairs.json
uv run --project parser --no-sync python -m litrag_parser.pairs --pdf-lib <library> --xml-lib <library> --show doi:10.…
uv run --project parser --no-sync python -m litrag_parser.confidence --calibrate <outside the repo>/pairs.json …
```

On 199 such papers the text is nearly always all there (recall 0.99), and
what goes wrong is where it is filed: a top-level heading the layout model
dropped or fused with the next, so the results stay under the methods; a
review's sections read as the introduction's children; a body that starts
with no heading and stays in the abstract; methods printed after the
reference list; and blocks that carry their paragraph twice. None of the
reader's older measurements of itself — page coverage, dropped lines, glyph
residue, audit errors — predicts any of it. So `confidence.py` measures the
tree for exactly those failures (where the prose lies by lane, the lanes a
paper of its type should have, text that repeats, headings that are not
headings, paragraphs cut in two) and turns them into one number in (0, 1]
with its reasons: `papers.confidence` and `confidence_detail`, on the paper
card, a sort and a filter in the window. Every limit is read off the pairs,
and the score is kept honest by them: at 0.9 or more, four readings in five
matched their XML well (faithful and precision both at least 0.9) and one in
twelve was seriously off; under 0.5, five in six were seriously off. It
flags; it never changes a tree.

## A model in the loop: the outline judge

What a PDF loses is rarely text and mostly structure, so `outline.py` lets a
local model read the whole paper — a median paper is 11,000 tokens, Qwen 3's
window is 40,000 — and say where its sections are: for every section a
title, a depth, a lane and the paragraph it starts at, as JSON. What is
taken from the answer is measured against the XML pairs and bounded by the
invariants: a lane, where the rules gave none (a section whose heading names
no lane and whose place under the section above it the reader only
inferred: in a review, one the model puts outside that section is a topical
section, `other`, whatever the model calls it; in a research paper a
subsection is part of its section, and only a section the reader could not
place takes the model's lane); a boundary, where the reader had none
(a built heading, labelled `built`, from the model's title, laned the way
the reader lanes any heading — its own name at the top level, its section's
above when nested — never by the model's word); never the depth, since
meaning alone cannot tell a flat outline from a nested one, and never a lane
a heading or a numbering already settled — there the model's word is a
note. Every answer is a row in `outlines`, keyed by the paper as the model
saw it and the model's name, so a rebuild replays it and never asks. It is
off unless `LITRAG_OUTLINE=on` (or `outline: true` on an `ingest`, `reparse`
or `judge` request); `LITRAG_OUTLINE_MODEL` picks the model.

```
LITRAG_OUTLINE=on npm run app
uv run --project parser --no-sync python -m litrag_parser.outline --pdf-lib <library> --xml-lib <library> --model qwen3:14b --json <outside the repo>/outline.json
```

The second line measures a model on papers held in both formats: the
faithful score before and after the judge, paper by paper, the answers it
could not read, the seconds a paper took. On the 64 pairs measured
(NOTES.md, 2026-09-18) Qwen 3 14B takes the PDFs' agreement with their XML
from 0.80 to 0.91 and the lanes their headings agree on from 0.88 to 0.96,
eleven papers better and two worse, at twenty seconds a paper; Qwen 3 8B
does nearly as much in thirteen and made no paper worse.

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

Each edge has a strength as well as a kind. A pointer scores 1.0; terms
score between 0.80 and 0.95, a caption between 0.70 and 0.80 and
resemblance between 0.60 and 0.70, each higher within its band the more
marks it rests on (a word pair counts two, a word one). The kinds never
overlap, so the order is still the evidence's; inside a kind, the edge
with more behind it comes first. A library read before this needs
`rebuild` for the new scores.

**In a query** the link is followed both ways, every piece a row
(`retrieve.hydrate`):

- A finding shows the methods it was measured by, strongest first, and of
  each method **the paragraph** the finding rests on rather than the
  subsection's opening: the one its marks name, else the one sharing the
  finding's rarer words, else, when no paragraph stands out, the first.
  Statistics and materials — the methods every finding leans on — are kept
  apart under "Also used", so they never take the place of the method
  that measured it. A finding with only those still gets its figure's
  methods, then its section's.
- A hit inside a methods subsection shows **the findings its method
  measured**, the reverse walk over the same edges.
- **Described elsewhere.** "Electrocompacted as previously described [14]"
  says nothing of how. `lineage.py` reads such sentences in a closed
  vocabulary of cues (a pointer inside the paper, a supplier's protocol or
  a figure's credit is none), takes the entries they cite, and looks for
  each among the library's papers by DOI, then PMID, then title. When the
  library holds the paper, the method shown is that paper's own
  subsection — the one the sentence's marks name, else its whole methods
  section, never a guess between subsections; when it does not, the entry
  is named, with the candidate that would fetch it. `python -m
  litrag_parser.lineage --lib DIR` counts all of it over a library.

**The truth set.** Thirteen pointers cannot say how often the edges are
right, so the window collects the truth: **Label links** on the Papers tab
steps through a queue of findings spread across papers and publishers,
linked and unlinked mixed (`label_queue`), and for each one a person
checks the methods it was measured by, or "no method", and may mark the
paragraph inside it (number keys, N, Enter). A finding's own **Label**
button, under "Measured by", opens it alone. The labels are rows of the
library (`link_labels`), a person's work rather than the paper's, so a
rebuild or a merge keeps them; each finds its finding again by id, else by
its words. `truth` (the op, or `python -m litrag_parser.truth --lib DIR
--measure`) scores the edges against them: precision by kind of evidence,
recall, the misses, the false links, and how often hydration's paragraph
is the one the person marked, beside the method's first paragraph.
Resemblance stays off until it scores 0.9 there.

A person need not label all of it. **Let the model label** (in the same
panel, or `python -m litrag_parser.labeller --lib DIR`) has the local model
(`qwen3:14b` through Ollama, on this machine) answer the same question, by
the same rule, for the hundred findings the queue would offer (twenty
seconds a finding with `qwen3:1.7b` on four CPU cores; the 14B model on a
GPU is untried). Its
answers are rows of their own (`model_labels`), never mixed with a
person's. The queue then offers the model's findings first, without saying
what it answered, so the person's next labels are its audit: once 25 are
audited and the person agrees on 90 % of them, its labels count for the
rest, and `truth` measures the edges again with them. Until then they are
only compared, finding by finding, with every disagreement listed.

## Figures read into numbers

A chart in a figure is data the paper does not print anywhere else. As a
PDF paper is read, each of its figures is cut from its page and its plots
read into rows (`charts.py`, `figures.py`): every bar and every point with
the value it stands for, the ends of its error bar, its series and its
category, and above them the axis's title and unit and the panel's letter.

- **How the figure was drawn decides how it is read.** A figure drawn as
  vectors is rendered at 600 dpi and its words are the PDF's own text layer
  — titles set on their side, slanted labels, `10^3` on a log axis. A
  figure that is an image is read at its own resolution, its words by OCR
  (RapidOCR, with the models its package ships: nothing is fetched).
- **A scale, or no number.** The y axis is fitted to at least three of its
  tick labels, linear or log, and must hold within 1 % of its range; one
  misread label may be left out. A frame with no numbers beside it — a
  photograph's edge, a panel's border — is no chart; a scale that will not
  hold leaves the plot `unread`, with the reason, and gives no value.
- **What is measured.** Bars standing on the axis, told apart by their
  fills (grouped bars by the colours repeating), their tops read through an
  outline's middle; markers, round shapes left when the lines joining them
  are opened away, two drawn over each other parted by colour; error bars,
  thin strokes up from a bar or a marker to their cap, and down into a bar
  in another colour. A whisker that cannot be seen is null, never assumed.
  Names come from the labels under the axis and the legend's swatches.
- **Where it shows.** A picture in the Papers tab lists its plots as tables
  — value ± error, a CSV of each a click away — and a figure read before
  this existed has **Read the figures**. In Query, a passage that cites a
  figure (or is its caption) carries its numbers, the panel it names first
  ("Figure 2B"). `select c.y_label, v.category, v.y, v.err_hi from
  chart_values v join charts c using (paper, figure, plot)` is the shape of
  the question.

On six synthetic charts with known values — simple, grouped, outlined with
slanted labels, points over days, two panels, a log axis — every value is
within 1.5 % of its axis's range, read either way. On a Wiley paper drawn
as vectors, 10 of 11 plots were read (the 11th, stress–strain curves, is
said unread), and they agree with the bars as printed; on an Advanced
Healthcare Materials paper of images, its grouped gene-expression bars read
within about 0.05. Curves without markers, box plots and horizontal bars are
not read yet.

**An XML paper's figures come from a PDF of it.** JATS names its figures
(`<graphic xlink:href="…g001.jpg"/>`) and holds none; Europe PMC's figure
pages sit behind a bot check, and the images its API gives open-access papers
are display-size (~730 px), too small to read a tick label. So the XML stays
the paper — its text, its structure — and a PDF of the same paper is kept
beside it (`papers.figures_file`, `papers/<key>.figures.pdf`) only to read the
charts: each page that prints a figure's caption is read, an image there whole
at its own resolution and the rest with the page's text layer, and every plot
is pinned to the caption under it — "Figure 2." to the XML's figure 2 (a
sentence that begins "Figure 2 shows" is no caption). The PDF comes three
ways: a fetch takes the paper's open-access PDF beside its XML, from the PMC
Cloud Service or else EBI's bulk area; a PDF dropped into a project whose
paper is already its XML is kept for the figures instead of being set aside;
and **Collect PDFs** lists the XML papers whose figures want one, after the
papers with no copy at all.

**Where an open PDF comes from.** The PMC Cloud Service
(`pmc-oa-opendata.s3.amazonaws.com`) is the National Library of Medicine's
copy of PMC's open-access articles as files, the successor NCBI named when
it retired its OA web service in August 2026: it is asked by PMCID alone,
its newest version of the article taken, and the PDF kept only when the MD5
NLM lists for it holds. The articles are NLM's data, each under its own
licence; NLM does not endorse this tool. EBI's bulk area is asked after it,
for what the Cloud Service lacks. An NIH author manuscript is there as XML
and text, never as a PDF.

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

## The library as a graph, and the rounds it grows by

Papers cite papers, and the store keeps that as rows too (`graph.py`). A
*work* is a paper the project holds or a candidate it does not hold yet;
`cites` is one row per citation between two works, `authors` one row per
author of every work, and `works` is a view over both, so a question about
the literature is a `SELECT`:

```sql
-- the papers held, in the order they were published
select year, first_author, title from works where state = 'held' order by year, published;
-- everything one author wrote that the project knows of, held or not
select w.year, w.title, w.state from authors a join works w using (work)
where a.family = 'Akkus' order by w.year;
-- what to read next: the works the project's papers cite most, not held yet
select cited_here, year, first_author, title, work from works
where state = 'candidate' order by cited_here desc, year desc;
```

- **Among the papers held**, a reference naming another held paper — by
  DOI, PMID, or its whole title and year — is a citation as soon as both
  are read; nothing is asked of the network.
- **A citation round** (**Citation round** on the Graph tab, the `round`
  op) asks, for each paper read since the last round, what it cites: its
  own reference list, Europe PMC's list of it and OpenAlex's; ticked, also
  what cites it. Every work it can identify (a DOI or PMID) is looked up in
  Europe PMC — twenty to a request — and filed as a candidate of the next
  round: round 1 is what a search found or a person dropped in, round 2 what
  those cite, and so on. Nothing is fetched. On two papers, 46 and 27
  references and 27 citing papers came back as 117 candidates, 46 of them
  with open XML, in about nine seconds.
- **OpenAlex beside Europe PMC** (**OpenAlex too**, ticked by default).
  [OpenAlex](https://openalex.org) is an open index of 300M+ works in every
  field: it knows papers PubMed never indexed — engineering, physics,
  materials journals — and a reference list for any DOI whose publisher
  deposited one. On three papers Europe PMC had already been asked about,
  its lists named 183 works and added 95 candidates, 18 of them unknown to
  Europe PMC (Ceramics International, J Mech Phys Solids…), and 28 with an
  open copy outside PMC (the candidate's **open** link — for a person to
  follow; nothing is fetched from it). A work Europe PMC knows is filed with
  Europe PMC's record, since that is what a fetch needs; one it does not,
  with OpenAlex's. For a paper neither has a list for (a PDF with no DOI), an
  entry naming no identifier is searched in OpenAlex by its words and taken
  only when one work's whole title (four words or more), year (give or take
  one) and first author are all in it; else it is counted and left, never
  guessed onto a paper. OpenAlex is asked one work at a time by its id —
  free and unlimited — and in lists of a hundred while its daily budget
  lasts ($0.10 a day without a key, shared by every machine behind one
  address; $1 with a free key in `LITRAG_OPENALEX_KEY`). A spent budget
  makes the lists one-by-one (slower, still free) and leaves what needs it
  — what cites a paper, the title searches — for the next round, said in
  the log. `LITRAG_OPENALEX=off` turns it off.
- **Every entry linked to its work, every passage with it.** A paper's
  reference list is linked entry by entry to the works the rounds found
  (`ref_works`): by the entry's own DOI or PMID, by a held paper's whole
  title in it, by Europe PMC's entry at the same place in the list when its
  first author and year agree, or by the one work of OpenAlex's list whose
  whole title, year and first author the entry carries. An entry none of
  these names stays unlinked. Since every in-text marker already names its
  entry (`citations`), every passage that cites is joined to the work it
  cites — `passage_cites`, a view: `select * from passage_cites where work =
  'doi:10.1016/…'` is every chunk of the library that cites that paper. The
  lists each source gave are kept as rows (`ref_lists`), so a paper read
  again is linked again without asking anything. On the Graph tab a work's
  detail lists **Cited in the text** — each passage, its paper and heading,
  the sentence around the marker, a click from the passage in its tree; an
  edge knows how many passages it stands for. In the Papers tab an entry
  says what it names (→ in the library, a click away; → found, a
  candidate), and in Query a passage's citations lead to the papers held.
- **How right the links are** is measured, not assumed: `python -m
  litrag_parser.graph --lib <a PDF library> --truth <the same papers' JATS>`
  scores every link of the first against the identifiers the second's
  entries carry. On six open papers from six publishers (MDPI, Cureus,
  Scientific Reports, Wiley, PLOS, Frontiers): 246 of 270 PDF entries
  linked, none wrong, 14 to works the JATS itself names no identifier for
  (books, a few non-PubMed papers, checked by hand), and 231 of the 252
  works the JATS identifies reached (0.92). What it took: a DOI the PDF's
  line breaks mangled is trusted only once Europe PMC or OpenAlex knows it
  (it had made all 10 wrong links of the first try); the PMIDs PDF entries
  print are read; a held paper's own PMID is looked up so Europe PMC's list
  can be asked; titles are matched with accents folded and the PDF's lost
  hyphens forgiven, against any year the entry prints; and an entry that
  prints no title (Wiley's "Geissler J, Injury 2019, 50, S64.") by its
  first author, year, volume and first page. A rebuild with the network
  cut, and a merge, give back every link.
- **Expand: read the most cited** runs a round, then fetches and reads the
  works the papers held cite most (linked to the most of them, then the
  most cited anywhere; at most the number beside it, 10 by default) — the
  next round of the library in one click. Each paper takes a minute or so to
  read; once read, every passage that cited it leads to it.
- **The next round is a choice.** Any query's rows that are candidates can
  be ticked and fetched and read from the SQL pane (**Fetch & read
  selected**), which is the next round of the library; a paper read later
  is due its own citation round. An author in a work's detail opens the
  project's other works of theirs, or a Europe PMC search for them
  (`AUTH:"Akkus O"`) — a new round from a person rather than a paper.
- **The Graph tab** draws it like a note graph: the papers held as filled
  discs, the candidates cited by at least two of them (or none, or all) as
  hollow ones, each the larger the more works here cite it, coloured by
  round, by year, or held-or-not; hover lights a paper's neighbours, a click
  shows its detail (its authors with ORCIDs where Europe PMC has them, what
  it cites and what cites it), a double click opens a held paper. A query's
  rows are lit in the graph, and a row clicked is found in it.

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
uv run --project parser --no-sync python -m litrag_parser.audit --lib ~/.protracker/library/looped-ligament
uv run --project parser --no-sync python -m litrag_parser.audit parser/tests/fixtures/*.docling.json --errors
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
uv run --project parser --no-sync litrag-parser --root=/path/to/root
{"id":"1","op":"init","name":"Looped Ligament"}
{"id":"2","op":"ingest","lib":"looped-ligament","paths":["/path/to/paper.pdf"]}
{"id":"3","op":"tree","lib":"looped-ligament","key":"doi:10.3390/mi15070851"}
```

One JSON line per request on stdin; events on stdout with the same `id`.
`AGENT.md` lists every op and shape.

An installed litrag has no uv on its path and no checkout: the same worker
is the console script in the environment its installer made,
`%LOCALAPPDATA%\litrag\venv\Scripts\litrag-parser.exe` on Windows,
`~/.local/share/litrag/venv/bin/litrag-parser` on Linux and
`~/Library/Application Support/litrag/venv/bin/litrag-parser` on macOS
(`LITRAG_VENV` names another environment). The window starts that when it
is installed and `uv run` when it runs from a checkout, finding uv on PATH
or, for a window opened from a shortcut, in `~/.local/bin` or
`~/.cargo/bin`; with nothing to run it says why under its header instead.
`LITRAG_PARSER` overrides both, as a JSON array of argv — the way to give a
path with spaces, `["C:\\Users\\Jane Doe\\AppData\\Local\\litrag\\venv\\Scripts\\litrag-parser.exe"]`
— as the path of a file, or as the old command line split on whitespace
(`PIPELINE.md`).

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
