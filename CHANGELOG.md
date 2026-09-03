# Changelog

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
