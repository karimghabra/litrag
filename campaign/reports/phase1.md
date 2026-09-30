# Phase 1 — reliability

**Gate: passed.** The failure is reproduced, characterised, and absorbed. Thirteen
fault-injection tests are green in CI, and the native crash the whole phase was
about happened during the campaign's own corpus ingest and cost one paper.

Commits `28db03f`, `0216bfe`. Ledger: phase 1.

---

## 1. What the failure actually is

`BACKLOG.md` recorded it as an intermittent access violation, "not a poison file",
on no particular paper. Three of those four things turned out to be true and the
fourth misleading — and the difference decided the fix.

**There are two distinct failures, not one.**

**(a) A hang, and it is reproducible.** Over a 25-PDF batch the in-process worker
stopped at the same paper — `doi:10.1002/wer.70343` — on **four** independent
runs, leaving it `parsing` with eighteen papers still `queued` behind it. That is
the same trace a crash leaves, which is why it had been read as one. It is not:

| | |
|---|---|
| Docling converting that paper alone (`wedge.py`) | **13 s**, 291 texts, fine |
| Docling converting all 25 sequentially in one process (`batchwedge.py`) | **25 of 25**, nothing died |
| The **worker** converting that paper alone, in-process | **hung for 2,402 s**, 0 trees |
| The **worker** converting that paper alone, with the child | **22 s**, 1 tree, 196 nodes |

So it is not the file, and it is not Docling: Docling reads the whole batch in one
process without complaint. What the worker adds is a **fresh thread per paper** —
spawned so the heartbeat can run while `convert()` blocks — and that is where the
wedge lives. Docling runs on `cuda:0` here, and a CUDA context is thread-affine.

**(b) A genuine native crash, caught in the wild.** During this campaign's corpus
ingest the layout child exited **3221226356** (`0xC0000374`, heap corruption)
reading `doi:10.1162/IMAG.a.1236` — the same code `BACKLOG.md` records against a
different paper. It was retried in a fresh child, that paper was read, and the
remaining papers were read. Before this phase it would have ended the run.

**One caution, recorded because it nearly produced a wrong finding.** `nohup … &`
from this harness does not survive, and a job killed that way leaves *exactly* the
same trace as the wedge — a paper at `parsing`, the rest `queued`. The first two
"reproductions" were of that kind and are not evidence. Every claim above comes
from a tracked run (`STATUS.md`, operational gotchas).

## 2. What was built

**The layout stage runs in a child process the worker supervises** (`layout.py`).
The seam was already in the design: the raw Docling document is written before any
row and everything after it is pure Python, so the child's whole job is to produce
that file. It is long-lived, because Docling takes seconds to build and pulls
torch. Per paper: a timeout, a respawn on death, **one** retry in a fresh child —
every paper that has ever killed the worker parsed fine on a later attempt — then
the paper fails with both attempts in its reason. A refusal *from* Docling (an XML
its backend cannot read) is the paper's own and is not retried.

`LITRAG_LAYOUT_CHILD=off` converts in-process, as before, and is still tested.

**Every pdfium handle goes through `recover.open_pdf`.** Five call sites leaked a
document on their exception path; pypdfium2 closes what it still holds through
`weakref.finalize`, so the native close ran whenever the collector next ran —
which can be inside a Docling parser thread that is itself in pdfium, and litrag's
calls never take Docling's lock. Behaviour-preserving: the 199 tuned pairs differ
in **no cell** after the change (ledger, `pairs-199-after-pdfium-fix`).

**"Already read" is a fact about the tree**, not about the `papers` row: nodes
exist *and* the raw document exists. That is what made the lost-seventeen-papers
failure silent and permanent — every later ingest read `parsed` and skipped the
file, and `rebuild` never visits a paper whose raw document is gone. A paper left
`parsing` by a dead worker is given `failed` and a reason when its library is next
opened; `harness.unread_papers` names every paper it is passing over, and `--gate`
fires on a broken claim but **not** on an honest `failed`.

## 3. The tests, and why they are the evidence

The real crash is intermittent and needs the models, so it can never be a test.
`fake_layout.py` speaks the same protocol and fails on demand — including a
**real** access violation, a bad pointer rather than a simulated exit, because
`ctypes.string_at(0)` raises a catchable `OSError` and would have tested nothing.
Thirteen tests cover: a crash retried once; a crash twice, reported; a wedge
killed and retried; a wedge on every attempt, given up on with a reason; a refusal
not retried; a child that hangs **before** it is ready; a child that dies before
it is ready; the heartbeat; the killed child being reaped; and the document
written whole or not at all.

Three of those came from the independent review, which found that the first cut
of this module reintroduced the very failure it exists to remove: `_spawn` read
the child's `ready` line with a bare blocking `readline()` outside any deadline.

## 4. Measured on the way

- **Docling's layout is not bit-reproducible**, and exactly one part of it is not:
  six conversions of one PDF gave two distinct documents, differing only in
  `pictures` bounding boxes at ~1e-4 pt. `texts` and `tables` were identical
  across all six and node counts never moved. (`DECISIONS.md` D5.)
- **The child reads what the process did.** Same PDFs, same raw documents
  (pictures aside), same node counts.
- **Throughput is unchanged in practice**: the campaign's 336-paper ingest ran at
  about 4.6 s a paper with the child on.

## 5. Still open

- `quit` is `os._exit`, so a paper queued but not started is abandoned without a
  word. It is *reported* by `unread_papers` and rescued by `reparse`, but the
  worker does not drain its queue. For `BACKLOG.md`.
- The wedge's root cause is localised to the worker's per-paper convert thread but
  not proven to the line. The child makes it survivable rather than absent;
  removing the thread entirely is the smaller fix and is untested. For
  `BACKLOG.md`, with the reproduction recipe above.
- The two-full-pass gate runs on the campaign corpus in Phase 2.
