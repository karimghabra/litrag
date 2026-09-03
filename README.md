# litrag

One library of papers per project, built and asked from the command line.
`lit` searches Europe PMC, fetches open-access full text, reads it into
sections, chunks and the numbers-with-units a methods section is made of,
embeds it locally, turns Europe PMC's text-mined terms into a graph, and
answers a question with cited passages — or a `SELECT`. A machine with a
GPU adds a model stage through Ollama that reads each section into claims,
materials, methods and named parameters. Nothing leaves the machine; no
Python.

It was built as the literature half of [Protracker](https://github.com/karimghabra/projtracker),
a lab notebook: the tracker records what you did, litrag holds what the
field already knows about it, and an assistant reads both. It stands on its
own too — a library does not need a tracker project to belong to.

## Quick start

```
npm install
npm link                                  puts `lit` on the PATH (or use `npm run lit -- …`)
lit init "Looped Ligament"                a library; ties to a Protracker project when `pt` is on the PATH
lit search looped-ligament '"electrochemically aligned collagen"'
lit fetch looped-ligament                 open-access full text; the rest are listed as needs-pdf
lit ingest looped-ligament                sections, chunks, the unit miner, embeddings (CPU)
lit annotate looped-ligament              entity nodes from Europe PMC's terms
lit query looped-ligament "genipin concentration and time for aligned collagen threads"
lit sql looped-ligament "select value, unit, sentence from parameters where unit = 'mM'"
lit wanted looped-ligament --csv wanted.csv     the PDFs to collect, most-cited first
```

Drop collected PDFs into the library's `inbox/` and run `lit ingest` again;
it files each by the DOI on its first page. `lit refresh <lib>` runs the
saved searches, fetches, ingests and annotates in one go.

## With a GPU

```
ollama pull qwen3:14b nomic-embed-text
lit doctor
lit config looped-ligament --extract ollama [--embed ollama]
lit extract looped-ligament               claims · materials · methods · named parameters, as rows
```

## Where things live

```
$LITRAG_ROOT   (else $PROTRACKER_LIBRARY, else ~/.protracker/library)
├── models/                the CPU embedding model, cached once
└── looped-ligament/
    ├── library.json       id, name, project id, includes, queries, backends
    ├── lit.sqlite         papers · sections · chunks · chunks_fts · vectors · parameters ·
    │                      claims · materials · methods · entities · mentions · refs · queries
    ├── papers/            what was fetched, and what came through the inbox
    └── inbox/             where you drop PDFs
```

## The documents

| File | What it is |
|---|---|
| `DESIGN.md` | The decisions everything follows from, the schema, the pipeline stage by stage, the graph layer, and how this compares with the lab's own scripts and the published systems. |
| `AGENT.md` | How an assistant drives `lit` — the verbs, the JSON shapes, the things that look like bugs — and how to behave while doing so. |
| `NOTES.md` | The assistant's notebook: the libraries, what worked, standing decisions. |
| `BACKLOG.md` | Where wants wait until they are built. |
| `CHANGELOG.md` | What each version changed. |
| `CLAUDE.md` | The invariants a change to this code must keep. |
| `.claude/skills/literature/` | The `/literature` skill: a bench question in, cited passages out, gaps named as gaps. |

`npm run check` runs the typecheck and the tests; the tests use fixtures
from Europe PMC and a fake Ollama and never touch the network or a GPU.
