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

- Europe PMC's JATS full text needs no PDF parsing; its text-mined terms
  give the graph real entities (genipin, ethanol, carbodiimides, rabbit)
  at no cost. Both are one REST call per paper.
- `bge-small-en-v1.5` quantised on the CPU: ~4 minutes for 4,900 chunks on
  a slow cloud box; seconds on a GPU through Ollama.
- Hybrid retrieval with the graph as a third list: on the pilot it removed
  word-match false positives and left already-good answers alone. Modest
  until the model stage adds materials and methods as entities.

### Standing decisions

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

- **2026-09-04** — First local session ran the whole loop on Karim's
  machine. looped-ligament stands at 37 ingested / 45 needs-pdf, 1,878
  chunks embedded (`ollama:nomic-embed-text`), model stage read 37/37 with
  qwen3:14b (~8.6k claims), and the genipin bench question seeded from the
  graph as hoped — though every top hit came from one OA paper
  (doi:10.3390/mi15070851), so the paywalled canon really is the missing
  half. Machine notes: Ollama's model store lives on `E:\ollama\models`
  (`OLLAMA_MODELS`, User scope) because `C:` filled to zero bytes free
  mid-pull; `pt` is not on the PATH, so init used `--project-id n156
  --project-ref looped-ligament`; the live Protracker vault is
  `C:\Users\ihave\AppData\Roaming\protracker\vault` (the Desktop
  "8-10-2026" copy is stale, and `PROTRACKER_VAULT` still points at it).
  Built `lit collect` (issue #1) the same day — Karim's PI kept hitting
  Cloudflare challenges collecting PDFs by hand, and one window at a
  human pace is also the pattern publishers tolerate.

- **2026-09-03** — Initial commit. Not yet run on the user's machine: the
  first local session is `npm link`, `ollama pull qwen3:14b`, `lit doctor`,
  `lit init "Looped Ligament"`, the two ELAC searches, `lit refresh`, then
  `lit wanted` for the PDFs to collect and `lit config --extract ollama`
  + `lit extract` once Ollama is up. Then the bench questions with
  `--trace`, and the model-stage entities should show up as seeds.
