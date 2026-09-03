# litrag — the design

The knowledge base beside the notebook. Protracker records what Karim does;
the literature loop holds what the field already knows about it, one library
per project, so that the assistant working a project is informed by the
papers as well as by the record. His frame, from the backlog: *the system that
combines the structured data with the intelligence* — the tracker is the
structured half of his own work; this is the structured half of everyone
else's.

This document records the decisions and the plan. It began life inside the
Protracker repository as `LITERATURE.md` and moved here with the code on
2026-09-03; Protracker's backlog holds the original ask and its design
stakes, and the vault's **Literature Loop** project holds the steps.

## 1. Decisions

**1.1 A separate tool, in its own repository.** The tracker's invariants
stand untouched: the vault stays plain text, canonical, small, and holds no
papers. litrag has its own store, its own CLI (`lit`) and its own tests, and
touches Protracker in one place — `lit init` asks `pt` what a project ref
is, when `pt` is on the PATH. Nothing in the tracker knows it exists.

**1.2 It is a local service, in Node, with no Python.** The corpus is
copyrighted PDFs plus Karim's research direction: it never leaves the
machine. Everything runs in the same Node the app ships — SQLite through
`node:sqlite`, PDF text through `pdfjs-dist`, embeddings through
`@huggingface/transformers` (ONNX on the CPU; the model is fetched once and
cached beside the libraries). The installer's promise — no Python, no Rust,
no compilers — holds.

**1.2a The GPU does the reading.** Karim's machine has an RTX 5080, and
Ollama on it is the one thing beyond Node the loop asks for. The tool talks
to Ollama on `127.0.0.1` for two stages: embeddings, when a larger model
than the CPU one is worth having, and the *model stage* — reading each
section of each paper into claims, materials, methods and named parameters,
as JSON against a schema so the answer is rows or nothing. A library says
which backends it uses (`lit config <lib> --extract ollama --embed
ollama`); the default is the CPU embedder and the miner alone, so a machine
without a GPU still gets a working library, and the GPU adds the rows on
top.

**1.3 The store is SQLite: rows first, vectors beside them.** "What
crosslinker concentrations has anyone used on electrocompacted collagen" is a
query with columns in it. So the store is tables — papers, sections, chunks,
parameters, references — with an FTS5 index for words and float vectors for
meaning. `lit sql` runs read-only SQL against it; `lit query` runs the hybrid
retrieval. A vector database would be a second store with nothing the rows
cannot hold at this size (thousands of chunks, not millions).

**1.4 One library per project, keyed to the vault.** A library carries the
project's vault id (`n156`) in its manifest, so a rename does not orphan it,
and the Research tab and the assistant find a project's papers by the same
ref they use for its tasks. A library can also stand alone, for a set of
tasks that is not a project.

**1.5 The shared spine is an include, not a copy.** The method itself — ELAC
— belongs to every project. A library names the libraries it draws on
(`includes: ["elac-methodology"]`) and queries run across the union, each
hit labelled with the library it came from. One paper lives in one place.

**1.6 Ingest is a verb.** "Dynamic" means the library grows whenever a paper
lands — a search run again, a DOI added, a PDF dropped in the inbox — and the
ingest is idempotent: a paper is keyed by DOI, then PMID, then the hash of
its file, so running it twice changes nothing.

**1.7 Sources, in order of trust.** Europe PMC first: it searches MEDLINE and
PMC together, returns open-access full text as JATS XML (sections already
delimited — no PDF parsing), and has reference lists for snowballing.
Crossref for a DOI that Europe PMC does not know. Unpaywall for open-access
PDF locations, once Karim has given an email for its terms. Paywalled papers
are Karim's to fetch through Scripps: the tool lists what it could not get,
and the inbox takes what he brings back. Nothing scrapes a publisher.

**1.8 Paper text stays on the machine; the assistant reads rows.** The
extraction into rows is the local model's job (1.2a), so no paper text goes
to any API. The assistant — Claude, in a session — reads what `lit query`
and `lit sql` return: chunks with citations, and rows. A Claude-backed
extractor remains possible as a third backend for a machine with no GPU,
and stays off unless Karim turns it on.

**1.9 The app comes after the pipeline, and the pipeline is shaped for it.**
The Research tab is a later phase (§7). What it will need is already here:
libraries keyed to vault project ids, every verb answering in JSON, the
store readable by SELECT, and stages that report progress line by line —
which is what `lit serve` will stream to a tab.

## 2. Where things live

```
$PROTRACKER_LIBRARY  (default ~/.protracker/library — never inside the vault)
├── models/                      the embedding model, cached once
├── looped-ligament/
│   ├── library.json             { id, name, projectId, includes, queries, extract }
│   ├── lit.sqlite               the store
│   ├── papers/                  what the tool fetched: <key>.xml, <key>.pdf
│   └── inbox/                   what Karim drops in; ingest moves it to papers/
└── elac-methodology/ …
```

The library root is gitignored and outside the vault by default, so neither
the app's vault watcher nor the archive sync ever sees a PDF.

## 3. The schema

```
papers      key, doi, pmid, pmcid, title, year, journal, authors, abstract,
            source (europepmc|crossref|inbox), status
            (candidate → fetched → ingested | needs-pdf), file, sha256,
            cited_by_count, added_at, ingested_at
sections    paper, ordinal, heading, kind (abstract|intro|methods|results|
            discussion|other), text
chunks      id, paper, section, ordinal, page?, text, tokens
chunks_fts  FTS5 over chunks.text (bm25 for lexical recall)
vectors     chunk, model, dims, vec (float32 blob)
parameters  paper, section, chunk, value, unit, kind (concentration|time|
            temperature|voltage|current|ph|stress|length|…), sentence,
            entity? ("EDC concentration" — the model names it, the miner
            cannot), source (miner | ollama:<model>)
claims      paper, section, text, kind (finding|method|limitation|
            hypothesis|background), source          — the model stage
materials   paper, section, name, role, amount?, source
methods     paper, section, name, description, source
refs        paper, cited_doi?, cited_pmid?, cited_title, matched_paper?
entities    name, norm, kind (chemicals | gene-proteins | organisms |
            experimental-methods | material | method | …)
mentions    entity, paper, chunk?, count, source (europepmc | model)
queries     library's saved searches: source, query, last_run, hits
```

`papers.extracted_with` stamps the model that read a paper, so the model
stage resumes where it stopped and re-reads when the model changes.

`status` is the pipeline's state machine and `lit status` is its read.
`parameters` is what makes "what concentrations has anyone used" a `SELECT`.

## 4. The verbs

```
lit libraries                          every library, with its project and counts
lit init <project-ref|name> [--query Q] [--include LIB]
lit search <lib> "<query>" [--since YEAR] [--limit N]
                                       stage candidates from Europe PMC (no fetch)
lit add <lib> <doi|pmid|pmcid|file.pdf>  one paper, by hand
lit fetch <lib>                        full text for every candidate it can get;
                                       the rest become needs-pdf
lit ingest <lib>                       read, chunk, mine parameters, embed —
                                       for fetched papers and the inbox; idempotent
lit extract <lib> [--limit N]          the model stage through Ollama, resumable
lit annotate <lib>                     entity nodes from Europe PMC's text-mined terms
lit graph <lib> | lit entities <lib>   what the graph is made of
lit config <lib> --extract ollama --embed ollama [--ollama-chat M] [--ollama-embed M]
lit doctor [<lib>]                     root, model cache, Ollama and its models
lit status <lib>                       counts by status; what needs a PDF
lit wanted <lib> [--csv FILE]          the PDFs to collect, most-cited first, with doi.org links
lit query <lib> "<question>" [--limit N]
                                       hybrid retrieval: chunks with citations
lit sql <lib> "<select …>"             read-only SQL
lit snowball <lib> <paper>             stage the references of a paper
lit serve [--port 7411]                the same verbs over HTTP on 127.0.0.1
```

Every verb takes `--json`; the human form is for people. The pipeline is
`search → fetch → ingest → query`; `lit refresh <lib>` runs the saved queries,
fetches and ingests in one go, for the "new paper landed" case.

## 5. The pipeline, stage by stage

1. **Search** — Europe PMC `search` with the library's query; each hit becomes
   a `candidate` row (title, year, DOI, abstract, open-access flag). Dedupe by
   DOI, then PMID. Nothing is downloaded.
2. **Fetch** — for each candidate: open access → JATS XML from Europe PMC;
   else an OA PDF via Unpaywall if configured; else `needs-pdf`. A PDF in the
   inbox is matched to its candidate by DOI found in the text, or added as a
   new paper.
3. **Extract** — JATS → sections directly. PDF → pages of text → sections by
   heading heuristics (Abstract / Introduction / Methods / Results /
   Discussion / References), references dropped from the body.
4. **Chunk** — paragraphs grouped to ~250 words with a one-sentence overlap,
   each chunk remembering its section and page.
5. **Mine** — the local parameter miner: every number-with-unit in a sentence
   becomes a `parameters` row with the sentence around it. No model.
6. **Embed** — `bge-small-en-v1.5` (384 dims, quantised, CPU) over every chunk
   not yet embedded for this model. Vectors are float32 blobs.
7. **The model stage** (`lit extract`, GPU) — each section, in pieces of at
   most ~1200 words cut at sentences, to the chat model with the rows
   schema as Ollama's `format`, temperature 0, thinking off. Claims,
   materials, methods and named parameters land in their tables with the
   model's name as `source`; the miner's rows stay beside them.

## 6. Retrieval

`lit query` runs the question three ways and fuses the rankings by
reciprocal rank: through FTS5 (bm25 over the words), through the embedder
(cosine over the library's vectors, brute force — a few thousand 384-float
vectors take milliseconds; a question is embedded with the instruction BGE
v1.5 was trained to expect), and through the graph (§6a). It returns the
top chunks with paper, section, page and a citation string, and `--trace`
says which rankings held each and where. The assistant answers from those
and cites them; the tool never generates prose. Included libraries are
searched the same way and labelled.

### 6a. The graph layer

After HippoRAG 2 (Gutiérrez et al., ICML 2025), with the store's own rows
as the graph. Nodes are papers, chunks and entities; edges are a chunk
naming an entity (weighted by how often), a chunk belonging to a paper,
and a paper citing another the library holds. The entities come from two
places, neither a cost: **Europe PMC's text-mined terms** for every
open-access paper (`lit annotate` — chemicals, proteins, organisms,
experimental methods, with the section each sits in), and the **model
stage's materials and methods** when it has run. An entity named in more
than 60% of the papers is left out of the graph — "collagen" in a collagen
library links everything to everything and ranks nothing.

A question seeds a personalized PageRank at the entities it names (weight
1 each) and at its ten nearest passages (weight 0.05 each, HippoRAG 2's
passage weight, so entities lead and passages follow); damping 0.5, thirty
rounds. Chunks rank by where the walk settles. What this adds over words
and meaning: a passage that shares the question's *things* — genipin,
ethanol, a cell line — without sharing its words, and passages in papers
the best hits cite. What it cannot do: reach a passage no entity or
citation touches; those are the two other rankings' job, which is why all
three are fused rather than one chosen.

The graph is built from the tables at query time, in memory. There is no
second store, nothing to rebuild, and `lit graph` shows what it is made of.

## 6b. Compared with the lab's own pipeline

Dr. D'Lima's scripts (September 2026) run the same loop in Python: PubMed
E-utilities → PMCIDs and abstracts to CSV → PMC full-text XML per PMCID →
JATS to JSON → a FAISS store built with `bge-large-en-v1.5` on the CPU →
dense retrieval into LM Studio (Mistral Small). Stage for stage:

| | the lab's scripts | the literature loop |
|---|---|---|
| Source | PubMed via Entrez, with MeSH terms and publication types | Europe PMC (search, full text, terms) — the same PMC XML, one API |
| Identity | PMCID | DOI, then PMID, then file hash; a paper is filed once |
| Chunks | 450 tokens target, 800 max, 80 overlap; conclusions kept whole; methods grouped by adjacent paragraphs; figure captions their own chunk type | ~250 words, one-sentence overlap; captions inside their section |
| Embeddings | bge-large (1024-d), FAISS flat inner product, BGE query instruction | bge-small (384-d) or Ollama, SQLite blobs, BGE query instruction — since this comparison |
| Retrieval | dense only; top 14 → 5; ≥0.20 similarity; ≤3 chunks per section; 4,200-token budget | bm25 + dense + graph, reciprocal rank fusion |
| Updates | append new chunks by content hash; a changed chunk forces a rebuild (FAISS cannot replace a vector) | rows and vectors per paper are replaced in one transaction; `--reread` re-reads everything |
| Rows | none — chunks and metadata | parameters (miner), claims/materials/methods (model), entities, refs; SQL |
| Answering | Mistral Small in LM Studio, `[S1]` labels, saved as Markdown | the assistant, from cited chunks; no prose generated by the tool |
| Paywalled papers | not handled | listed as needs-pdf; inbox |

Borrowed from his: the BGE query instruction (a real gap — bge v1.5 wants
it and ours did not send it), publication types for review-versus-article
(his open TODO, `pub_type` now), and the case for section-aware chunk
sizes — his 450-token target with whole conclusions is worth an A/B
against ours on the bench questions. Kept from ours: hybrid retrieval, the
SQL store with provenance, idempotent updates, and no Python.

## 7. The Research tab

Sidebar entry **Research**. It opens on a project picker (the vault's
projects, with a paper count beside those that have a library). A project
with a library shows its papers — title, year, journal, status, with a search
box that runs `lit query` and shows hits under their papers — and a way to
add: a search against Europe PMC with tick-to-stage, a DOI box, and the
inbox folder. A project without one shows one thing: **Create a library for
<project>**, with a seed query prefilled from the project's name and
description.

The renderer talks to `lit serve` over HTTP on localhost; Electron's main
process starts the service on demand and stops it with the app. The e2e
suite runs in a browser tab with no service, so the tab renders against a
fake transport there, as the sync surfaces already do.

This is the second phase. The first is the pipeline behind it, because the
tab is only worth building once a library answers a real question.

## 8. The assistant

**Decided, 2026-09-03:** the assistant lives on Karim's machine as a local
session and drives `lit` there, the way it drives `pt`. The corpus, the
GPU and the questions are in one place; nothing is exported for the cloud.
`AGENT.md` §7 is the assistant's manual for it and the `/literature` skill
is the manner of answering: passages in, citations out, gaps named as
gaps. `/harvest` will gain a step — an intention that names a paper or a
method becomes a `lit search` — when the harvest next runs against a
library.

### Setting up on the 5080

```
ollama pull qwen3:14b            the reader (~9 GB at 4-bit)
ollama pull nomic-embed-text     embeddings on the GPU (optional; bge-small on the CPU is the default)
npm run lit -- doctor            root, model cache, Ollama and its models
npm run lit -- config looped-ligament --extract ollama [--embed ollama]
npm run lit -- extract looped-ligament     the model stage; resumable, about a minute a paper
npm run lit -- status looped-ligament      claims · materials · methods counted
```

Then the same bench questions with `--trace`: the entities from the model
stage should show up as seeds, and the graph ranking should start earning
its place.

## 9. Phases

1. **This session — the general workflow.** `src/` with the schema,
   Europe PMC source, JATS and PDF extraction, chunking, the parameter miner,
   local embeddings, hybrid query, `lit sql`, and the CLI; unit tests with
   fixtures and a fake embedder; a pilot library for Looped Ligament from real
   open-access papers; one bench question answered with citations.
2. **Prove and refine.** Karim brings the paywalled PDFs through the inbox;
   the schema and the miner get tuned on what the questions actually need.
3. **The Research tab** and `lit serve`, with the fake transport for tests.
4. **The assistant's verbs**: `/literature`, the harvest step, the cloud
   question settled.
5. **A Claude extractor**, only if a machine without the GPU ever needs it.

## 10. Open, for Karim

- **Unpaywall email.** Their terms want a contact email per request; with it
  the tool finds open-access PDFs for papers Europe PMC has only as abstracts.
- **Which local models.** Defaults are `qwen3:14b` for reading and
  `nomic-embed-text` for embeddings, both comfortable in 16 GB. Worth a
  bake-off on ten papers: `gemma3:12b` and `qwen3:32b` (q4) are the other
  candidates for reading; `bge-m3` or `mxbai-embed-large` for embeddings.
- **The pilot corpus.** Looped Ligament, seeded with "electrochemically
  aligned collagen" and "electrocompaction collagen" — the first thing to
  confirm at the bench is whether the query set is the right one.
