# The assistant's notebook

litrag's own memory: what an assistant living with this repository needs to
remember between sessions about the libraries, the machine, and what was
tried. It ships with the repository so every session finds it. The research
context itself — the user's program, projects, vocabulary and people — lives
in Protracker's `NOTES.md`, the notebook of the tool this one serves; read
that one before talking to the user about their work, and keep this one to
the literature.

How to maintain it: long-term memory holds what stays true (the libraries,
their query sets, the models that worked, standing decisions); short-term
memory holds the current stretch, dated, overwritten freely, promoted upward
when it turns out durable. Mark inference as inference.

---

## Long-term memory

### The libraries

- **looped-ligament** (Protracker project `n156`) — the pilot. Seeded
  2026-09-02 with three Europe PMC searches: `"electrochemically aligned
  collagen"`, `(electrocompaction OR electrocompacted OR "electrochemical
  alignment") AND collagen`, and a broad tendon/ligament/delamination
  query that pulls in off-topic reviews and should be pruned. The
  open-access half is ~78 papers; the paywalled half (~47, `lit wanted`)
  is the ELAC canon — Akkus lab 2008–2019 — and is what the crosslinking
  and delamination questions actually need.

### What worked

- **Docling 2.126** on the CPU (4 cores, no GPU): ~15 s for an 8-page and a
  14-page PDF alike once the models are loaded (~3 s), ~0.5 GB of models
  fetched from Hugging Face on the first paper. JATS through the same
  converter: 20 ms, once a JATS DOCTYPE is prepended (Europe PMC's XML has
  none and Docling's detector keys on it). Docling gives every heading
  level 1 — `heading_hierarchy_options` changes nothing on these PDFs — so
  the hierarchy is the tree builder's: numbering, then the lane vocabulary,
  then "beneath an open section means child". Two real failures found on
  the first two papers and turned into rules: a top heading merged into
  the subheading below it as a list item, and a heading echoed across a
  page break.
- Electron's binary does not download through this cloud box's proxy
  unless `ELECTRON_GET_USE_PROXY=1 GLOBAL_AGENT_HTTPS_PROXY=$HTTPS_PROXY
  NODE_EXTRA_CA_CERTS=/root/.ccr/ca-bundle.crt` are set for `npm install`.
  Karim's machine needs none of that.

- Europe PMC's JATS full text needs no PDF parsing; its text-mined terms
  give the graph real entities (genipin, ethanol, carbodiimides, rabbit)
  at no cost. Both are one REST call per paper.
- `bge-small-en-v1.5` quantised on the CPU: ~4 minutes for 4,900 chunks on
  a slow cloud box; seconds on a GPU through Ollama.
- Hybrid retrieval with the graph as a third list: on the pilot it removed
  word-match false positives and left already-good answers alone. Modest
  until the model stage adds materials and methods as entities.

### Standing decisions

- **2026-09-11** — Karim is "not at all attached to the current
  implementation". More than one language is fine. What he wants first is
  an app with a GUI to *see* papers being ingested and trees being built:
  Electron, with Docling doing the parsing. Revision 2 of `DESIGN.md`.
  The `lit` CLI stays in `src/` untouched until the app has its verbs.

- **2026-09-03** — The assistant lives on the user's machine for the
  literature (Karim: "you can live as an agent on my machine and utilize
  the cli for the literature RAG"). Nothing is exported for cloud sessions.
- **2026-09-03** — litrag is its own repository; Protracker's copy of the
  code is to be retired. Libraries stay under `~/.protracker/library` so
  the app finds them.
- The click stays the user's: collecting paywalled PDFs is done by a person
  under institutional access; the tool catches files, never fetches behind
  a login.

## Short-term memory

- **2026-09-11** — Revision 2 built on branch
  `claude/tissue-engineering-literature-rag-srz9ja`: `parser/` (Docling
  worker, tree, store, 17 tests), `app/` (Electron window, protocol tests,
  headless smoke run), documents rewritten. Fixtures are the two real
  papers: NAR 2011 (PMC3258128, `doi:10.1093/nar/gkr715`) and Micromachines
  2024 (PMC11278924, `doi:10.3390/mi15070851`, the ELAC crosslinking paper,
  as PDF and as JATS). Not yet run on Karim's machine: `uv sync --project
  parser`, `npm --prefix app install`, `npm run app`, then thirty ELAC PDFs
  through the window to find the next layout failures. His pilot library
  `looped-ligament` under `~/.protracker/library` has revision 1's
  `lit.sqlite` beside where the app will write `store.sqlite`; the two do
  not collide.

- **2026-09-03** — Initial commit. Not yet run on the user's machine: the
  first local session is `npm link`, `ollama pull qwen3:14b`, `lit doctor`,
  `lit init "Looped Ligament"`, the two ELAC searches, `lit refresh`, then
  `lit wanted` for the PDFs to collect and `lit config --extract ollama`
  + `lit extract` once Ollama is up. Then the bench questions with
  `--trace`, and the model-stage entities should show up as seeds.
