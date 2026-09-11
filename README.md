# litrag

One library of papers per project, read into trees you can see.

Drop a PDF on the window and watch it being read: filed by its DOI, laid
out by [Docling](https://github.com/docling-project/docling) into headings,
paragraphs, tables and figures, built into a tree whose every node knows
its section, its lane — methods, results, discussion — and the page and box
it came from. Click a node and the page opens with the box drawn on it.
Everything the reader learned is a row in SQLite you can `SELECT`. Nothing
leaves the machine.

It is the literature half of [Protracker](https://github.com/karimghabra/projtracker),
a lab notebook: the tracker records what you did, litrag holds what the
field already knows about it, and an assistant reads both. It stands on
its own too.

## Quick start

Needs Node 22+, Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```
git clone … && cd litrag
uv sync --project parser            Docling and its dependencies (torch: a few GB)
npm --prefix app install            Electron, pdf.js
npm run app                         the window
```

In the window: **New library**, then **Add papers…** or drop PDFs (or
Europe PMC JATS XML) anywhere. The first paper also downloads Docling's
layout and table models (~0.5 GB, once). A paper takes about fifteen
seconds on four CPU cores; a GPU is picked up automatically when torch
sees one.

## What you see

| Pane | What it shows |
|---|---|
| Papers | each paper with its key, status and live stage — filed · models · layout · tree · saved — then a bar of its lanes, and a flag when no methods section was found |
| Tree | the paper's sections nested as the paper meant them, every node coloured by lane, tables as `rows × cols`, chips to dim everything but one lane |
| Page | the page a node came from with its box, every other node on the page faint; the node's ancestry, role, Docling label and text; a table's cells as a grid |
| Log | the worker's stages and Docling's own log lines, as they happen |

## Where things live

```
$LITRAG_ROOT   (else $PROTRACKER_LIBRARY, else ~/.protracker/library)
├── models/docling/        Docling's models, if pre-fetched here
└── <library>/
    ├── library.json       id, name, project id
    ├── store.sqlite       papers · pages · nodes · nodes_fts · events
    ├── papers/            the files, named by key: doi_10.1093_nar_gkr715.pdf
    ├── parsed/            the raw Docling document per paper, never edited
    └── inbox/             drop files here instead, if you prefer
```

`sqlite3 store.sqlite "select role, count(*) from nodes group by role"` is a
fine way to look at a library; so is the worker's `sql` op.

## The worker on its own

```
uv run --project parser litrag-parser --root=/path/to/root
{"id":"1","op":"init","name":"Looped Ligament"}
{"id":"2","op":"ingest","lib":"looped-ligament","paths":["/path/to/paper.pdf"]}
{"id":"3","op":"tree","lib":"looped-ligament","key":"doi:10.3390/mi15070851"}
```

One JSON line per request on stdin; events on stdout with the same `id`.
`AGENT.md` lists every op and shape.

## The `lit` CLI

`src/` holds the earlier retrieval loop — Europe PMC search and fetch,
chunks, embeddings, hybrid retrieval with a graph walk, `lit query`, `lit
sql`. It still runs (`npm run lit -- help`) against its own store and is
not yet wired to the tree; `DESIGN.md` says how it will be.

## The documents

| File | What it is |
|---|---|
| `DESIGN.md` | The decisions: the tree and the app (revision 2), and the retrieval loop they will feed (revision 1). |
| `AGENT.md` | How an assistant drives the worker and the CLI — ops, shapes, conduct. |
| `NOTES.md` | The assistant's notebook: the libraries, what worked, standing decisions. |
| `BACKLOG.md` | Where wants wait until they are built. |
| `CHANGELOG.md` | What each version changed. |
| `CLAUDE.md` | The invariants a change to this code must keep. |

`npm run check:all` runs everything: the CLI's typecheck and tests, the
app's typecheck, tests and build, the parser's pytest. The parser tests run
over saved Docling documents and never touch the network or the models.
