# Brief: one week on litrag's reader

> Persisted verbatim from the campaign kickoff message, because it existed only
> in the assistant's context. Re-read this, `PLAN.md` and `STATUS.md` after any
> fresh start or context compaction, before doing anything else.

This brief was prepared for Karim by a planning assistant that read litrag's
README, `DESIGN.md` on `main`, the descriptions of PRs #19 and #20, and the
`AGENT.md` diff from #20's merge commit. It did not read the code. You have the
repository, this brief, and `PLAN.md`. Karim is the person you are working for.
You have about a week of continuous work and no token limit, so depth and care
are affordable; false confidence is not.

Start from the branch `claude/decisions-by-meaning`.

## What litrag is, and where it stands

litrag reads scientific papers into trees and stores them as rows in SQLite, one
library per research project, so an assistant can answer from the literature with
citations. A Python worker parses PDFs and Europe PMC JATS with Docling; a tree
builder repairs what the layout model gets wrong; every node knows its section,
its lane (methods, results, discussion and so on) and the page and box it came
from. An Electron window shows the reading as it happens. Nothing leaves the
machine.

The repo's best idea is the witness: a paper held as both PDF and JATS XML is its
own ground truth, and `pairs.py` scores a PDF reading against the XML. With it,
the last branch measured, and you should reproduce:

- `faithful` by words on 199 tuned pairs: 0.832 to 0.973.
- On 127 novel papers: 93.4% of chunks in the right lane, 89.4% as one chunk in
  the right lane.
- Ingestion: 0.9995 of the XML's prose words reach the tree. The text is not
  being lost. Placement is the problem.
- The limit that matters: the rules key on publishers' layouts. On a publisher
  already fitted, 0.966; on one never seen, 0.906; for editorials and letters,
  0.933 against 0.699. The unfamiliar sample is 20 papers.
- A model pass that reviewed the reader's log (`review.py`) gained nothing,
  because it was only allowed to fill silences. A likelihood scorer for paragraph
  joins (`boundary.py`) was too wrong on real pairs to ship on.
- The worker crashes intermittently with an access violation on the PDF path, and
  a paper that was filed but never got a tree is skipped forever.

## What Karim wants

99.9% on totally novel papers from totally novel publishers, through a reader that
adapts to each document instead of accumulating rules per publisher. His words: "a
dynamic way of handling classification and ingestion."

`PLAN.md` turns that into targets that can be verified: word-level accounting
(T1), precision of asserted lanes on novel publishers with coverage reported (T2),
precision that does not move between familiar and novel publishers (T3), no silent
failures (T4). Read them closely. They are the definition of done.

The planning assistant's view, which you should treat as a hypothesis and not as a
constraint: a static reader calibrated on other publishers cannot reach 99.9%
blind, but a reader that induces each paper's template from the paper itself,
checks its reading against the document's own invariants, asserts a lane only
where independent mechanisms agree, and abstains otherwise, plausibly can reach
99.9% precision on what it asserts. Whether coverage at that precision is useful
is an empirical question. Your job is to build it well and find out. If the number
comes out lower, the number you report is the lower one, with the analysis of why.

## How to work

**Orient before acting.** Do Phase 0 properly. Read the documents, then the code.
The plan was written without sight of the code, so expect it to be wrong in
places. Where it is, the code and `CLAUDE.md` win; record the discrepancy and
adapt the plan rather than forcing the code to fit it.

**Keep your state outside your head.** A week of work will outlive any single
context. Create `campaign/` at the start and keep it current:

- `campaign/STATUS.md`: the current phase, what is in flight, the next three
  actions, open questions, anything a fresh reader of the repo would need to pick
  the work up cold. Update it at every natural stopping point.
- `campaign/LEDGER.jsonl`: one line per evaluation run (time, commit, split,
  manifest hash, flags, seed, command, metrics).
- `campaign/DECISIONS.md`: every choice that is really Karim's to make, with the
  reversible default you took and why.
- `campaign/reports/`: a short report per phase gate.
- `campaign/REPORT.md`: the final report.

Whenever you start fresh or your context has been compacted, re-read this brief,
`PLAN.md` and `STATUS.md` before doing anything else.

**Follow the house style.** The repo has strong conventions and they are good
ones. Decisions go in `DESIGN.md` as a new revision section; measurements and
standing decisions in `NOTES.md`; what was refused or deferred in `BACKLOG.md`;
ops, flags and shapes in `AGENT.md`; invariants in `CLAUDE.md` (do not weaken one
without putting it in `DECISIONS.md`). New stages are switched by `LITRAG_*`
environment variables and ship off until their gate passes. Verdicts, templates,
repairs and overrides are rows, and `rebuild` replays them without asking any
model. PR #20's description is the model for yours: what it does, what was
measured, what the witness refused, the honest limit, known issues, verification.

**Git.** Work on an integration branch off `claude/decisions-by-meaning`, with a
branch and a draft PR per phase. Small commits, each one green under
`npm run check:all`. Never push to `main`, never force-push, never merge your own
PR into `main`.

**Karim's data.** His libraries hold copyrighted PDFs and his research direction.
Treat them as read-only: never `reread`, rebuild in place, move or delete anything
under his library root. Work on copies under a campaign root of your own, and
check free disk before large downloads. The evaluation corpus you build is
open-access, so inspect it freely within the split rules. When you run gates over
Karim's own libraries, work from aggregates and the short excerpts you need to
debug, not whole papers.

**Nothing leaves the machine.** This applies to the product's runtime, and it is
easy to violate by accident in this project: you are a remote model, and it will
be tempting to wire a strong remote model in as the escalation step. Do not.
Escalation uses local models through Ollama, or a fake transport with tests if
none is available here. The only network calls the product makes are the ones it
already makes to Europe PMC and similar metadata services at ingest time.

**Parse once, rebuild many.** Docling is the slow step. Keep every raw Docling
document and iterate on `rebuild`.

## Evaluation discipline

This is the part most likely to go wrong over a long autonomous run, and the part
that decides whether the final number means anything.

A held-out publisher stops being novel the moment you read its failures and change
the code. So splits are by publisher, and they have different rules (`PLAN.md`,
section 3): look at anything in DEV; VAL gives you aggregates, per-publisher
numbers and a small logged inspection budget; SEALED gives aggregates only, at
most six times; EXAM is scored once after the code is frozen. Build the harness so
that it enforces this, because good intentions erode by day five.

Make every metric prove it can fail before you trust it (the mutation tests in the
plan). Report macro-averages across publishers beside micro-averages, and
intervals bootstrapped by publisher. Only claim "at least X" when the lower bound
supports it.

The witness is noisy too. At the precision you are aiming for, a large share of
the remaining disagreements may be the XML's fault. Audit them with evidence,
store the audit as rows, and always report raw numbers first and adjusted numbers
beside them. Never redefine a metric to make a number rise. If a definition is
wrong, version it, and report both.

Do not write a rule keyed to a publisher's shapes, even when it would fix the
paper in front of you. That is the habit this week exists to replace. If a
publisher-keyed rule is the only fix, it goes in `BACKLOG.md` with its price.

## Autonomy

Work through the phases without waiting for Karim. When a choice is his (a new
heavy dependency such as Java or Docker, a schema change that breaks an old
library, anything touching an invariant, anything about what may be sent where),
take the reversible default, write it in `DECISIONS.md`, and continue.

Stop and write up, rather than pressing on, only if: the baseline cannot be
reproduced and you cannot explain why; you have neither the pair libraries nor the
network access to build a corpus; or continuing would require breaking an
invariant in `CLAUDE.md` or touching Karim's libraries in place.

When a line of work has cost about a day without moving its gate metric, stop,
record what it taught and what it would have cost to continue, price the next
alternative on DEV, and move on. The repo already treats a refused rule as a
result. So should you.

If your environment lets you run subagents, use them for parallel exploration and,
more importantly, for independent review with fresh context. If it does not, start
a fresh session for reviews.

## Before you come back

Do not return with "done" until you have run these checks and made the corrections
they call for. Put the outcome of each in the final report. A check that still
fails after your best effort is reported as failing, with what you tried.

1. **Every number is reproducible.** Each figure in the report maps to a ledger
   line. From a clean checkout, `npm run check:all` is green and re-running the
   DEV evaluation reproduces the reported DEV numbers. If not: hunt the
   nondeterminism (thread or dictionary ordering, unpinned dependencies, model
   sampling, time-dependent code), fix it, and re-run every affected ledger entry.
2. **Rebuild is deterministic and model-free.** Two rebuilds of the same library
   give identical rows apart from timestamps, with Ollama stopped. If not: find
   the stage that asks a model or reads the clock, and move its output into rows.
3. **The metrics can fail.** The mutation tests pass in CI. If a metric does not
   react to its mutation: fix the metric first, then distrust and re-run
   everything measured with it.
4. **No leakage.** No publisher, template fingerprint or DOI crosses splits;
   SEALED was scored no more than six times; the ledger shows no code change
   between freezing and scoring EXAM; every VAL inspection is logged. If leakage
   is found: move the contaminated publishers to DEV, redraw replacements from the
   reserve, re-score, and say so in the report.
5. **The old gates still hold.** The 199 tuned pairs, the three corpora, and
   Karim's libraries, run read-only. If a paper got worse: understand it. Fix it,
   or justify it under R3.11 with the numbers, and list every such paper in the
   report.
6. **Abstention is not hiding the problem.** Report coverage overall, per lane,
   and per paper type; show that abstentions are spread sensibly and that methods
   and results keep useful coverage. If the precision target is met only at
   trivial coverage: say exactly that. Report the risk-coverage curve and the
   operating point you would recommend.
7. **Everything risky is gated.** Each new model-dependent or behaviour-changing
   stage has a flag, replays from rows, and is on by default only if its gate
   passed.
8. **The invariants hold.** Search the product's runtime paths for network calls
   and confirm nothing new sends paper text or page images anywhere. Confirm raw
   Docling documents are never edited, filing is idempotent, and everything
   answers in JSON.
9. **The reader is robust.** Two full-corpus ingests with zero papers lacking a
   terminal state; fault-injection tests green.
10. **An independent reviewer tried to break it.** With fresh context and only the
    repo and the ledger, a reviewer (a) reproduces the headline numbers, (b) reads
    the week's diff looking for logic keyed to a publisher, and (c) hunts DEV for
    papers where the new reader is confidently wrong. Whatever it finds: fix it or
    report it before returning.
11. **EXAM was run once, then audited.** Every asserted-lane disagreement on EXAM
    has a row with the page, the quote, the XML path and a verdict, so Karim can
    check each by eye.
12. **The documents follow the code.** `DESIGN.md` has a new revision for the
    dynamic reader; `AGENT.md`, `NOTES.md`, `BACKLOG.md`, `CHANGELOG.md`,
    `PIPELINE.md` and the README are current.

## The final report

`campaign/REPORT.md`, written for someone who was not here all week. Lead with a
table of T1 to T4 on EXAM: raw point estimates, intervals, n papers, n publishers,
coverage. Then, in prose with tables where the content is tabular:

- What was built, and which parts ship on, which off, and why.
- What transferred to novel publishers and what did not, with the
  familiar-versus-novel comparison at the shipped operating point.
- The risk-coverage curves, and the operating point you recommend.
- The witness-noise audit and what it implies for the ceiling of the metric.
- What was tried and refused, with the numbers that refused it.
- The remaining error classes, with counts and one example each.
- The outcome of each pre-return check.
- The decisions waiting for Karim.
- How to reproduce every number.

Say plainly whether the 99.9% target was met, on which definition, and with what
confidence. If it was not, say what the measured number is and what you believe
stands between it and the target. A clear account of a shortfall is worth more to
Karim than a number he cannot trust.
