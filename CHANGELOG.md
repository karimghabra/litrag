# Changelog

## 0.2.0 — 2026-09-11

The tree, the app, and a second language.

- `parser/`: a Python worker (uv) that reads PDFs and JATS XML with Docling
  into one node tree — sections, paragraphs, tables with their cells,
  figures, captions — each node with its parent, depth, ancestry, role and
  provenance (page and box for a PDF), stored as rows in SQLite beside the
  raw Docling document. Speaks JSON lines over stdio: `hello`, `libraries`,
  `init`, `ingest`, `reparse`, `rebuild`, `papers`, `tree`, `node`,
  `section`, `events`, `sql`, `file`, `parse_json`.
- Facets: a node's role is the role of the top-level heading above it,
  inherited down; the vocabulary is finite and anything else is `other`.
  The tree builder repairs what the layout model gets wrong on real
  papers — flat heading levels, a heading merged into the list item below
  it, a heading echoed across a page break, a doubled heading — stands in
  a visibly untitled section where a heading was dropped, and files what
  comes before the first heading as front matter.
- `app/`: an Electron window that shows papers as they are ingested — filed,
  models loading, layout, tree, saved — the tree of a paper with its lanes,
  and the page a node came from with its box drawn on it; tables as grids;
  drag-and-drop PDFs; the worker's own log.
- Tests: 16 parser tests over two saved Docling documents (a PDF each from
  Nucleic Acids Research and Micromachines, and the latter's JATS), the
  app's protocol tests, and a headless smoke run under Xvfb. CI checks the
  parser, the app and the CLI.
- The `lit` CLI of 0.1.0 is unchanged and not yet connected to the tree
  store.

## 0.1.0 — 2026-09-03

The literature loop, moved out of Protracker into a repository of its own.

- `lit init`, `search`, `add`, `fetch`, `ingest`, `annotate`, `extract`,
  `refresh`, `snowball`, `wanted`, `status`, `papers`, `query`, `sql`,
  `entities`, `graph`, `config`, `doctor`, `libraries`, `where`.
- Europe PMC as the source: search, open-access JATS full text, reference
  lists, text-mined terms.
- JATS and PDF reading into sections; ~250-word chunks cut at sentences;
  a miner for every value with a unit.
- Embeddings on the CPU (`bge-small-en-v1.5`, with the BGE query
  instruction) or through Ollama.
- Retrieval: bm25 over words, cosine over meaning, and a personalized
  PageRank walk over papers, chunks and entities, fused by reciprocal rank;
  `--trace` shows the seeds and each hit's ranks.
- The model stage through Ollama: claims, materials, methods and named
  parameters as JSON rows against a schema, resumable per paper.
- Libraries tied to Protracker projects through `pt --json show` when `pt`
  is on the PATH; standalone otherwise.
- Forty-two tests over fixtures from Europe PMC and a fake Ollama.
