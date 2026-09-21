# litrag: the dynamic reader

> Persisted verbatim from the campaign kickoff message. Read together with
> `PROMPT.md`, which says how to work; this file says what to build, in what
> order, and what has to be true before each part counts as done.
>
> Deviations from this plan, forced by what the code actually does, are recorded
> in `STATUS.md` under "Plan-vs-code discrepancies" and in `DECISIONS.md`.

A one-week plan. Read it together with `PROMPT.md`, which says how to work; this
file says what to build, in what order, and what has to be true before each part
counts as done.

## 0. What this plan was written from

The author of this plan read the README and `DESIGN.md` on `main`, the
descriptions of PRs #19 and #20, and the `AGENT.md` diff in the merge commit of
#20. The author did not read the code. Module names below (`pairs.py`,
`typography.py`, `confidence.py`, `meaning.py`, `boundary.py`, `outline.py`,
`review.py`, `recover.py`, `glyphs.py`, `headings.py`, `paper_type.py`,
`tree.py`, `harness.py`) come from those documents. Where this plan and the code
disagree, the code and `CLAUDE.md` win; note the disagreement in
`campaign/STATUS.md` and carry on.

## 1. The goal, stated so it can be measured

Karim's requirement is 99.9% on novel papers from novel publishers. It has to be
split into parts that can each be verified, because "99.9% of everything, blind"
cannot be measured against a witness that is itself noisier than 0.1%.

**T1. Accounting (ingestion).** For every born-digital PDF, every word in the
pdfium text layer is either inside a node or inside a `dropped` row that carries a
reason. Target: at least 99.9% of words accounted for, per paper, on at least 99%
of papers, with no witness needed. Where a witness exists, also keep reporting the
existing measure (share of the XML's prose words that reach the tree, 0.9995 in
PR #20).

**T2. Precision of asserted lanes, on novel publishers.** Among chunks whose lane
the reader asserts (a named lane, not `other`, not flagged uncertain), the share
whose lane matches the witness under `pairs.py`'s `placed` definition. Target: at
least 99.9% on publishers the reader has never been tuned on, with coverage (share
of witness chunks that get an asserted lane) reported beside it and pushed as high
as the precision bar allows. Aims for coverage, not requirements: 85% overall, 90%
for methods and results in research papers.

**T3. Transfer.** Precision on novel publishers should be statistically
indistinguishable from precision on familiar ones at the shipped operating point.
Coverage is the number allowed to fall under shift. This is the property that
makes T2 mean something on a publisher nobody has tested.

**T4. No silent failures.** Every file handed to `ingest` ends in a terminal state
with a reason. Papers with a `papers` row and no nodes: zero. A native crash costs
one paper, once, and is retried.

**T5. Reported, not targeted.** Heading detection precision and recall against the
witness's section titles, depth accuracy, paragraph integrity (split and merge
rates), reading-order errors, paper-type accuracy. All split by publisher
familiarity.

"Novel publisher" means a publisher (normalised name plus DOI registrant prefix)
that appears in none of Karim's libraries, none of the pair sets used before this
week, and not in this campaign's DEV split. Because different publishers sometimes
share a typesetter, also compute the template fingerprint (Phase 3) and report a
second split by novel template.

## 2. Principles, with their reasons

1. **Price before you build.** R3.11 in `DESIGN.md` already says the pairs can
   price a rule before a line of it is written. Every mechanism in this plan
   starts as a measurement on DEV that says how much headroom it has. If the
   headroom is not there, record that and move on.
2. **Key decisions to the document, never to the publisher.** A rule that
   recognises a publisher's shapes reads 0.966 on that publisher and 0.906 on the
   next. Typography relative to the paper's own body text, furniture by
   repetition, front matter by match against the paper's metadata record, and
   lanes by the genre's vocabulary all transfer.
3. **Under shift, lose coverage, not precision.** A learned confidence score
   calibrated on known publishers is miscalibrated on new ones; that is what shift
   means. Agreement between mechanisms that fail differently degrades the right
   way: on a strange layout they disagree more, so less is asserted.
4. **A model proposes; the document's own invariants dispose.** `review.py` gained
   nothing because a verdict could only fill a silence. A model may overrule a
   rule when, and only when, an objective check on the document improves as a
   result.
5. **Everything is a row, and `rebuild` never asks a model.** Already the repo's
   rule. Every new verdict, template, repair and override replays.
6. **Off by default until gated.** The repo ships `LITRAG_REVIEW`,
   `LITRAG_OUTLINE` and `LITRAG_BOUNDARY` off with their measurements on record.
   New stages follow the same convention.
7. **Nothing leaves the machine.** The product's runtime never sends paper text or
   page images to a remote API. Local models only.
8. **A held-out set is consumed by looking at it.** See section 3.

## 3. The evaluation protocol

Build this before changing the reader. Every later claim rests on it.

**Splits are by publisher, never by paper.**

| Split | What is in it | What you may look at |
| --- | --- | --- |
| DEV | every publisher already seen by the repo's history, plus about 40% of newly sampled publishers | anything |
| VAL | about 30% of newly sampled publishers | aggregates, per-publisher numbers, error categories; per-paper output only through the budgeted inspection below |
| SEALED | about 30% of newly sampled publishers | aggregate numbers only, at phase gates, at most six scorings in the week |
| EXAM | publishers in none of the above, drawn in the last phase | scored once, after the code is frozen |

VAL inspection budget: at most 30 papers over the week, each inspection logged
with the reason. An inspected paper moves to DEV from then on, along with its
publisher, and is replaced from the reserve. The harness enforces SEALED: it
prints aggregates only for that split and appends every scoring to the ledger.

**The corpus.** Europe PMC open-access papers held as both the publisher's PDF and
JATS XML. Aim for at least 300 distinct publishers at up to three papers each,
stratified by paper type (research, review, short forms) and by subject area so
that tissue engineering is a minority. Exclude or separately label PMC author
manuscripts: they share one NIH layout and would count as a single template. Reuse
the repo's existing Europe PMC client, confirm endpoints against the current
Europe PMC documentation, rate-limit politely, and scrape no publisher. Version
the manifest (DOIs, publisher, split, type) in the repo; the files stay outside
it.

**The ledger.** `campaign/LEDGER.jsonl`, one line per evaluation run: time,
commit, split, manifest hash, flags, seed, command, metrics. A number that is not
in the ledger does not go in a report.

**Uncertainty.** Confidence intervals by bootstrap clustered on publisher.
Macro-average across publishers beside the micro-average across chunks. A claim of
"at least 99.9%" is made only when the interval's lower bound supports it;
otherwise report the point estimate and the interval.

**The witness's own noise.** Sample at least 200 reader-witness disagreements
uniformly from DEV and classify each as reader error, witness error, a journal
convention, or ambiguous, with the page, the quote and the XML path as evidence,
stored as rows Karim can spot-check. Report raw numbers first, always; adjusted
numbers beside them, never instead of them.

**Test the tests.** Before trusting any metric, show it can fail: delete a
paragraph from a tree and watch accounting drop; swap two sections' lanes and
watch placement drop; shuffle labels and watch precision go to chance; plant one
paper in two splits and watch the leak check fire.

## 4. Phases

Each phase has a gate. A phase whose gate fails is not abandoned silently and not
forced through: follow the "if the gate fails" note, write down what was learned,
and decide with numbers.

### Phase 0. Orientation and baseline

- Read `CLAUDE.md`, `DESIGN.md` (all revisions), `AGENT.md`, `PIPELINE.md`,
  `NOTES.md`, `BACKLOG.md`, `CHANGELOG.md`, then the parser's code and tests.
- Discover the environment: OS, GPU, Ollama and which models it has, network
  access, where `$LITRAG_ROOT` and the pair libraries are, Docling's version, free
  disk, whether Docker or Java is present.
- Run `npm run check:all`. Reproduce PR #20's headline numbers before touching
  anything.
- Create `campaign/` (see `PROMPT.md`).

**Gate:** checks green; baseline reproduced within tolerance, or the reason it
cannot be reproduced is understood and written down.
**If the gate fails:** bisect between data, environment and nondeterminism. If the
old pair libraries are not available, say so plainly and let Phase 2's corpus
become the baseline.

### Phase 1. Reliability

- Reproduce the intermittent access violation on the PDF path if the platform
  allows. First hypothesis: pdfium is not thread-safe, and the worker answers
  reads on the main thread while an ingest thread runs Docling. List every pdfium
  call site and the thread it runs on.
- Whatever the cause, isolate the parse in a child process that the worker
  supervises: per-paper timeout, respawn on death, one retry in a fresh child,
  then quarantine with a reason.
- Derive "ingested" from facts (nodes exist, conservation checked), not from the
  presence of a `papers` row, so an interrupted paper is retried. The raw Docling
  document is already written before any row, so a crash after layout should
  resume at `tree` without the models.
- The harness reports papers with no nodes as an error.
- Add fault-injection tests: kill the child mid-parse, corrupt a PDF, fill the
  queue, and show every file reaches a terminal state.

**Gate:** two full passes over the largest available set with zero papers lacking
a terminal state; fault-injection tests green in CI.
**If the crash does not reproduce here:** ship the isolation and the state machine
anyway, with the fault-injection tests as the evidence, and leave a reproduction
recipe for Karim's machine in `BACKLOG.md`.

### Phase 2. The measurement apparatus

Corpus download can start during Phase 1. No reader change lands before this
phase's gate.

- Build the corpus, the splits, the ledger and the harness discipline of
  section 3.
- Implement T1 accounting, including a check that the text layer itself is sane
  (dictionary-word rate, `glyphs.py`, OCR on a sample of lines).
- Run the witness-noise audit.
- Produce risk-coverage reporting by familiarity, with clustered intervals.
- From the witness, compute a precision table per existing rule in `tree.py`: how
  often each rule fires, and how often it is right, by familiarity. Then label
  each rule as document-keyed, genre-keyed or publisher-keyed.
- Locate the remaining lane errors: what share sit under a confident rule, and
  what share in a silence.
- Pricing experiment A: using the witness to mark true headings, in what share of
  papers do all level-1 headings fall into one typographic style class distinct
  from body text? By familiarity.
- Pricing experiment B: with signals already stored as rows (vocabulary,
  typography, block classifier), what is lane precision and coverage where they
  all agree? By familiarity.

**Gate:** a baseline report on the new corpus in `campaign/reports/`, the metric
mutation tests passing, and the familiar-novel gap measured.
**If the gap is absent:** good news needs checking. Confirm the novel split really
has distant template fingerprints, check sample sizes, and check whether small
publishers' JATS is poorer, which would depress scores for reasons that are not
the reader's.

### Phase 3. Template induction

A new module (suggested `template.py`), building on `typography.py`.

- Per line, from pdfium runs: font family with the subset prefix stripped, size as
  median glyph height merged within a tolerance, weight and slant from the font
  name and flags, caps ratio, colour, indent relative to the column, centring,
  space above and below relative to body leading, line length relative to column
  width, numbering prefix, terminal punctuation, page band, recurrence across
  pages.
- Group lines into style classes deterministically. Start simple: an exact key on
  the discrete features with tolerance merging, refined only if measured to help.
- Assign roles to classes, not to lines, by pooling evidence over all members:
  vocabulary and canonical-name hits, numbering patterns, "is followed by body",
  length, recurrence (furniture), proximity to pictures and tables (captions),
  reference-entry shape, footnote position and size, match against the paper's
  Europe PMC or Crossref record (title, authors, affiliations, dates, licence).
  Docling's label is one piece of evidence, not the truth. Order heading classes
  into depths by prominence, numbering depth and containment. Abstain when the
  margin is small.
- Store the template as rows: classes, features, member counts, role, evidence,
  confidence. `rebuild` replays it.
- Integrate behind a flag. A/B against the current reader on DEV and VAL. Replace
  a publisher-keyed rule with template-derived behaviour only where the precision
  table and the A/B both support it; otherwise keep the rule and use the template
  as an additional mechanism for Phase 5.
- As one more independent signal, re-test Docling's own heading-hierarchy stage
  with `generate_parsed_pages=True`. Docling's documentation says the style signal
  is skipped silently without it, which may explain the "no levels" note in R2.3.

**Gate:** on VAL's novel publishers, heading F1 and placement improve beyond the
interval; on the legacy gates (the 199 tuned pairs and the three corpora) no paper
is worse, or each regression is understood and priced under R3.11.
**If it hurts fitted publishers:** the rules stay where they measure better.
Induction still earns its place as a mechanism in Phase 5.

### Phase 4. Closed-loop reading

A new module for invariants and one for repair.

Invariants, each returning pass, fail or not-applicable, with a severity and a
location:

- I1 conservation of text-layer words (T1).
- I2 no text said twice.
- I3 heading numbering: continuity, nesting, no regressions. A gap names the
  number that is missing and where to look for it.
- I4 reference list: entries enumerate 1..N or sort alphabetically; N agrees with
  the highest numeric citation; entries look like references.
- I5 citation markers resolve to entries.
- I6 every "Fig. k" and "Table k" resolves to caption k; captions enumerate
  without gaps; each caption hangs on a figure or table node.
- I7 the paper type's contract: required lanes present with a plausible share of
  the prose (extend `confidence.py`'s measurements).
- I8 lane order follows an allowed sequence for the type, methods-last variants
  included.
- I9 paragraphs are well formed (open and close like sentences) or have a join
  candidate; language-model continuity across joins, columns and pages.
- I10 no line that recurs across pages, and no line matching the metadata record,
  sits in the body.
- I11 geometry: reading order monotone within a column; every page with text-layer
  words has nodes.
- I12 every member of a style class holds the same role, exceptions listed.
- I13 headings are short, are followed by body, and do not end like sentences
  unless run in.

Price every invariant on DEV with the witness: P(reading error | fails),
P(fails | reading error), and how often it is not applicable. Invariants with high
precision become hard checks; the rest are advisory and feed confidence.

Repair: at each hard failure, generate alternatives where the ambiguity lives (a
class's second-best role; another column order for the page; a join or a split; a
heading candidate near an I3 gap that begins with the missing number;
re-attaching a caption). Accept a repair only if the total violation score falls
and I1 still holds. Bounded, deterministic, every accepted and rejected repair a
row.

Fold `boundary.py`'s likelihood into a small learned join classifier with
punctuation, case, matching font runs, column extent and the type of the
intervening block as features, trained on the witness's split reasons from DEV,
evaluated leave-publisher-out.

**Gate:** on VAL, repairs raise placement and paragraph integrity with zero
accepted repairs that the witness scores as a regression on DEV.
**If invariants prove imprecise:** demote them. If repairs regress, require two
independent invariants to improve before accepting.

### Phase 5. Assertion by agreement, and abstention

- Oracle hygiene first. Check whether `meaning.py` sends nomic-embed-text its
  required task prefix; derive every kind's threshold and margin from DEV at a
  target precision; cache scores separately from verdicts so a threshold sweep is
  free; test subtracting the paper's mean paragraph vector before comparing to
  block centroids, scored by familiarity and by subject area.
- Mechanisms: heading vocabulary and canonical names; template role; the block
  classifier; section-order grammar; Docling's labels and heading levels; a second
  reader if one runs locally (GROBID; Docling's VLM pipeline used for structure
  only, never for text).
- Schema: keep the asserted lane where `role` lives today, and add the best guess,
  a confidence, the reasons and an `uncertain` flag, so nothing is lost when the
  reader abstains.
- Policies to compare on VAL: unanimous agreement; agreement with no dissent among
  at least k; a gradient-boosted combiner trained on DEV and evaluated
  leave-publisher-out. Choose by T3: the policy whose precision on novel
  publishers stays closest to its precision on familiar ones at the operating
  point, then by coverage. Allow a stricter bar for methods and results.
- Novelty: a template fingerprint (style keys plus page geometry) and its distance
  to known templates. A first-contact paper is flagged for review however
  confident the reading looks.
- Paper type on novel publishers: measure `paper_type.py` by familiarity; if there
  is a gap, add a meaning-based fallback that abstains, and a small tool that
  clusters abstentions and proposes new types for Karim's approval.

**Gate:** on VAL's novel publishers, asserted-lane precision at the chosen
operating point, its interval, and its coverage; the same on SEALED as a check
that VAL was not overfit.
**If precision falls short at any useful coverage:** analyse errors by category,
find which mechanisms fail together, add a more independent one, raise the
agreement requirement, and tighten per lane.

### Phase 6. Propose-and-verify escalation

For papers that fail hard invariants or carry low confidence.

- Give a local model the page renders, the style-class table (samples, features,
  current role, evidence) and the failed checks. Take back, against a JSON schema,
  a short list of template edits: class roles and depths, furniture regions,
  column order per page. Never text.
- Re-run the deterministic reader with the proposal. Accept only if the violation
  score falls and conservation holds. Rows, replayed by `rebuild`.
- Local models only, through Ollama. If this environment has no suitable model,
  build it against a fake transport with tests and leave a measured runbook for
  Karim's GPU.

**Gate:** ships on only with a measured gain on VAL and no accepted regressions on
DEV; otherwise ships off with its numbers, as `review.py` did.

### Phase 7. Template memory and the review queue

- A `templates` table: fingerprint, class-to-role map, provenance (induced,
  verified by invariants, verified by a person), paper count, first seen, last
  verified. On a known fingerprint, reuse and then verify.
- In the window: a queue ordered by novelty and confidence; the failed invariants
  shown; a correction made at the level of a style class. The correction is an
  override row, re-anchored the way notes are, replayed by `rebuild` across every
  paper of that template. Keep the UI small; add an e2e case.

**Gate:** a correction to one class changes every paper of the template on
rebuild, survives a reread, and is covered by tests.

### Phase 8. The exam and the report

- Freeze the code. Draw EXAM from publishers in no split. Score once.
- Audit every asserted-lane disagreement on EXAM with page evidence, so Karim can
  verify each by eye.
- Run the pre-return checks in `PROMPT.md`, make the corrections they call for,
  and write `campaign/REPORT.md`.

## 5. Order and budget

Rough shares of the week: Phases 0-1, 10%; Phase 2, 20%; Phases 3-4, 35%;
Phases 5-6, 20%; Phase 7, 5%; Phase 8, 10%. Parse each paper with Docling once and
iterate on `rebuild`, which needs no models. If a line of work has used about a
day without moving its gate metric, stop, write up what it taught, price the next
alternative and move on.

## 6. Out of scope this week

Wiring the retrieval loop to the tree; the Research tab; non-biomedical witnesses
such as arXiv LaTeX (note ideas in `BACKLOG.md`); any remote model in the
product's runtime. Do expose the new confidence and `uncertain` columns so
retrieval can use them later.

## 7. What comes back

Draft PRs per phase against an integration branch, each described in the house
style of PR #20; `campaign/REPORT.md`; the ledger; the manifest; the audit rows;
`campaign/DECISIONS.md`; and the repo's documents updated to follow the code.
