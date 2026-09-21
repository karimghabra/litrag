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

## 5. Three bugs in my own measurement, each caught by its own output

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
