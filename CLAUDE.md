# litrag — repo guide for agents

One library of papers per project, built and asked from the command line.
`DESIGN.md` records the decisions; `AGENT.md` is how an assistant drives it,
and its conduct is binding; `NOTES.md` is the assistant's own notebook —
read it before talking to the user about their libraries, and keep it
current; `BACKLOG.md` is where wants wait; `README.md` is the front door.
The research context itself lives in Protracker's notebook.

Invariants:

1. **Local.** Paper text goes to `127.0.0.1` (Ollama) and nowhere else. The
   only network calls are Europe PMC's: search, full text, terms, references.
2. **Node only.** No Python, no native build steps beyond what npm installs.
   SQLite is `node:sqlite`; PDFs are `pdfjs-dist`; CPU embeddings are
   `@huggingface/transformers`.
3. **Rows first.** Everything the pipeline learns is a row a person can
   `SELECT`; vectors and the graph are built from the rows, never the other
   way round. `lit sql` is a first-class read.
4. **Idempotent.** A paper is filed once (DOI, then PMID, then file hash);
   running any stage twice changes nothing. `--reread` is the way to read
   everything again.
5. **Every verb answers in JSON** with `--json`, and the human form is for
   people. A stage reports progress line by line on stderr.

Verify before you push: `npm run check` (typecheck, then the unit tests —
fixtures from Europe PMC and a fake Ollama; nothing touches the network or a
GPU). The libraries live outside the repository (`LITRAG_ROOT`, default
`~/.protracker/library`) and never enter it.
