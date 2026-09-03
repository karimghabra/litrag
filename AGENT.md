# Driving litrag as an assistant

One library of papers per project — designed in `DESIGN.md`. It runs on the
user's own machine, where the papers and the GPU are; a session there
drives it from the command line: `lit <verb>` (after `npm link`), or `npm
run lit -- <verb>` from this repository, every verb with `--json`. When
Protracker's `pt` is on the PATH, `lit init <project>` ties a library to the
vault's project; otherwise a library stands alone.

```
lit libraries                       the libraries, with their vault project ids
lit query <lib> "<question>" --json  cited passages: words, meaning and the graph walk fused
lit sql <lib> "select …"            the rows: parameters, materials, methods, claims, entities
lit status <lib>                     what is ingested, what needs a PDF
lit wanted <lib> [--csv FILE]        the PDFs to collect, most-cited first, with links
lit search | fetch | ingest | annotate | extract | refresh    growing it
```

A `<lib>` is a library id (`looped-ligament`), the project's vault id
(`n156`), or its name. The `/literature` skill is the manner of answering
from it; its conduct is binding:

- **The library speaks, not your training.** Every claim in an answer from
  the literature cites a passage the query returned. A gap is named as a
  gap, with the verb that would close it.
- **Read with `--json`; grow only on the user's word.** `query`, `sql`,
  `status`, `entities`, `graph` read. `search` stages, `fetch` and `ingest`
  read papers in, `extract` runs the model — none of those unasked, and
  never `init` or `config`.
- **Paper text stays on the machine.** The model stage and the embeddings
  talk to Ollama on `127.0.0.1`; the assistant sees passages, never files.
- **The user's notebook is the user's voice.** What the literature says goes
  into conversation, or into the research record when the user says so —
  never into a journal as if the user had said it.

Every verb, with its flags: `lit help`.
