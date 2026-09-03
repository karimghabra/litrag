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
