# Driving litrag as an assistant

This is the guide for an agent that operates litrag on someone's behalf — a
research assistant that keeps a library of papers per project, grows it when
a paper lands, and answers bench questions from it with citations. It is
written the way Protracker's `AGENT.md` is, and for the same reader: an
agent arriving cold, needing the verbs, the shapes it will reason over, and
the conduct expected around a scientist's library.

The library is the user's. The papers in it were collected under their
institutional access; nothing in them leaves the machine, and nothing about
them is asserted that a passage does not support.

Sections 1 to 5 describe the `lit` CLI of revision 1, which is deprecated;
section 6, how to behave, binds whatever you drive; section 8 is the current
pipeline, the worker and the window. `PIPELINE.md` says which code is which.

## 1. Invoking the CLI

After `npm install && npm link` the binary is `lit`. From a checkout without
linking:

```
npm run lit -- <args>
node --experimental-transform-types --no-warnings src/cli.ts <args>
```

Every invocation is `lit [--root DIR] [--json] <command> [args]`.

- **`--root DIR`** — where the libraries live. Defaults to `$LITRAG_ROOT`,
  then `$PROTRACKER_LIBRARY`, then `~/.protracker/library` — the same place
  Protracker's app will look, so the two agree with no setup. `lit where`
  prints the one in use; check it once before the first write.
- **`--json`** — structured output on stdout, for every read and for every
  write's report. Errors under `--json` are `{ "ok": false, "code": "...",
  "message": "..." }` with exit code 1. Prefer `--json` for anything you
  will reason over; the human-readable form is for people.
- **Progress goes to stderr**, one line per paper or batch, so a long
  `ingest` or `extract` can be watched without polluting the JSON.
- **`lit help`** is the whole page; `lit <verb> --help` is not — read the
  page once.

A `<lib>` argument is a library id (`looped-ligament`), the vault project id
it serves (`n156`), or its name (`"Looped Ligament"`), case-insensitively.

## 2. The model in one screen

```
Library › Paper › Section › Chunk        (+ Entity, mentioned by chunks)
```

- A **library** is a folder under the root: `library.json` (id, name,
  project id, includes, saved queries, backends), `lit.sqlite`, `papers/`
  (what was fetched), `inbox/` (what a person dropped in). One per project;
  `includes` names other libraries searched alongside — the shared spine.
- A **paper** is filed once, under a **key**: `doi:<doi>`, else
  `pmid:<n>`, else `pmcid:<PMC…>`, else `sha:<hash>` for a PDF with no DOI
  on its page. Seeing it again fills gaps and never moves it backwards.
- A paper's **status** is the pipeline's state machine: `candidate`
  (staged by a search) → `fetched` (full text on disk) → `ingested` (read
  into rows). A candidate with no open-access text becomes `needs-pdf`
  until a PDF arrives through the inbox. `extracted_with` stamps the model
  that read it; `annotated_at` says Europe PMC's terms are in.
- **Sections** keep the paper's own headings, nested ones joined with ` › `,
  and a **kind** derived from the heading: abstract, introduction, methods,
  results, discussion, other. **Chunks** are ~250 words cut at sentences,
  each remembering its section and page.
- **Rows** are what the pipeline learned: `parameters` (every value with a
  unit, from the miner; with `entity` named when the model stage read it),
  `claims`, `materials`, `methods` (the model stage), `refs` (what the paper
  cites, matched to library papers when it can), `entities` and
  `mentions` (the graph: Europe PMC's text-mined terms, and the model
  stage's materials and methods, pinned to the chunks that name them).
- **Vectors** are keyed by the model that made them; changing the embedding
  backend means the next `ingest` embeds everything again. Nothing is lost.

## 3. Verbs by intent

**Make and see**
```
lit libraries                                  every library, its project, counts by status
lit init "Looped Ligament" [--query Q]... [--include LIB]...
                                               ties to the vault project when pt is on the PATH
lit init "Reading list" --no-project           a library of its own
lit status <lib>                               counts by stage; the model stage; the graph
lit papers <lib>                               one line per paper: status, year, type, key, title
lit where | lit doctor [<lib>]                 the root; the model cache; Ollama and its models
```

**Grow it**
```
lit search <lib> "<query>" [--since YEAR] [--limit N]   stage candidates from Europe PMC; the query is saved
lit add <lib> <doi | pmid | pmcid | file.pdf>          one paper by hand; a PDF goes to the inbox
lit fetch <lib>                                        open-access full text; the rest become needs-pdf
lit ingest <lib> [--reread]                            read fetched papers and the inbox; embed
lit annotate <lib>                                     entity nodes from Europe PMC's terms
lit extract <lib> [--limit N]                          the model stage, through Ollama; resumable
lit refresh <lib>                                      the saved searches, then fetch, ingest, annotate, extract
lit snowball <lib> <paper-key>                         stage what a paper cites
lit wanted <lib> [--csv FILE]                          the PDFs to collect, most-cited first, with links
```

**Ask it**
```
lit query <lib> "<question>" [--limit N] [--no-spine] [--no-graph] [--trace]
lit sql <lib> "select …" [--limit N]                   one read-only SELECT
lit entities <lib> [--kind K] [--limit N]              what it knows the names of
lit graph <lib>                                        nodes, edges, the entities that span most papers
```

**Backends**
```
lit config <lib> --extract ollama --embed ollama [--ollama-url U] [--ollama-chat M] [--ollama-embed M]
```
The default is the CPU embedder and the miner alone: a machine without a
GPU gets a working library. The GPU adds the model stage's rows on top.

Europe PMC's query syntax works as typed — quotes for phrases, `AND`/`OR`,
`PUB_YEAR:[2015 TO 3000]` — and `--since` writes the year clause for you.

## 4. Reading with `--json`

The shapes you will reason over most:

- `libraries`: `[{ id, name, projectId, projectRef, includes[], queries[],
  papers { candidate, fetched, needs-pdf, ingested }, chunks }]`.
- `status`: `{ library (the manifest), papers { … }, chunks, vectors,
  parameters, claims, materials, methods, extracted, annotated, entities,
  mentions, needsPdf[{ key, title, year, doi }] }`.
- `query`: `[{ library, chunk, paper, title, year, journal, doi, section,
  kind, page, text, score, ranks { words?, meaning?, graph? }, citation }]`
  — `ranks` says which of the three rankings held the chunk and where; with
  `--trace` the answer is `{ hits[], trace { seeds { <library>: [names] } } }`.
  `citation` is the string to cite: title, year, journal, DOI, section, page.
- `wanted`: `[{ key, year, title, journal, doi, pmid, cited_by_count,
  pub_type, link }]`, most-cited first.
- `papers`: every column of the `papers` table, including `status`,
  `pub_type` ("research-article; journal article" / "review-article; …"),
  `extracted_with`, `annotated_at`.
- `search`: `{ ok, hits, staged, papers[] }`; `fetch`: `{ ok, fetched[],
  needsPdf[] }`; `ingest`: `{ ok, inbox[], ingested[], failed[{ key, error }],
  embedded }`; `annotate`: `{ ok, annotated[], skipped[], mentions }`;
  `extract`: `{ ok, extracted[], failed[], sections }`.
- `graph`: `{ papers, chunks, entities, hubsDropped, citationEdges,
  mentionEdges, topEntities[{ name, kind, papers }] }`.
- `entities`: `[{ name, kind, papers, mentions }]`; kinds are Europe PMC's
  (`chemicals`, `gene-proteins`, `organisms`, `experimental-methods`,
  `gene-ontology`, `diseases`) and the model stage's (`material`, `method`).
- `sql`: the rows, as objects. The tables: `papers`, `sections`, `chunks`,
  `chunks_fts`, `vectors`, `parameters`, `claims`, `materials`, `methods`,
  `entities`, `mentions`, `refs`, `queries`. `DESIGN.md` §3 has the columns.
- `doctor`: `{ root, rootExists, modelCache, library?, ollama? { reachable,
  version, models[], missing[] } }`.

## 5. Things that look like bugs and are not

- **A candidate's title from a search is the search's title.** The paper's
  own title, authors and abstract arrive with its full text; a search hit
  with markup in the title is cleaned, and a placeholder title gives way
  to the paper's own at ingest.
- **`needs-pdf` is not a failure.** It is the tool saying Europe PMC has
  no open-access full text; `lit wanted` is the list, the inbox is the
  door. A paper that came through the inbox with no DOI on its page is
  filed by its bytes and reads as `Untitled (<file>)` until you rename it.
- **`ingest` reports `failed` for a file it could not read** and leaves the
  paper `fetched`, so one bad PDF never stops the rest. The error names
  the file.
- **The first `ingest` on a machine downloads the CPU embedding model**
  (~34 MB, once, into `<root>/models/`). The first `query` on a fresh
  process loads it (about a second).
- **`extract` refuses until the library says `--extract ollama`**, and
  says so; `lit doctor <lib>` says whether Ollama is reachable and which
  models it is missing. `refresh` skips the model stage quietly when Ollama
  is not there and says so on stderr.
- **A `query` with no graph seeds is normal.** The walk starts from the
  nearest passages instead; `--trace` says "none named". Entities named in
  more than 60% of the library's papers are left out of the graph on
  purpose ("collagen" in a collagen library ranks nothing).
- **Vectors disappear after `lit config --embed …`** — they are keyed by
  model; the next `ingest` remakes them for the new one, and says how many.
- **`sql` refuses anything but one SELECT**, and a semicolon inside a
  string literal is fine.
- **`refresh` re-runs every saved search at limit 50**, so a broad query
  keeps pulling in broad papers. Prune `queries` in `library.json`, or
  say so to the user; the query set is theirs.

## 6. How to behave as the assistant

- **The library speaks, not your training.** Every claim in an answer from
  the literature cites a passage `query` returned, or a row `sql` returned.
  A fact you know from elsewhere is said as that — "not in the library,
  but…" — never dressed as a finding.
- **Name a gap as a gap, with the verb that would close it.** Off-topic
  top hits mean "the library does not speak to this"; then say why — the
  papers that would are on `lit wanted` (name them), or no search has been
  run for the topic — and offer `search`, `snowball`, or a PDF for the
  inbox. Run it on the user's word.
- **Read freely; grow on the user's word.** `query`, `sql`, `status`,
  `papers`, `wanted`, `entities`, `graph`, `doctor` are reads. `search`
  stages, `fetch` and `ingest` read papers in, `annotate` and `extract`
  make rows — none of those unasked, and never `init`, `config`, or a
  change to `library.json`.
- **Say what a search pulled in before fetching it.** A query that returns
  off-topic reviews is worth a sentence; forty irrelevant papers in a
  library make every later answer worse.
- **Quote values in the paper's words.** When a number is the answer, give
  the `sentence` the row came from, with its citation, rather than the
  number alone; a concentration without its buffer and time is half a fact.
- **Paper text stays on the machine.** The model stage and Ollama
  embeddings talk to `127.0.0.1`; the assistant sees passages and rows,
  never files, and sends neither anywhere.
- **The user's notebook is the user's voice.** What the literature says
  goes into conversation, or into the research record when the user says
  so — never into a journal as if the user had said it.
- **Ambiguity that changes the library is a question, not a guess.** "Add
  the Akkus papers" is a search, a snowball, or a list of DOIs; ask which,
  or say which you chose in the same breath as the result.

## 8. The worker and the window (revision 2)

Since 2026-09-11 the parsing lives in `parser/` (Python, Docling) and the
window in `app/` (Electron). An assistant on the machine can drive the
worker directly, the way it drives `lit`:

```
uv run --project parser litrag-parser [--root=DIR]
```

One JSON object per line on stdin; events on stdout, each carrying the
request's `id`. Reads answer at once; `ingest`, `reparse` and `rebuild` are
queued (`queued` comes back immediately, then the stream, then `done`).

| op | params | answers with |
|---|---|---|
| `hello` | | `hello`: worker version, root, python, docling (null until first use), device, `meaning` (the oracle's summary: per-kind asked/named, `down`, `error`; null with `LITRAG_LANES=off`), `boundary` (the scorer's summary once one was built; null otherwise) |
| `libraries` | | `libraries`: `[{id, name, dir, projectId}]` |
| `init` | `name`, `projectId?` | `library` |
| `ingest` | `lib`, `paths[]`, `reread?`, `offline?` | `paper` per file (`existed`, `kept`, `doi`, `file`, `status`), then per paper `stage` (`opening` · `models` · `layout` · `recover` · `tree` · `saved` \| `failed`), `working` heartbeats, `log` lines, a `tree` summary (`type`: `{type, source, detail}`, `roles`, `has_methods`, `nodes`, `pages`, `seconds`, `repairs`, `dropped`, `notes`, `meaning`, `edges`: findings, linked, unlinked and the count per evidence kind), then `done`. A PDF with no DOI or PMCID on its first pages is asked for on Europe PMC by its title (`identified` event with `doi`/`pmid` when one record's title is the same words) before it gets a hash key; `offline: true` skips the call. With a DOI or PMID, Europe PMC's record comes once too — publication types, authors, journal, year — into `papers` (`record.py`); a JATS file's own contributor group, journal and year override it on every read and rebuild |
| `reparse` | `lib`, `keys?` | the same stream; every paper when `keys` is absent |
| `rebuild` | `lib`, `keys?` | rows again from `parsed/*.docling.json` without Docling — the text layer read back (`recover.py`), cached judgments reused, the model never asked; `tree` per paper (with `repairs`: `joined`, `rejoined`, `rejoined_meaning`, `reordered`, `caption_tail`, `stitched`, `deduplicated`, `formula_text`, `junk`, `judged`, `glyphs`, `inferred_references`, `recovered`, `rebuilt`, `formulas`, `attached`, `front_meaning`, `label_veto`, `captions_meaning`, `laned`, `built_headings`, `lane_disagreement`; `dropped`: `furniture`, `picture`, `junk`, `label`, `empty`; `judged`), `done`. A formula node whose text is "[equation as image: …]" marks an equation the JATS carries only as a picture; the audit grades it `image-formula`, a warning |
| *(meaning)* | | Not an op: every question of resemblance goes to one oracle (`meaning.py`, nomic-embed-text through Ollama), each a *kind* with example texts, a threshold and a margin — `heading` (the lane a heading names: cosine ≥ 0.75, margin ≥ 0.08), `front` (what a line of front matter is), `label` (a run-in label the list does not know; vetoes a geometry join), `figtext` (a figure's legend; adds, never removes), `refentry` (a reference entry in a shape no pattern knows), `which` (which open paragraph a tail belongs to), `block` (a section's lane from its paragraphs, by centroids in `data/block_lanes.json` plus a position prior; only methods/results/references, only for a heading that named nothing; a paper with no heading gets `built` headings). Every answer below threshold is `other`. Verdicts are rows in `<root>/lanes.sqlite` (`verdicts`: `kind, key, model, name, score, margin, at`; the model string hashes the kind's examples and thresholds, so an edit re-asks), so `rebuild` needs no model and a wrong answer is a row to delete. `LITRAG_LANES=off` turns it off; with Ollama down, unknown texts are `other`, not stored, and the worker emits a `meaning` stage saying so. `LITRAG_VOCABULARY=off` is an experiment, never a default: the vocabulary names no heading and the embedder names every one alone (kind `heading-alone`, abstract and references included, wider margins on those two). The `tree` event carries `meaning` (per-kind asked/named, down, error) and `notes` (a count; the notes themselves are `events` rows: `lane-disagreement`, `lane-suggested`). `abstract` and `references` are never named by meaning from a heading |
| *(boundary)* | | Not an op: where the rules and the page leave a pair of blocks open, `boundary.py` scores whether B continues A by likelihood (Qwen2.5-0.5B through transformers, CPU, fetched once from Hugging Face): mean log p(first 24 tokens of B \| last 500 chars of A) − log p(B \| nothing) ≥ 1.5 nats/token is a join. Verdicts are `judgments` rows (`model = boundary:<model>@<threshold>`), replayed by `rebuild`. Off by default (`LITRAG_BOUNDARY=on` turns it on; `LITRAG_BOUNDARY_MODEL` picks the model): the synthetic gate passes (precision 0.92, recall 0.92 paper-out at 1.05; 0.97 and 0.71 at the shipped 1.5) but on the real leftover pairs one join in fifteen would be wrong. When on, it is asked before the generative judge; `python -m litrag_parser.boundary --calibrate --lib … [--synthetic 600]` reports the numbers |
| *(type)* | | Not an op: `paper_type.py` decides what kind of paper each is — research, review, case-report, letter, editorial, protocol, data, correction, other — and its subtype (rct, systematic-review, case-series, brief-report, …) from one table over every source's vocabulary: Europe PMC's publication types, the JATS `article-type` and `<subject>`, the title, the printed label; the most trusted specific label decides (record › file › subject › title › page), a default bucket ("research-article", "Journal Article") never decides alone, the shape decides only where measured (reviews, case reports, data descriptors; research when a default confirms it), and every disagreement is a `type-disagreement` note (an `events` row, an audit finding). `papers.type`, `subtype`, `type_source` (record · jats · subject · title · printed · shape · default · none), `type_detail`; every `tree` event carries `type`. `python -m litrag_parser.paper_type --fetch` stores the record, `--measure` scores every source against the stated labels |
| *(headings)* | | Not an op: `headings.py` gives every section the catalogue's canonical name beside its own heading (`nodes.canonical`: "Materials and methods", "Conclusions", "Conflicts of interest", …, thirty names), by the spellings the XML libraries use, by family, else by the embedder against centroids learned from those libraries (`data/headings.json`); an exact spelling of a top-level name settles a heading's depth and lane where the vocabulary was silent. `python -m litrag_parser.headings --harvest / --measure / --make-centroids` |
| `judge` | `lib`, `keys?` | `rebuild` with the local model reading every adjacent pair the rules leave (Ollama at `LITRAG_OLLAMA_URL`, model from the library's `ollama.chat`, `LITRAG_JUDGE_MODEL`, or qwen3:14b); verdicts land in `judgments`; `tree` per paper, `done`. `ingest` and `reparse` take `"judge": true` to do the same as each paper is read |
| `papers` | `lib` | `papers`: rows of `papers` with a `nodes` count — among the columns `type`, `subtype` and `type_source` (record · jats · subject · title · printed · shape · default · none), `pub_types`, `authors` (JSON `[{name, affiliations, corresponding}]`), `journal`, `year` |
| `tree` | `lib`, `key` | `tree`: `paper`, `pages`, `roles`, nested `root` |
| `node` | `lib`, `node_id`, `siblings?` | `node` |
| `section` | `lib`, `key`, `role` | `section`: every node in that lane, reading order |
| `events` | `lib`, `key` | `events`: the paper's stage history |
| `sql` | `lib`, `sql`, `limit?` | `rows`: `columns`, `rows` — one SELECT, read-only |
| `file` | `lib`, `key` | `file`: path of the paper and of its raw Docling document |
| `parse_json` | `path`, `key?` | `tree` from a saved Docling document, no models |
| `refs` | `lib`, `key` | `refs`: the paper's reference list — `ref_no`, `node_id`, `ref_id`, `text`, `doi`, `pmid`, `year`, `first_author`, `title`, and `cited_by` (node ids). A `tree`'s nodes carry `cites` (ref numbers) and an entry its `ref_no`; the tables are `refs` and `citations` |
| | | Every `tree` event carries `repairs` — `recovered`, `rebuilt`, `formulas`, `attached` (the text layer read back, recover.py), `joined`, `rejoined`, `reordered` (a first page the layout model read out of order: the introduction's heading and first lines led back to the body), `stitched`, `deduplicated`, `judged` — and `dropped` — `furniture`, `picture`, `label`, `junk` — so what the reader put back or left out is visible per paper |
| `edges` | `lib`, `node_id` | `edges`: the node's edges both ways, each with the other node's id, type, role, heading, ancestry, page and first 200 characters — `out` (a finding's `measured_by` method subsections; the figures a paragraph `cites_figure`) and `in` (the findings measured under a method; the paragraphs citing a figure). Rows of `edges` (`paper, src, dst, kind, evidence, detail, score`), written at ingest and rebuild by `edges.py`: a finding (a results paragraph, or a discussion paragraph that cites a figure) is linked to a methods subsection by a `pointer` in its text ("see Section 2.3"), else by `terms` only that subsection owns (its marks: a heading word its body uses, a word pair it repeats, or a word pair the paper says in four blocks or fewer, none of them one of the paper's commonplaces), else through the `caption` of the figure it cites, else by `similarity` from the oracle when the nearest clearly beats the next — that last only with `LITRAG_EDGES_SIMILARITY=on`, since it agreed with pointers 3 times in 7; a finding no evidence places stays unlinked, and the reply carries `candidates` (how many method parts the paper offers) so the window can say why. `python -m litrag_parser.edges --measure --lib …` reports how often terms and similarity agree with the pointers when the pointers are hidden |
| `audit` | `lib`, `key?` — or `path` | `audit`: per paper, every node that reads wrong next to its neighbours (`findings`: kind, severity, node_id, message, text), `counts` by kind, and `dropped` items — see `parser/litrag_parser/audit.py`; `python -m litrag_parser.audit --lib <dir>` prints the same |
| `quit` | | `bye` |

Errors are `{"event":"error","id":…,"message":…}`. A `<lib>` is the
library id, its name, or its Protracker project id. The store is
`<root>/<lib>/store.sqlite`; `nodes` is the table to read — `role`, `type`,
`heading`, `canonical` (the catalogue's name for a section, or NULL), `ancestry` (JSON), `text`, `page`, `bbox_*`, `table_json` — and
`nodes_fts` matches words in it. A node's `type` is one of section,
paragraph, list_item, table, picture, caption, formula, code, footnote, or
`meta` — the front matter's typed lines, whose `label` says which: authors,
affiliations, dates, correspondence, keywords, notice, other. `refs` holds the reference list per paper
and `citations` which node names which entry; join them to `nodes` to ask
what a chunk leans on, or which chunks lean on one paper.

Conduct is unchanged: the library is the user's; a node's text is cited by
paper, section and page; a lane that reads `other` or a section titled
"(heading not detected)" is reported as the gap it is, not papered over.
