# Backlog

Where wants wait until they are built. Strike entries that get built; add
wants as they are voiced.

## Named priorities

- **Collect mode** (Karim, 2026-09-03) — the one step of the loop that
  needs a screen: an in-app browser (in Protracker's Research tab, or a
  small window of litrag's own) that walks the `lit wanted` list, opens
  each DOI through the institution's proxy, lets the user sign in once and
  click the PDF, catches the download into the inbox as `<key>.pdf`, and
  moves on. Electron: a WebContentsView on a persistent session partition,
  `session.on('will-download')`, the PDF plugin off so inline viewers hand
  the file over. The click stays the user's. A few days, with a Playwright
  test against a fake publisher page.
- **A bake-off of local models** on ten papers for the model stage:
  `qwen3:14b` (default), `gemma3:12b`, `qwen3:32b` at 4-bit; for
  embeddings `nomic-embed-text` (default), `bge-m3`, `mxbai-embed-large`.
  Judged on the rows they produce for the same sections, by hand.
- **A test set of bench questions** with known answers, LitQA2-style —
  ten from the user's own papers — so retrieval changes are measured, not
  eyeballed. Retrieval-only first (is the right chunk in the top five),
  then answers.

## Borrowed ideas, not yet built

- **Rerank and summarise before answering** (PaperQA2's "RCS"): the model
  scores each retrieved chunk 0–10 for the question and writes a short
  summary; only the top summaries reach the answerer. Their largest
  precision gain; one Ollama call per chunk on the GPU.
- **Verification questions in the model stage** (ChatExtract): after the
  rows come back, ask the model per row "is this value stated in this
  text?" and drop the noes.
- **Sentence offsets on model rows**, so every claim can be audited back
  to the exact sentence, as the 2026 schema-constrained biomedical
  extraction paper does. `parameters.sentence` already carries the miner's
  sentence; the model's rows carry `context` in the model's words.
- **Forward citations** via Europe PMC's `citations` endpoint; `snowball`
  only walks backward today.
- **Section-aware chunk sizes** as in the lab's own scripts: conclusions
  kept whole, methods grouped by adjacent paragraphs, ~450-token target.
  Worth an A/B on the test set before copying.
- **`lit ask`**: the fully offline answerer — send the retrieved chunks to
  Ollama and print the answer with `[S1]` labels, as the lab's LM Studio
  script does — for a corpus that should never reach a cloud assistant.
- **Synonym edges** between entity nodes by name embedding (HippoRAG 2's
  synonym edges): "EDC" and "carbodiimide" are one node's worth of meaning.

## Observed, not urgent

- `refresh` re-runs saved searches at limit 50 regardless of their
  original limit; a `--limit` per saved query would let a broad query stay
  narrow.
- A PDF from the inbox with no DOI on its first pages reads as
  `Untitled (<file>)`; a `lit rename <key> "<title>"` (or the model stage
  reading the title) would fix it without the app.
- Europe PMC search returns at most 100 per call; paging with
  `cursorMark` would let a saved query grow past that.
- The CPU embedder pulls ~450 MB of onnxruntime into `node_modules`. An
  install without it, for a machine that will always use Ollama, would be
  an optional dependency.

## Explicitly not wanted

- No automated downloading behind a login: publishers' terms forbid it and
  it gets campus IP ranges blocked. The tool catches what a person clicks.
- No second store: the graph is built from the tables at query time. A
  graph database would be a second thing to keep true.
- No prose from the tool. `query` returns passages with citations; the
  answer is the assistant's, or `lit ask`'s when that exists.
