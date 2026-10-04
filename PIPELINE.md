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
`uv run --project parser --no-sync python -m …`: `--no-sync` keeps the
torch the environment was synced with, which a `uv run` without that
sync's `--extra` would swap for PyPI's (README, Quick start).

## What to use

| Part | Status | Use it for |
|---|---|---|
| `app/`, the window (`npm run app`) | current | five tabs over one project at a time: **Projects** (make one, describe it, merge several), **Search** (Europe PMC, XML first — Europe PMC's, else NCBI's for an author manuscript — then open PDFs, the rest listed to download by hand), **Papers** (trees, pages, citations, edges, the canonical face; **Reparse all**), **Types** (each kind of paper's canonical structure, a paper's mapping onto it), **Query** (passages retrieved and hydrated from the tree) |
| `npm run e2e:studio` | current | the window end to end: a search against Europe PMC stood in on 127.0.0.1, a fetch, two real PDFs through Docling, types, a hydrated query through the embedder, a merge — the harness a change to the product has to pass |
| `npm run gate:ingestion -- --pairs A:B … [--min 0.95]`, `python -m litrag_parser.bench` | measurement | the reader judged in the unit retrieval returns (a paragraph node in the right lane, against the XML twin); retrieval judged on questions with a known answering passage, the tree beside the `lit` CLI |
| `parser/`, the worker (`uv run --project parser --no-sync litrag-parser`) | current | what the window does, and the ops it has no button for, among them `rebuild`, `judge`, `audit` and `sql` |
| `npm run harness`, `npm run audit` | current | judging a change to the reader on whole libraries |
| `python -m litrag_parser.headings`, `.meaning`, `.paper_type`, `.edges`, `.judge`, `.boundary` | maintenance | regenerating the shipped centroids, measuring a kind or the type before it decides, fetching Europe PMC records, running or calibrating the opt-in judges |
| `python -m litrag_parser.pairs`, `.confidence --calibrate` | measurement | a PDF's reading against the XML's of the same paper, from two libraries; the confidence score against those pairs. The truth every change to the reader or to the score is judged on |
| `python -m litrag_parser.truth --lib DIR --measure`, `.labeller --lib DIR`, `.lineage --lib DIR` | measurement | the finding→method links against the labels a person gave in **Label links** (precision per evidence, recall, misses, false links, the paragraph hydration shows against the method's first), and against the local model's once a person's audit of them agrees; the model labelling; the methods a library says are described elsewhere, and how many of the cited papers it holds |
| `python -m litrag_parser.outline --pdf-lib DIR --xml-lib DIR --model M` | measurement | the outline judge (a local model reading the whole paper) scored on the pairs: faithful before and after, per model |
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
   step that runs Docling, and the only one that can take the process down
   with it, so it runs in a child the worker supervises (`layout.py`): the
   child is long-lived because Docling takes seconds to build, a paper that
   kills it is retried once in a fresh child, and a paper that kills two
   fails with a reason instead of ending the run.
3. **Recovered.** For a PDF, the text layer is read back for the lines the
   layout model missed (`recover.py`), and its type for the headings it
   missed: run-in, fused into a paragraph, dropped, or misspelt, and every
   heading's depth as the page sets it (`typography.py`). Both are
   deterministic and run again on `rebuild`.
4. **Built.** `tree.py` turns the document into nodes (front matter,
   sections, paragraphs, tables, figures and captions, each with its page
   and box) and undoes what the fonts did to symbols (`glyphs.py`). Front
   matter is not only what stands before the body: a line whose shape is
   unmistakably the publisher's — a date line, the licence, an imprint, an
   editor's name, an author list — is front matter on the first two pages
   wherever the layout model read it, because journals set these down the
   margin and the model reads them between the body's paragraphs. A
   top-level heading's lane comes from the vocabulary (`facets.py`), else
   the catalogue (`headings.py`, which also gives the section its
   `nodes.canonical` name), else the embedder (`meaning.py`). A paper that
   printed no headings gets built ones, labelled `built` (`structure.py`).
   A page break the rules cannot join goes to the judge only when asked
   (`judge.py`; `boundary.py` when switched on). With `LITRAG_OUTLINE=on`
   a local model then reads the whole paper and says where its sections
   are (`outline.py`): a lane where the rules gave none, a built heading
   where the reader missed a boundary (its lane by the reader's rule, not
   the model's), never the depth; the answer is a row a rebuild replays.
5. **Linked.** In-text citations to the reference list (`citations.py`),
   and findings to the methods that produced them (`edges.py`), each edge
   with its evidence and a strength. A query follows them both ways and,
   for a method "as previously described [14]", on to the cited paper's
   own method when the library holds it (`retrieve.py`, `lineage.py`).
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

`invariants.py` asks the same kind of question as `confidence.py` and answers
with a **place** rather than a share: thirteen checks (conservation, no text
twice, heading numbering, the reference list, citation markers, figures and
captions, the type's contract, lane order, paragraph integrity, furniture in
the body, geometry, style classes, headings) each returning pass, fail or
not-applicable, and a failure carrying one violation per node with its page
and its text. It is not in the pipeline: nothing calls it while a paper is
read, and nothing in the reader acts on it. Every check is `advisory` —
priced on 66 publishers the reader had never seen, one predicts a wrong lane
at four times chance and the rest are at or near it
(`campaign/reports/phase4.md`), which is not a precision a repair can act on.
`python -m litrag_parser.invariants --lib <dir> [--only I3,I9] [--json]`.

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
uv run --project parser --no-sync litrag-parser --root=$HOME/.protracker/library
{"id":"1","op":"rebuild","lib":"looped-ligament"}
{"id":"2","op":"quit"}
```

## Rules first, then meaning

Every question of resemblance (a heading's lane, a section's canonical
name, a line of front matter, a figure's legend, a reference entry) goes to
one oracle, `meaning.py`: nomic-embed-text through Ollama on 127.0.0.1,
each question a *kind* with its examples or centroids, a threshold and a
margin. Rules answer first; the oracle answers only where they are silent,
and below its threshold or margin its answer is `other`. Its verdicts are
rows in `<root>/lanes.sqlite`, replayed by a rebuild.

A row keeps the **ranking** — every group with its score — and not only the
verdict, and the cache is keyed on what determines that ranking (the examples
or centroids, the prefix, the prior, the embedder) and **not** on the
threshold and margin, which only decide what to do with it. So editing a
kind's examples or centroids makes it ask again once; editing a threshold or
a margin makes it decide again for nothing, in either direction. A row
written before rankings were stored is honest about it — a hit under the rule
that wrote it and a miss under any other — and `LITRAG_LANES_REFRESH=on` asks
again for such rows so they gain one. It is off by default because turning it
on re-embeds a whole library the next time its papers are read.

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
| `LITRAG_LANES_REFRESH` | off | ask the embedder again for a stored verdict that carries no ranking, so it gains one and a threshold can be tried against it. Re-embeds a whole library once | current; the campaign turns it on per corpus |
| `LITRAG_OLLAMA_URL` | `http://127.0.0.1:11434` | where the embedder and the judge are asked; keep it local | current |
| `LITRAG_JUDGE`, `LITRAG_JUDGE_MODEL` | off, `qwen3:14b` | `1` asks the judge on every ingest and reparse | opt-in |
| `LITRAG_OUTLINE`, `LITRAG_OUTLINE_MODEL` | off, `qwen3:14b` | `on` asks the outline judge on every PDF ingest and reparse, and replays its rows on rebuild | opt-in; measured on the pairs (NOTES.md) |
| `LITRAG_BOUNDARY`, `LITRAG_BOUNDARY_MODEL` | off, `Qwen/Qwen2.5-0.5B` | `on` scores the page breaks the rules leave open | opt-in; below its gate |
| `LITRAG_EDGES_SIMILARITY` | off | `on` lets resemblance link a finding to a method where no pointer or mark does | opt-in |
| `LITRAG_TYPE_PROFILE` | off | `on` lets the profile kind name a paper's type | opt-in; measured at 0.66 accuracy |
| `LITRAG_VOCABULARY` | on | `off` names headings by the embedder alone | experiment, to measure what the vocabulary is worth |
| `LITRAG_LAYOUT_CHILD`, `LITRAG_LAYOUT_TIMEOUT` | on, `300` | Docling's layout stage runs in a child process the worker supervises, so a native crash costs one paper and not the rest of the queue: a per-paper timeout, a respawn when the child dies, one retry in a fresh child, then the paper fails with a reason. `off` converts in the worker's own process, as before | current |
| `LITRAG_PARSER` | installed: the environment's `litrag-parser`; a checkout: `uv run --project <repo>/parser litrag-parser`, uv from PATH, else litrag's `uv/`, `~/.local/bin` or `~/.cargo/bin` | the command the window starts as its worker, in one of three forms: a JSON array of argv, the form for a path with spaces (`["C:\\Users\\Jane Doe\\AppData\\Local\\litrag\\venv\\Scripts\\litrag-parser.exe"]`); the path of an existing file, taken whole as one program; else a command line split on whitespace (`uv run --project /x/parser litrag-parser`), the old form, in which no part may hold a space. With nothing to run — no environment, no uv — the window says why in a band under its header and spawns nothing | current |
| `LITRAG_VENV` | `<install root>/venv` | the environment an installed app runs its worker from, `Scripts\litrag-parser.exe` on Windows and `bin/litrag-parser` elsewhere; uv is not run. The install root is `%LOCALAPPDATA%\litrag` on Windows, `$XDG_DATA_HOME/litrag` (else `~/.local/share/litrag`) on Linux, `~/Library/Application Support/litrag` on macOS | current |
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
