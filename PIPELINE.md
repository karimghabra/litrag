# The pipeline, and how to use it

This repository holds two generations of code. The current one reads papers
into trees: the window in `app/` and the Python worker in `parser/`, which
meet only on JSON lines and keep everything in each library's
`store.sqlite`. The older one, the `lit` CLI in `src/`, is revision 1's
retrieval loop over its own `lit.sqlite`. It still runs and its tests still
pass, but nothing it does reaches the tree, and it is deprecated. This page
says which code is which, how a paper moves through the current pipeline,
and which switches are experiments. `DESIGN.md` has the reasons; `AGENT.md`
§8 has every op and its shape. Every `python -m` below runs as
`uv run --project parser python -m …`.

## What to use

| Part | Status | Use it for |
|---|---|---|
| `app/`, the window (`npm run app`) | current | making a library, adding papers, watching them read, reading trees, pages, citations and edges; **Reparse all** reads every paper again with Docling |
| `parser/`, the worker (`uv run --project parser litrag-parser`) | current | what the window does, and the ops it has no button for, among them `rebuild`, `judge`, `audit` and `sql` |
| `npm run harness`, `npm run audit` | current | judging a change to the reader on whole libraries |
| `python -m litrag_parser.review` | current | reading, paper by paper and page by page, **what the reader did**: every page drawn, every node boxed in its lane, every modification numbered where it happened with the text before and after, and an index over any set of libraries |
| `python -m litrag_parser.summary`, `.report` | current | the corpus on one page — every library, publisher, kind of paper and measurement, in tables — and the front page over it: what the reader does, what it scores against the XML of the same papers, and what is most wrong with it. Both read what the measurements wrote; neither invents a number |
| `python -m litrag_parser.headings`, `.meaning`, `.paper_type`, `.edges`, `.judge`, `.boundary` | maintenance | regenerating the shipped centroids, measuring a kind or the type before it decides, fetching Europe PMC records, running or calibrating the opt-in judges |
| `python -m litrag_parser.pairs`, `.confidence --calibrate` | measurement | a PDF's reading against the XML's of the same paper, from two libraries; the confidence score against those pairs. The truth every change to the reader or to the score is judged on |
| `src/` and `tests/`, the `lit` CLI (`npm run lit`, `bin/lit.js`) | deprecated | nothing new: it searches and fetches from Europe PMC and retrieves over `lit.sqlite`; its verbs are to be ported to `store.sqlite` one at a time and struck from `src/` (`BACKLOG.md`) |
| `AGENT.md` §1–5, `DESIGN.md` "Revision 1" | describe the deprecated CLI | background; revision 2 overruled its reader (R2.1): pdf.js text with heading patterns, and sections cut into 250-word chunks |
| `AGENT.md` §6 | binding | how an assistant behaves around a library, whatever it drives; the verbs it names are the CLI's |
| `AGENT.md` §8, `DESIGN.md` R2 and R3 | current | the worker's ops, and the decisions behind the pipeline |

The CLI and the worker share a library's folder (`library.json`, `papers/`,
`inbox/`) but not its store. A paper `lit fetch` saves into `papers/` is not
in `store.sqlite` until the window or the worker ingests it, and `lit
ingest` cuts chunks into `lit.sqlite`, never a tree.

## A paper through the pipeline

1. **Filed.** The file is stored in `papers/` under its key (the DOI, else
   the PMID, else the file's hash) with a row in `papers`. A paper already
   read stays as it was read; `reread` replaces it. For a paper with a DOI
   or PMID, Europe PMC's record is fetched once: publication types,
   authors, journal, year.
2. **Laid out.** A JATS file is prepared first (`jats_prep.py`,
   `mathml.py`), then Docling reads the PDF or the XML. Its document is
   saved as `parsed/<key>.docling.json` and never edited. This is the only
   step that runs Docling.
3. **Recovered.** For a PDF, the text layer is read back for the lines the
   layout model missed (`recover.py`).
4. **Built.** `tree.py` turns the document into nodes (front matter,
   sections, paragraphs, tables, figures and captions, each with its page
   and box) and undoes what the fonts did to symbols (`glyphs.py`). A
   top-level heading's lane comes from the vocabulary (`facets.py`), else
   the catalogue (`headings.py`, which also gives the section its
   `nodes.canonical` name), else the embedder (`meaning.py`). A paper that
   printed no headings gets built ones, labelled `built` (`structure.py`).
   A page break the rules cannot join goes to the judge only when asked
   (`judge.py`; `boundary.py` when switched on).
5. **Linked.** In-text citations to the reference list (`citations.py`),
   and findings to the methods that produced them (`edges.py`).
6. **Typed.** `paper_type.py` names the kind of paper from the record, the
   file, its subject line, the title, the printed label, and last the
   tree's own shape. Where two of them disagree, a note says so.
7. **Scored.** `confidence.py` measures the tree for the ways a reading goes
   wrong — prose filed in the abstract, the back matter or the reference
   list, one lane holding the body, a lane the type should have and does
   not, text said twice, headings that are not headings, paragraphs cut in
   two — and stores one number in (0, 1] with its reasons. It flags a
   reading; it changes nothing in it.
8. **Saved.** Rows in `store.sqlite`: `papers`, `pages`, `nodes` with
   `nodes_fts`, `refs`, `citations`, `edges`, `judgments`, `events`. The
   audit (`audit.py`) reads them when asked.

## Reparse, rebuild, judge

- **Reparse** (the window's **Reparse all**, the `reparse` op) runs every
  step again, Docling included. It needs Docling's models and takes
  minutes. Use it when what Docling is given has changed: `jats_prep.py`,
  `mathml.py`, or Docling itself.
- **Rebuild** (the `rebuild` op; the window has no button) runs steps 3 to
  8 again from `parsed/`, without Docling, in about a second a paper. Use
  it after any change to those steps. The judge is not asked; its stored
  verdicts are replayed. The embedder is asked only about texts it holds no
  verdict for; while Ollama is down those read as `other` and are not
  stored, and the next rebuild with Ollama up reads them.
- **Judge** (the `judge` op) is a rebuild that also asks the local model
  about the page breaks the rules leave open; `npm run judge -- --lib <dir>`
  asks from a shell (`--dry-run` counts the pairs first), and
  `LITRAG_JUDGE=1` asks on every ingest and reparse.

From a shell, one library per request:

```
uv run --project parser litrag-parser --root=$HOME/.protracker/library
{"id":"1","op":"rebuild","lib":"looped-ligament"}
{"id":"2","op":"quit"}
```

## What the reader changed, and where

Every pass that alters a paper records the change beside its counter (`changes.py`): the page, the box, the
text as it stood, the text as it stands, and the rule's own reason. `tree.repairs` and `tree.dropped` count as
they always did — the log never disagrees with them — and `tree.changes` is the record. What no pass records
yet is named in the review rather than hidden, so the two can be read against each other.

```
uv run --project parser python -m litrag_parser.review  --lib <library> --out <outside the repo>/review
                                                        [--limit N] [--key KEY] [--format pdf|jats] [--scale 1.4]
uv run --project parser python -m litrag_parser.summary --review <dir> --measure <dir> [--lib <library> ...]
uv run --project parser python -m litrag_parser.report  --review <dir> --measure <dir>
```

`summary` puts the corpus in tables and `report` writes the page over both — the argument, with every figure,
including the ones inside its prose, read out of the measurement run rather than typed. A measurement that was
never run leaves no section behind instead of a row of zeroes that read as a result.

Nothing is written to a library: the trees are rebuilt in memory the way `rebuild` reads them, and the pages
are drawn from the paper's own file. One paper that fails is reported and the run goes on.

## Rules first, then meaning

Every question of resemblance (a heading's lane, a section's canonical
name, a line of front matter, a figure's legend, a reference entry) goes to
one oracle, `meaning.py`: nomic-embed-text through Ollama on 127.0.0.1,
each question a *kind* with its examples or centroids, a threshold and a
margin. Rules answer first; the oracle answers only where they are silent,
and below its threshold or margin its answer is `other`. Its verdicts are
rows in `<root>/lanes.sqlite`, replayed by a rebuild, and editing a kind's
examples, threshold, margin or centroids makes it ask again once.

- `lanes.py` only configures the oracle and asks the heading question. Its
  name, and the name of `lanes.sqlite`, are history: a new question is a
  new kind in `meaning.py`, not a new module or a new pattern list.
- `data/block_lanes.json` and `data/headings.json` are generated from the
  libraries and committed. Regenerate them rather than editing them:
  `python -m litrag_parser.meaning --make-centroids`, and
  `python -m litrag_parser.headings --harvest`, then `--make-centroids`.
- A kind, or a rule of the paper's type, decides only after it was measured
  on the libraries (`--measure`, the harness); `NOTES.md` keeps the numbers.

## Switches

| Variable | Default | What it does | Status |
|---|---|---|---|
| `LITRAG_ROOT` | `$PROTRACKER_LIBRARY`, else `~/.protracker/library` | where the libraries and `lanes.sqlite` live | current |
| `LITRAG_LANES` | on | `off` runs with no oracle: whatever the rules do not name is `other` | current; the tests turn it off |
| `LITRAG_LANES_MODEL` | `nomic-embed-text` | the embedder | current |
| `LITRAG_OLLAMA_URL` | `http://127.0.0.1:11434` | where the embedder and the judge are asked; keep it local | current |
| `LITRAG_JUDGE`, `LITRAG_JUDGE_MODEL` | off, `qwen3:14b` | `1` asks the judge on every ingest and reparse | opt-in |
| `LITRAG_BOUNDARY`, `LITRAG_BOUNDARY_MODEL` | off, `Qwen/Qwen2.5-0.5B` | `on` scores the page breaks the rules leave open | opt-in; below its gate |
| `LITRAG_EDGES_SIMILARITY` | off | `on` lets resemblance link a finding to a method where no pointer or mark does | opt-in |
| `LITRAG_TYPE_PROFILE` | off | `on` lets the profile kind name a paper's type | opt-in; measured at 0.66 accuracy |
| `LITRAG_VOCABULARY` | on | `off` names headings by the embedder alone | experiment, to measure what the vocabulary is worth |
| `LITRAG_PARSER` | the repository's `parser/` | the command the window starts as its worker | current |
| `LITRAG_HEADLESS`, `LITRAG_E2E_PAPERS`, `LITRAG_E2E_MIN_METHODS`, `LITRAG_E2E_MIN_TITLES` | | the end-to-end suite | tests |
| `LITRAG_PT` | `pt` | Protracker's command | the deprecated CLI only |

## Checking a change

```
npm run check:all                         before every push
LITRAG_HEADLESS=1 npm run e2e             the real window and worker on the fixture
npm run harness -- --lib <library> --json <outside the repo>/before.json
npm run harness -- --lib <library> --baseline <outside the repo>/before.json --gate
```

Run the harness on the pilot libraries and on libraries of papers no rule
was written from, and read its summary lines against the baseline as well
as the gate's exit code. Where a library has a companion holding the same
papers in the other format, run `pairs` too: it is the only check that says
whether the text landed under the right lane. The libraries, the papers and the harness's output
stay outside the repository.
