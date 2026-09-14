# litrag — repo guide for agents

One library of papers per project. A desktop app watches each paper being
read into a tree; a Python worker does the reading and owns the store; the
`lit` CLI in `src/` is the earlier retrieval loop, kept until the app has
replaced its verbs. `DESIGN.md` records the decisions; `AGENT.md` is how an
assistant drives it, and its conduct is binding; `NOTES.md` is the
assistant's own notebook — read it before talking to the user about their
libraries, and keep it current; `BACKLOG.md` is where wants wait;
`README.md` is the front door. The research context itself lives in
Protracker's notebook.

Where things are:

- `parser/` — Python, managed with `uv`. Docling reads PDFs and JATS XML;
  `tree.py` turns a Docling document into the node tree; `store.py` is the
  SQLite schema and its reads; `worker.py` speaks JSON lines over stdio.
- `app/` — Electron, TypeScript, built with esbuild. `src/main` spawns the
  worker and relays its events; `src/renderer` is the window: papers,
  tree, page with boxes, log.
- `src/`, `tests/` — the `lit` CLI (Node): Europe PMC, chunks, embeddings,
  hybrid retrieval. Not yet wired to the tree store.

Invariants:

1. **Local.** Paper text stays on the machine. Docling's models, and the
   small language model the boundary scorer uses, are fetched once from
   Hugging Face and run here; the embedder and the judge are Ollama on
   127.0.0.1; the only other network calls are Europe PMC's. Nothing reads a
   paper for a cloud service.
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
run of the app is `npm run app`; a headless one is `app/tests/smoke.mjs`.
The libraries live outside the repository (`LITRAG_ROOT`, default
`~/.protracker/library`) and never enter it.
