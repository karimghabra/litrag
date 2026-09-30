# Phase 4 — closed-loop reading

**Gate: not met, and the reason is the finding.** Thirteen invariants are built, each
returning pass, fail or not-applicable with a location. All thirteen were priced on DEV
against the witness — 123 papers, 66 publishers the reader has never seen — and **none earns
the precision a deterministic repair needs.** Four rules out of the thirteen checks say
anything at all once their lift is bootstrapped over publishers. `PLAN.md`'s own fallback is
the instruction that applies: *"If invariants prove imprecise: demote them."* They stay
advisory, the repair loop is not built, and what was learned is written down here.

Branch `campaign/phase4-closed-loop`. Ledger: phase 4.

---

## 1. What was built

`parser/litrag_parser/invariants.py`. Thirteen checks, I1–I13 as `PLAN.md` names them, each a
plain read of the tree; nothing asks a model and nothing keys on a publisher. Where the reader
already measures something the check uses it rather than restating it: I1 is
`evaluate.accounting`, which is T1's own definition and not a second one; I5 and I4 go through
`citations.py`; I6 through `edges.figures`; I7 through `confidence.EXPECTED`; I11's page half
through `harness.page_coverage`.

The difference from `confidence.py` is the whole point of the module. Confidence answers "how
far can this reading be trusted" with one number and a handful of shares, which is the right
answer for a person and a useless one for a repair: **a share cannot be applied anywhere.**
Every violation here carries a node, a page and the offending text.

Three things were deliberate:

- **`n/a` is not a pass.** A paper with no reference list has not satisfied I4. How often a
  check is not applicable is reported beside its precision, because a corpus average that
  counts silence as success measures nothing.
- **Every invariant starts `advisory`**, and stance is data rather than code, so only a
  measured number moves one and the commit that moves it has to name the number.
- **A check that raises loses only itself.** Thirteen checks over a corpus will meet a paper
  that breaks one of them.

### The join that had to exist first

`evaluate.landings` turns the PDF–XML comparison around the *witness's* paragraphs. That is
right for "how much of this paper was read correctly" and no use for "is this finding real",
because a finding is at a node. So `evaluate.node_verdicts` indexes the same comparison the
other way — this block of the reading, and whether the XML twin disagrees with what was done to
it — built on the same `_land`/`_held`/`_top` primitives rather than a second definition of
them. It keeps three disagreements apart, because they are not one fault: prose under the wrong
heading (`lane_wrong`), two of the witness's paragraphs inside one of the reading's (`merged`),
and one of the witness's cut across several (`holds_a_split`).

Blocks the witness cannot speak about — a caption, a table, prose the XML does not have — are
**absent** from the verdicts rather than present and correct. Counting the unjudgeable as right
would flatter every check priced against them, and roughly a third of what the checks fire at
is unjudgeable.

A check that fires at a *heading* cannot be priced this way at all: the witness has paragraphs,
not headings, so I3, I12 and I13 would have scored `None` and looked untested when they were
only being asked the wrong question. A wrong heading shows as wrong prose underneath it, so a
section is judged by what it governs, and the two populations — blocks and sections — are
priced apart with their own base rates.

## 2. Three corrections to the measurement, before any number from it is believed

Each of these changed a headline, and the first two changed which rule looked best.

**(a) A code is not a rule.** I6 is three rules — a caption hanging on nothing, a gap in the
numbering, a call that names no caption — and their average describes none of them. Priced at
the code level, I13 looked like the strongest check in the set at 0.967. Priced per rule it is
one rule, `long`, and that rule is one document (below).

**(b) One document is 32 per cent of DEV.** The conference-proceedings supplement
`10.15167/2421-4248/jpmh2019.60.3s1` — 691 witness paragraphs, read at 0.034 precision — owns
1,834 of DEV's 5,671 judgeable blocks. Dropping it moves the `split` base rate from **0.393 to
0.105**. It also produces 273 of I13 `long`'s 279 firings: its Italian abstract titles are all
over sixteen words. I13's 0.967 was that one paper. This is the third time this document has
falsified a measurement in this campaign; the defence each time has been the per-paper column,
and this table carries `biggest` — the largest paper's share of a rule's firings — for that
reason.

**(c) A bootstrapped numerator over a fixed denominator is not a lift.** The first cut compared
a publisher-clustered precision interval against the micro base rate — a base rate that
document owns a third of. The lift is now computed **inside** each draw, so a rule clears
chance only if it clears it on the publishers it was resampled with. This changed the answer:
`I9 unterminated` over a page break stopped clearing and the same-page variant started. The
claim "the page break is the discriminator" was about to be written down and was wrong.

## 3. What the invariants are worth

123 papers, 66 novel publishers, 5,671 judgeable blocks, 2,217 judgeable sections. 95 per cent
intervals are bootstrapped over **publishers**, and both ends of the lift are resampled.

Base rates: a block is `lane_wrong` 0.041 of the time and holds part of a cut paragraph 0.393
of the time; a section contains a lane error 0.053 of the time.

**Against a wrong lane — the fault the target is about:**

| rule | at | fired | pubs | biggest | precision | 95% | lift | lift 95% |
|---|---|---|---|---|---|---|---|---|
| I12 `paragraph-read-as-section` | section | 80 | 41 | 0.04 | **0.225** | [0.112, 0.353] | **4.23** | **[1.91, 8.53]** |
| I12 `paragraph-read-as-list_item` | block | 30 | 17 | 0.10 | 0.133 | [0.0, 0.267] | 3.28 | [0.0, 11.65] |
| I6 `dangling-call` | block | 68 | 20 | 0.12 | 0.059 | [0.0, 0.143] | 1.45 | [0.0, 4.44] |
| I11 `inversion:left` | block | 406 | 48 | 0.46 | 0.047 | [0.011, 0.143] | 1.15 | [0.53, 1.90] |
| I9 `unterminated:same-page` | block | 70 | 32 | 0.23 | 0.043 | [0.0, 0.109] | 1.06 | [0.0, 2.10] |
| I13 `long` | section | 235 | 7 | **0.96** | 0.004 | [0.0, 0.429] | 0.08 | [0.0, 5.02] |
| I9 `lowercase-start` | block | 65 | 31 | 0.12 | 0.000 | [0.0, 0.0] | 0.00 | [0.0, 0.0] |

**Exactly one rule in the set predicts a wrong lane**: text set the way the document sets its
own paragraphs, but read as a section heading. It fires 80 times across 64 papers and 41
publishers, no paper owning more than four per cent of its firings, and it is right 22.5 per
cent of the time against a base rate of 5.3 — four times chance, with the interval clear of 1.

It survived two further attempts to explain it away, and the second is the one that mattered.

**Does it find a bad *place*, or only a bad *paper*?** A rule that fires more often on papers
that are badly read throughout would show a lift against the corpus and none against the paper
it is in, and pointing a repair at it would be pointing at an arbitrary section of a bad paper.
Conditioned on the paper — a flagged section against the other sections of the same paper,
pooled over 60 papers and bootstrapped over publishers — it holds: **0.189 of flagged sections
hold a lane error against 0.032 of their own paper's others, a within-paper lift of 5.85, 95%
[1.82, 17.88].** It finds a place.

Every split-predicting rule fails this test. `I9 lowercase-start` is right 77 per cent of the
time about a cut paragraph, and within its own paper the unflagged blocks are right 63 per cent
of the time: a within-paper lift of 1.22, 95% [0.89, 7.13]. The same for `unterminated` (0.98
and 0.95) and for both inversions. **They identify badly-read papers, not cut paragraphs.**
That is a confidence signal, and it is not a location. It is also a direct answer to the join
classifier `PLAN.md` asks for in this phase: the structural features it names — punctuation,
case, column extent — do not beat the paper's own base rate, so a classifier over them would
learn which papers are hard rather than which boundaries are joins.

**Does it find what it says it finds?** No, and this is the sharper result. The rule's own
claim is that a block is *not a heading at all*. The witness can answer that directly: does its
reading have a heading there? Over 3,207 headings the reading prints, 1,507 have no match in
the XML (0.470). I12 flags 80 and 35 are unmatched — **precision 0.4375, 95% [0.309, 0.575],
lift 0.93.** Against the claim it actually makes, the rule is *no better than picking a printed
heading at random*.

Both numbers are true at once, and the pair of them is the finding: **the rule points at
sections whose lanes go wrong, and the headings it points at are usually real.** Its examples
are `Abstract`, `OBJECTIVES`, `1. Study design`, `Availability of data and material` — ordinary
headings, set in a typeface that does not stand apart from the body, in papers where something
near them goes wrong. The repair it licenses is "look here", not "delete this heading", and
writing the second would have been the obvious thing to write from the first number alone.

A caution on that second measurement. A base rate of 0.470 cannot mean that the reader invents
nearly half the headings it prints; most of it is the XML not carrying headings the PDF does.
The supplement alone contributes 1,091 of the 1,507. This is the `front:keywords` lesson from
Phase 3 in a new place — **the measure was partly pricing the witness** — and it is reported
because it is the number that stopped a rule being written, not because it is clean.

**Against a paragraph the reading cut in two:**

| rule | at | fired | pubs | precision | 95% | lift 95% |
|---|---|---|---|---|---|---|
| I9 `lowercase-start` | block | 65 | 31 | 0.769 | [0.618, 0.900] | [1.18, 8.42] |
| I9 `unterminated:same-page` | block | 70 | 32 | 0.586 | [0.358, 0.775] | [1.05, 5.52] |
| I11 `inversion:left` | block | 406 | 48 | 0.539 | [0.114, 0.796] | [1.12, 1.81] |
| I9 `unterminated:over-a-break` | block | 38 | 25 | 0.684 | [0.517, 0.821] | [0.99, 7.92] |

Against a merge, nothing clears: the base rate is 0.009 and every interval reaches 0.

**The two faults want different signals, and neither rule crosses over.** `I9 lowercase-start`
is right 77 per cent of the time about a cut paragraph and **0 per cent** of the time about a
lane — 65 firings, not one of them at a mislaned block. `I12 paragraph-read-as-section` is four
times chance on lanes and *below* chance on cuts. A single "violation score", which is what
`PLAN.md`'s repair rule proposes to minimise, would add these together.

**Agreement does not rescue either.** `PLAN.md`'s fallback is to require two independent
invariants before accepting a repair. It helps, and never on enough. The best pair against a
cut is `I11 inversion:left` with `I9 lowercase-start` at 1.000 — over **ten** firings in eight
papers. Against a lane error, pairing I12 with an inversion lifts 0.225 to 0.300 and 0.286 —
over **ten and fourteen** firings. Every combination precise enough to act on is too rare to
act on, which is the same sentence twice.

## 4. Where that leaves the repair loop

`PLAN.md` asks for repair driven by hard failures, accepted when the total violation score
falls and I1 still holds. Two things stop it here, and neither is a matter of engineering
effort.

**No check is precise enough to be hard.** The best lane-error predictor in the set is right
22.5 per cent of the time. A deterministic repair acting on it would be wrong three times in
four. `PLAN.md` anticipated this and says what to do: demote them. All thirteen stay advisory.

**And the one rule that survives does not license the repair it seems to.** The obvious repair
for "a paragraph read as a section heading" is to demote the heading. The direct measurement
says the headings it flags are real four times in five more often than not — lift 0.93 against
the witness's own headings — so that repair would delete correct headings at close to the rate
it deletes wrong ones. What the rule earns is the right to **ask**, which is Phase 6's shape
and not this phase's.

**The join classifier is not worth training on these features.** `PLAN.md` asks for
`boundary.py`'s likelihood folded into a learned join classifier over punctuation, case, font
runs and column extent, trained on DEV's split reasons and evaluated leave-publisher-out. The
within-paper test says the structural half of that feature set carries no information about
*which* boundary is a join — every one of them sits at a lift of 1 inside its own paper. A
classifier trained on them would reach a respectable leave-publisher-out score by learning
which papers are hard, and a boundary classifier that has learnt the paper is a confidence
score wearing the wrong name. Whether the language model's likelihood carries what the
structure does not is a live question and the one worth asking next; it is not answered here
and is not claimed to be.

**I1 cannot be the guard it is written as.** Conservation fails on **123 of 123** DEV papers at
the 0.999 floor, because DEV conserves 0.968. A check that fires everywhere separates nothing:
P(a paper reads below 0.90 | I1 fails) is 0.146, which is just the share of DEV that reads
below 0.90. I1 is a *target*, not a discriminator, and "I1 still holds" in a repair rule has to
mean "does not get worse", not "is above the floor". The floor is left at 0.999 because that is
what T1 asks for and moving it to make the check pass is the thing this campaign does not do.

## 5. Two negative results worth keeping

**I10 fires at nothing the witness can judge.** One paper failed it in 123, and no violation
landed on a judgeable block. The reason is visible on the fixture: the running heads *are*
there — `Micromachines 2024 , 15 , 851`, `x FOR PEER REVIEW 10 of 15` — but they are glued
**inside** paragraphs rather than standing as nodes of their own, so a check that compares
whole nodes cannot see them. I2 and I9 both trip over the same text from the other side. The
check is looking at the right fault with the wrong granularity. `BACKLOG.md`.

**I11's inversions are mostly not the reader's fault.** 869 firings, 1,141 more at places the
witness cannot judge, and a lane-error lift of 1.02. The two-column heuristic reads front
matter and full-width blocks as column members.

## 6. What Phase 4 hands on

**One signal worth carrying into Phase 6.** Text set the way its document sets its paragraphs
but read as a section heading concentrates lane errors 5.85× inside the paper it is in, over 60
papers and 41 publishers. It costs one typography pass, keys on nothing but the document, and
names 80 places in DEV — a candidate set small enough to escalate and broad enough to matter.
It is not a repair. It is a question worth asking a model, and the phase that asks it is the
one that also verifies the answer.

**A method the campaign did not have before.** Three tests now stand between a located finding
and a claim about it, and each of them killed something here:

1. price the rule, not the check it lives in — this killed I13;
2. resample the base rate with the numerator, over publishers — this swapped which shape of I9
   survived;
3. condition on the paper — this killed every split rule, and is the one that would have let a
   join classifier through.

`within_paper.py` and `phase4_rules.py` are the scripts; `evaluate.node_verdicts` is the join
they rest on and is in the repository with tests.

**Nothing in the reader changed.** No rule was written, no repair was accepted, and
`npm run check:all` is green at 321 tests. The reader reads DEV exactly as it did at the end of
Phase 3, which is the point: the phase's output is that four of thirteen checks say anything,
one of them says something useful, and none of them says it well enough to act on alone.
