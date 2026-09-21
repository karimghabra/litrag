# Phase 5 — assertion by agreement, and abstention

Branch `campaign/phase5-agreement`, cut from Phase 4's. Ledger: phase 5.

---

## 1. Oracle hygiene: a threshold sweep was the opposite of free

`PLAN.md` opens this phase with "derive every kind's threshold and margin from DEV at a target
precision; cache scores separately from verdicts so a threshold sweep is free". The second
clause turned out to be the blocker for the first, and in a stronger form than it is written.

**The threshold was part of the cache key.** `Kind.signature()` hashed the threshold, the
margin and the per-lane margins alongside the examples, the centroids and the prefix. Editing a
threshold therefore did not re-decide anything: it changed the key, missed every row and
re-embedded the corpus. `lanes.sqlite` carries the evidence — **125,739 rows, and up to five
signatures for one kind**, one per time a threshold was edited:

| kind | rows | `other` | rule signatures |
|---|---|---|---|
| heading | 56,793 | 94.5% | 5 |
| heading-alone | 19,413 | 94.5% | 3 |
| canonical | 19,279 | 98.0% | 1 |
| block | 16,606 | 97.6% | 4 |
| *all* | **125,739** | **90.8%** | |

**And a refusal forgot what it refused.** `_decide` collapses a ranking into a verdict, and only
the verdict was stored. Where the rule declined the nearest group the row says `other` and no
longer knows which group that was — for **90.8 per cent of every row ever written**. A threshold
could be raised by replay and never lowered, which is the direction a sweep needs.

**The fix is to split the key.** `Kind.space()` is what determines the *ranking* — the examples
or centroids, the prefix, the position prior, the embedder. `Kind.rule()` is what decides from
it, and is stored beside the verdict rather than inside its key. The top four of the ranking is
stored with it. A sweep is now a re-decision over rows already held, in both directions and for
nothing.

A row written before this is honest about being so: a hit under the rule that wrote it and a
**miss** under any other, rather than one rule's answer passed off as another's.
`LITRAG_LANES_REFRESH=on` asks again for such rows so they gain rankings; it is off by default
because turning it on re-embeds a whole library the next time its papers are read.

**Verification that the refactor changed nothing.** DEV re-scored on the refreshed cache:

| | before | after |
|---|---|---|
| precision | 0.90115 | 0.90115 |
| precision, witness-named lanes | 0.95073 | 0.95073 |
| coverage | 0.79715 | 0.79715 |
| conservation | 0.96805 | 0.96805 |

Identical to the digit, which is what a refactor should be.

**What was *not* wrong.** The first thing checked was whether `meaning.py` sends
nomic-embed-text its required task prefix, because `PLAN.md` names it and because it would have
invalidated every number in the campaign. It does: `QUERY` and `CLASSIFY` are applied at every
call site, and `Kind.space()` includes the prefix so changing one re-asks. The hypothesis was
mine and it was wrong.

## 2. The silence keeps what it nearly decided

`nodes` gains `guess`, `confidence` and `reasons`; `Node` gains the same three. `role` is
untouched and `other` still means the reader declined, so nothing that reads a role changes and
abstaining still costs coverage rather than precision — `CLAUDE.md`'s fifth invariant intact.
What changes is that the near miss survives.

No `uncertain` column, though `PLAN.md` asks for one: it would be
`role = 'other' AND guess IS NOT NULL`, and a flag that is a function of two other columns is a
third thing to keep in step with them.

## 3. A layout recognised without being named

`template.py` reads a paper's setting off its own type: page size, one column or two, the body's
face and size, the faces that stand apart from it and how, and where the type block sits. **No
journal name, no DOI prefix, no string from any publisher** — a test asserts the field list so
it stays that way.

Priced on DEV's 114 fingerprintable papers over 64 publishers the reader has never seen, against
the publisher as a *lower bound* on what a template is (one publisher sets its letters and its
research articles differently, and those pairs count against the fingerprint unfairly):

| | |
|---|---|
| AUC — a same-publisher pair nearer than a different one | **0.9061** |
| nearest neighbour shares the publisher | **71/82 = 0.866** |
| … against a chance rate of | 0.0147 |
| median distance, same publisher / different | 0.257 / 0.702 |

Two things the first measurement found, both fixed and re-measured:

**An embedded font subset carries a random six-letter tag.** One publisher's two papers read
`FVKCKB+ArnoPro-Regular` and `VEHTVM+ArnoPro-Regular` — one typeface counted as two. Stripping
the tag moved AUC 0.892 → 0.906, nearest neighbour 0.805 → 0.866, and publishers agreeing on one
body face from 21 of 32 to 25 of 32. Producers that hash the whole name (`AdvTTd9b1c495`) cannot
be rescued.

**The exact key repeated not once across 114 papers**, because it hashed the type block to the
thousandth of a page and the page count. The key is now taken over what a template fixes — page,
columns, body, the faces that stand apart — and the block and the length are left to `distance`,
which is a question of degree. Two groups repeat now. **Phase 7's "on a known fingerprint, reuse"
will have to lean on the distance rather than the key**, and that is worth knowing before it is
built.

## 4. There is nothing to agree: the mechanisms are one mechanism

`PLAN.md` asks for assertion by agreement among independent mechanisms, and names them: the
heading vocabulary, the catalogue of canonical names, the block classifier, the section-order
grammar, Docling's labels. The reader today runs the first three by **precedence** — the
vocabulary's regexes, then the catalogue, then the embedder on the heading — so the route Phase
3 measured as the *least* precise (0.891, against the embedder's 0.988) wins every contest it
enters, and the most precise is asked only where the others are silent.

That is a good reason to expect agreement to help. It does not, and the reason is the finding.

**Over 1,134 scoreable top-level sections, the mechanisms disagree zero times.** Where two or
more of them name a lane — 434 sections — they name the *same* lane, without exception:

| mechanisms that speak | sections |
|---|---|
| none | 575 |
| one | 125 |
| two | 29 |
| three | 405 |
| **two or more, disagreeing** | **0** |

So `unanimous` is `precedence`, `embedder first` is `precedence`, and all three veto policies are
no-ops. They score identically to the last digit because they *are* the same policy. The three
routes are different code — anchored regexes, a lookup table, a cosine — reading the same
heading string, and the table and the centroids are both harvested from the same corpus of
canonical spellings. A heading any of them knows is a canonical heading they all know, and they
all map it the same way. **The reader has one mechanism for a heading's lane, wearing three
hats.**

The one genuinely independent mechanism is the block classifier, which reads a section's
*paragraphs* rather than its heading. It is silent everywhere (§6).

### What agreement can still mean

If the three never conflict, then *how many of them recognise a heading* is not a resolution of
disagreement but a measure of how canonical the heading is — and that is worth something:

| policy | precision | 95% over publishers | coverage | precision by words | wrong |
|---|---|---|---|---|---|
| current (the reader) | 0.9380 | [0.9013, 0.9668] | 0.9542 | 0.9759 | 31 |
| two recognise it | 0.9788 | [0.9567, 0.9948] | 0.7195 | 0.9964 | 8 |
| **three recognise it** | **0.9886** | **[0.9759, 0.9974]** | 0.6698 | **0.9972** | **4** |

*(66 novel publishers, the conference supplement set aside — see below. Sections the witness
itself lanes `other` are excluded, as `precision_on_named` already does over paragraphs.)*

Asserting only where all three recognise the heading takes section precision from 0.938 to
**0.9886** and word precision to **0.9972**, and costs word coverage 0.981 → 0.811. Whether that
trade is the right one is `PLAN.md`'s gate and needs the fitted column beside it; it is not
decided here.

**The supplement, again, and it inverts the table.** With
`10.15167/2421-4248/jpmh2019.60.3s1` left in, it is **53.8 per cent of all scoreable sections**
and every precision above falls by ten points: `current` reads 0.8426 rather than 0.9380. Both
numbers are true; the second describes a corpus that is half one document. This is the fourth
measurement in this campaign that document has turned over.

## 5. The one independent mechanism, and why it may not contradict a heading

The block classifier reads a section's **paragraphs**, which makes it the only opinion in the
reader not derived from the heading. It is also silent almost everywhere: its scores cluster
around 0.70–0.76 against the best lane and about **0.02** behind against the second, where the
kind requires a margin of **0.08**. So `lane_sections`'s note — "the heading names methods, the
paragraphs read as results; the heading stands" — almost never fires, and the one opinion that
could contradict a heading is refused before it can.

`PLAN.md`'s first bullet for this phase is to derive thresholds from DEV rather than assume
them, so its threshold and margin were swept over 907 witness-named sections:

| threshold | margin | speaks | coverage | precision | agrees with the heading | and is right |
|---|---|---|---|---|---|---|
| 0.50 | 0.000 | 480 | 0.529 | 0.2604 | 120 | 119 |
| 0.65 | 0.010 | 224 | 0.247 | 0.3438 | 75 | 74 |
| 0.70 | 0.020 | 74 | 0.082 | 0.5135 | 39 | 38 |
| 0.72 | 0.030 | 23 | 0.025 | 0.5652 | 14 | 13 |
| 0.74 | 0.030 | 21 | 0.023 | **0.6190** | 13 | 13 |

**No threshold makes it fit to contradict a heading.** Its best precision anywhere is 0.619 on
21 sections, and at any usable coverage it is 0.26 to 0.46 — against heading routes that run
0.94 to 0.99. A veto by a mechanism that is wrong half the time would silence mostly-correct
headings, so `LITRAG_LANE_AGREEMENT` stays off, and now for a reason with a number on it rather
than caution.

**But as a confirmer it is near perfect.** In every row of that sweep, where it agrees with the
heading the heading is right about 99 per cent of the time: 119 of 120, 74 of 75, 38 of 39, 13
of 13. Joined with the heading routes on DEV's non-supplement sections:

| | precision | 95% over publishers | word coverage |
|---|---|---|---|
| current (the reader) | 0.9380 | [0.9013, 0.9668] | 0.9809 |
| all three routes recognise the heading | 0.9886 | [0.9759, 0.9974] | 0.8106 |
| **the paragraphs agree with the heading** | **1.0000** | **[1.0, 1.0]** | 0.2342 |
| both | 1.0000 | [1.0, 1.0] | 0.2246 |

Perfect precision exists and costs three quarters of the coverage. It is not an operating point;
it is a demonstration that the witness and the reader can be made to agree completely when the
reader is allowed to pick its ground.

## 6. The choice, made by the transfer gate

`PLAN.md`: *"Choose by T3: the policy whose precision on novel publishers stays closest to its
precision on familiar ones at the operating point, then by coverage."*

| policy | DEV | FITTED | **gap** | word coverage | wrong |
|---|---|---|---|---|---|
| current (precedence) | 0.9380 | 0.9704 | **+0.0324** | 0.9809 | 31 |
| **two of three recognise it** | 0.9788 | 0.9769 | **−0.0019** | 0.8471 | 8 |
| three of three | 0.9886 | 0.9843 | −0.0043 | 0.8106 | 4 |

**Two wins on both criteria** — the smaller gap and the larger coverage. Three is more precise
on DEV, and that is precisely the reason not to choose it on DEV.

The gap is the result worth keeping. Today's rule is 0.0324 better on the publishers it was
written on than on publishers it has never seen; under either agreement policy that gap closes
to nothing and slightly inverts. `LITRAG_CANONICAL_ONLY` takes that floor, `on` meaning two.

### And then the end-to-end measurement refused it

Every number above is per **section**, and T2 is per **paragraph**. Run through the campaign's
own scorer on DEV, with the witness now provably unmoved (4,631 paragraphs and 914 `other` in
all three columns):

| | baseline | floor 2 | floor 3 |
|---|---|---|---|
| witness paragraphs | 4,631 | 4,631 | 4,631 |
| asserted | 3,126 | 2,750 | 2,639 |
| wrong | 309 | 264 | 253 |
| precision | 0.90115 | 0.904 | 0.90413 |
| **precision, witness-named** | **0.95073** | **0.95286** | 0.9525 |
| **coverage, witness-named** | **0.79715** | **0.70191** | 0.67393 |

**It withholds 376 assertions to remove 45 wrong ones.** The withheld set was right 0.8803 of
the time, against 0.904 for the set it kept — barely a difference. Nine and a half points of
coverage buys two-tenths of a point of precision. **It does not ship.**

The two views are both correct and the gap between them is the lesson. Per section the floor
removed 23 of the 31 wrong sections; per paragraph it removed 45 of 309 wrong paragraphs. So the
wrong sections it caught were **small** — about two paragraphs each — and the wrong paragraphs
that remain sit in **large sections whose headings are perfectly canonical**. Which is the real
finding underneath:

> The lane errors that are left are not heading-recognition errors. Something else puts prose in
> the wrong lane — where a section starts and stops, the abstract boundary (0.637 precision on
> novel publishers), the short forms — and no amount of being stricter about *headings* reaches
> them.

`LITRAG_CANONICAL_ONLY` stays in the tree, off, with this number beside it. It is the right
mechanism aimed at the wrong error.

## 7. Four bugs in my own measurement, each caught by its own output

The first run of the mechanism table reported that the block classifier **speaks zero times out
of 1,260**, which is not a finding about the reader but about the script. `structure.lane_sections`
builds its cache key from `tree._descendants`, which yields the section itself before its
children; the script had a second implementation that walked the children only, in another
order, so `_key(paras)` differed and every lookup missed. A measurement that reports a component
doing nothing at all should be suspected of not having asked it.

The second: 645 of the 1,260 sections — 51 per cent — had `abstract` as their witness lane,
because the conference supplement `10.15167/2421-4248/jpmh2019.60.3s1` contributes 691 abstracts.
That document has now distorted the abstract-lane finding in Phase 3, I13's precision and two
base rates in Phase 4, and this. Every table in this phase reports the largest paper's share.

The third: 126 sections have `other` as their witness lane, where nothing that names a lane can
be right. They are set aside, as `precision_on_named` already does over paragraphs.

The fourth is the worst, because it looked like success. The first end-to-end run of the
canonical floor reported precision where the witness names a lane rising from 0.95073 to
**0.97575** — and the witness's own `other` count rising from 914 paragraphs to **2,408**, and
the split's witness paragraph count from 4,631 to 4,985. `harness.read_paper` builds both trees
with the same `build_tree`, so the floor was filtering the **JATS twin** alongside the PDF
reading. The precision rose because the questions with answers had been thrown away on both
sides of the comparison.

It was caught by a number that had no business moving: the witness cannot change when the reader
does. The fix is not a measurement patch — `pages` is already `tree.py`'s discriminator for
exactly this ("a PDF's layout model fuses lines; an XML's title is its title"), and a JATS file
states its structure outright, so withholding a lane it declares would be wrong in the product
quite apart from the measurement.

**A rule for the rest of this campaign, and for whoever reads this next: after any reader change,
check that the witness's own counts are unmoved before reading any number beside them.**

## 8. What Phase 5 delivered, and what it refused

**Shipped, default on:** nothing in the reader's behaviour. The reader reads DEV exactly as it
did at the end of Phase 4 — 0.90115 precision, 0.95073 on witness-named lanes, 0.79715 coverage,
0.96805 conservation.

**Shipped, working:**

- the oracle's cache split into `space()` and `rule()`, so a threshold sweep is a re-decision
  over rows already held rather than a re-reading of the corpus, in both directions;
- `guess`, `confidence` and `reasons` on `nodes`, so an abstention records what it nearly
  decided;
- `template.py`, a layout fingerprint with nothing of the publisher in it, priced at AUC 0.906
  and nearest-neighbour 0.866 against a chance rate of 0.0147;
- two switches, both off, both with their numbers: `LITRAG_LANE_AGREEMENT` and
  `LITRAG_CANONICAL_ONLY`.

**Refused, with the number:**

- *assertion by agreement* in the sense `PLAN.md` means it — the mechanisms never disagree;
- *the block classifier as a veto* — 0.26 to 0.62 precision, so it would silence mostly-correct
  headings;
- *the canonical floor* — 9.5 points of coverage for 0.2 of precision end to end.

**Not done:** the VAL gate. `PLAN.md` puts it at the chosen operating point, and DEV declined
the operating point before VAL was reached. Scoring VAL to confirm a rejection DEV already made
would spend a split on a question that is answered. VAL and SEALED remain unscored; EXAM remains
unfetched.

**The finding to carry forward.** Every mechanism this phase examined reads the *heading*, and
the heading is no longer where the errors are. On novel publishers `methods` reads 1.000 and
`results` 0.990 — those headings are recognised. What is left is `abstract` at 0.637, the short
forms, and prose that lands in a section it does not belong to. Phase 6's escalation and Phase
7's template memory are both better aimed at that than anything in this phase was.
