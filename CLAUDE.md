# litrag — repo guide for agents

Papers are kept once, in a shared store; a project is the set of papers
it holds. A desktop app watches each paper being
read into a tree; a Python worker does the reading and owns the store; the
`lit` CLI in `src/` is the earlier retrieval loop, kept until the app has
replaced its verbs. `PIPELINE.md` is the pipeline on one page and says
which code is current and which is deprecated; `DESIGN.md` records the
decisions; `AGENT.md` is how an assistant drives it, and its conduct is
binding; `NOTES.md` is the
assistant's own notebook — read it before talking to the user about their
libraries, and keep it current; `BACKLOG.md` is where wants wait;
`README.md` is the front door. The research context itself lives in
Protracker's notebook.

Where things are:

- `parser/` — Python, managed with `uv`. Docling reads PDFs and JATS XML;
  `tree.py` turns a Docling document into the node tree; `store.py` is the
  SQLite schema and its reads; `members.py` is which projects hold which
  papers in the shared store; `migrate.py` moves a library that still keeps
  its own store into it; `worker.py` speaks JSON lines over stdio.
- `app/` — Electron, TypeScript, built with esbuild. `src/main` spawns the
  worker and relays its events; `src/renderer` is the window: eight tabs
  over one project — Projects, Search, Papers (tree, page with boxes,
  canonical face), Types, Ask (cited answers from the local model), Query,
  Graph (citations, rounds, SQL), Plots (figures found by caption, one
  digitised by a person, a sigmoid fitted: `digitise.py`, `fit.py`) — and
  the log.
- The shared store is `<root>/_shared`: a project is its `project_papers`
  rows, and every worker read of "the library" is narrowed with
  `members.scope` (DESIGN.md R4.6). Tests make old-shape libraries with
  `create_library(...)`; the worker's `init` makes shared ones.
- Accounts and each person's recent papers, favourites, notes and collections
  are `<root>/users.sqlite` (`users.py`), reached only through the worker's
  account ops with a `session` (`app/src/renderer/account.ts`); they tell
  people apart and are not a security boundary (DESIGN.md R4.7).
- `src/`, `tests/` — the `lit` CLI (Node): Europe PMC, chunks, embeddings,
  hybrid retrieval. Deprecated, and not wired to the tree store but for
  `lit ask`, which sends the worker one `ask`; kept until its verbs are
  ported to it (`BACKLOG.md`).

Invariants:

1. **Local.** Paper text stays on the machine. Docling's models, and the
   small language model the boundary scorer uses, are fetched once from
   Hugging Face and run here; the embedder, the judge and the answerer
   (`answer.py`) are Ollama on 127.0.0.1; the only other network calls are Europe PMC's and NCBI's
   (PMC's XML by PMCID, for the author manuscripts Europe PMC will not
   serve: an identifier out, the article in — Karim, 2026-09-30), and
   NLM's PMC Cloud Service (`pmc-oa-opendata.s3.amazonaws.com`: an
   open-access paper's PDF by PMCID, the same way — Karim, 2026-10-04),
   and OpenAlex (`api.openalex.org`, for citation rounds beside Europe PMC:
   identifiers out, and for a reference entry naming none its own words,
   never a paper's text — Karim, 2026-10-04), and the host of an open copy
   OpenAlex names (a publisher's or a repository's: the request for that
   PDF and nothing else, a bot check left to a person — Karim, 2026-10-04).
   For an XML paper's figure images: the PMC Cloud Service and Europe PMC
   (the image files its XML names, by PMCID), OpenAlex (which open copy of
   the paper there is, to cut its figures from: its DOI out, one request at
   a time, paced), and that open copy's host (the request for that PDF and
   nothing else).
   Nothing reads a paper for a cloud service.
2. **Two languages, one wire.** Python owns parsing and the store; TypeScript
   owns the window. They meet only on the JSON-lines protocol in
   `parser/litrag_parser/worker.py` and `app/src/main/protocol.ts`. No
   Python in the renderer, no SQLite writes from Electron.
3. **Rows first.** Every node a paper is read into is a row a person can
   `SELECT` (`nodes`, `pages`, `papers`, `events`). The raw Docling document
   is kept beside the store, immutable; the rows are derived from it, and
   `rebuild` derives them again without running Docling.
4. **Idempotent.** A paper is filed once — DOI, then PMID, then file hash —
   and a paper already read stays as it was read; `reread` is the way to
   replace it. Running any stage twice changes nothing.
5. **Unassignable beats misassigned.** A node's role comes from the
   top-level heading above it: the vocabulary in `facets.py` names it
   exactly, the embedder in `meaning.py` names it by resemblance, and a
   heading near nothing is `other`, never a guess. Every question of
   resemblance the reader asks (a heading's lane, a line's kind of front
   matter, a figure's legend, a reference entry, which paragraph a tail
   belongs to, what a section's paragraphs are) is answered the same way:
   named only when the nearest example is near enough and clearly nearer
   than the next, `other` otherwise, and every answer a row that a rebuild
   replays. A verdict adds; it never overrides a rule that fired, never drops
   text, and never files prose under a heading it does not belong to. A
   heading the reader built from a paper that printed none is labelled
   `built`, never passed off as the author's. A dropped heading becomes a
   visibly untitled section, never a silent merge.
6. **Every answer is JSON,** and progress streams as events while a paper is
   read, so the window shows what is happening rather than a spinner.

Verify before you push: `npm run check:all` — the CLI's typecheck and tests,
the app's typecheck, tests and build, and the parser's pytest (fixtures are
saved Docling documents; nothing touches the network or the models). A real
run of the app is `npm run app`; a headless one is `app/tests/smoke.mjs`;
the product end to end is `LITRAG_HEADLESS=1 npm run e2e:studio` (Docling's
models, Ollama, and two PDFs from the archive beside the checkout); the Plots
tab end to end, without models, is `npx playwright test digitise.spec.ts` in
`app/`. A request's `id` is the wire's own: an op naming a row takes another key.
The libraries live outside the repository (`LITRAG_ROOT`, default
`~/.protracker/library`) and never enter it.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
