# Phase 2 — the measurement apparatus

**Gate: passed.** A corpus drawn by publisher, metrics that have each been shown
to fail, and the familiar-versus-novel gap measured on 66 novel publishers
instead of the 20 papers it rested on before.

Commits `c05cc08`, `87e36a6`, `0216bfe`, `e25db5d`, `d230406`, `7ce0518`,
`e8dec14`. Ledger: phase 2, and every `score-*` line.

---

## 1. The corpus

`BACKLOG.md` asked for a set "drawn by publisher, not by topic", after the fourth
held-out set turned out to be 92 per cent publishers the rules were already
written on — which made its headline a measure of transfer to new *papers*, read
as transfer to new *layouts*.

- **87 fitted prefixes** over 1,371 papers are what the reader was built on; five
  of them are half of it (MDPI 278, Springer Nature 136, Elsevier 120, Frontiers
  115, Wiley 109). Novel means: in none of those 87.
- **29,270 candidates** over 426 prefixes, from 106 Europe PMC queries — 96
  ordinary subject terms spanning the whole of biomedicine and its neighbours,
  plus 8 `PUB_TYPE` queries for the kinds the reader reads worst.
- **Splits are a hash of the publisher's own name** and a fixed salt. So a split
  can be recomputed by anyone, does not depend on the order a search returned
  things in, and — the part that matters — **cannot move when the corpus grows**.
- **336 of 340 papers fetched, covering 181 of 181 publishers.** DEV 131, VAL 97,
  SEALED 108. EXAM (74 papers, 31 publishers) is defined and **not fetched**.

Two things were priced before the split was fixed, because finding them out
afterwards would have shrunk whichever split they fell in: Europe PMC's
`HAS_PDF:y` is its index and not EBI's holdings (**47 per cent** of candidates
have no PDF in the bulk area, and one format is no witness), and PMC author
manuscripts share one NIH layout, so they would have counted as a publisher.

## 2. The metrics, and the proof each can fail

Twenty tests in `test_evaluate.py`, most of them mutations. Delete a paragraph and
conservation falls; swap two sections' lanes **in the tree** and precision falls
1.000 → 0.484; make the reader name one lane for everything and precision falls
below 0.5 at full coverage; make it name nothing and precision is `None` rather
than perfect; shuffle the labels and it falls to chance; plant a publisher, a DOI
or a fitted prefix in two splits and the leak check fires.

The clustered bootstrap gets its own test, against an unclustered one computed in
the test: on eleven publishers where ten read perfectly and one reads nothing
right, clustered is [0.727, 1.000] and unclustered [0.868, 0.945]. The first
version of that test asserted only "reaches below 0.92", **which the unclustered
interval also does** — it could not have failed. The independent review found
that, not me.

## 3. T3, measured

Both columns at the same commit, on the same metric definitions.

| | FITTED — 63 publishers the reader was built on | DEV — 66 publishers it has never seen |
|---|---|---|
| papers | 487 | 123 |
| witness paragraphs | 21,139 | 4,631 |
| **precision, strict** | **0.9778** | **0.8999** |
| precision, witness-named lanes | 0.9855 [0.976, 0.992] | 0.9494 [0.899, 0.980] |
| precision, macro over publishers | 0.9123 | 0.8695 |
| coverage, all paragraphs | 0.7385 | 0.6752 |
| coverage, witness-named lanes | 0.9767 | 0.7974 |
| conservation (T1) | 0.9179 | 0.9681 |

**The drop is real and it is about 3.6 points** on the comparison that sets the
witness's own silences aside — 0.9855 against 0.9494, with intervals that barely
overlap. On the strict reading it is 7.8 points. `NOTES.md` had estimated the
same effect at 0.966 → 0.906 from 20 papers across 12 prefixes; it now rests on 66
novel publishers with an interval clustered on them.

**And the reader fails in the right direction.** Coverage on witness-named lanes
falls much harder than precision does — 0.977 to 0.797 — which is what PLAN's
principle 3 asks for: under shift, lose coverage, not precision. Nothing was built
to make that happen; it is what the existing `other`-by-default already does.

## 4. Where the damage is, and it is where Phase 0 said

Per lane, on the publishers the reader has never seen:

| lane | precision | coverage | n |
|---|---|---|---|
| **methods** | **1.000** | 0.98 | 827 |
| results | 0.990 | 0.98 | 519 |
| discussion | 0.990 | 0.96 | 753 |
| introduction | 0.897 | 0.85 | 780 |
| results-discussion | 0.863 | 1.00 | 117 |
| **abstract** | **0.633** | **0.20** | 721 |
| `other` | 0.000 | 0.18 | 914 |

On a publisher it has never seen, the reader reads the **body almost
flawlessly** — methods is perfect on 827 paragraphs — and falls apart at the
**front of the paper**. The four largest confusions are `introduction → abstract`
(63), `other → results` (63), `other → introduction` (43), `other → abstract`
(30).

That is precisely the front-matter path Phase 0 localised as holding all ~150
publisher-specific tokens. The attribution was a hypothesis then; it is a
measurement now, and it says where Phase 3 should aim.

(`other` at 0.000 is structural, not a reading failure: asserting means naming a
lane and being correct means matching `other`, so the two cannot both hold. It is
reported because those 914 paragraphs are a fifth of the corpus and the 163 the
reader asserts on are real wrong assertions under the strict reading.)

## 4a. Which rules transfer, and which do not

Every rule that takes a block out of the body, priced against the witness, on both
columns at the same commit. Precision here is the two-way `body` reading: a
removal is wrong only if the witness holds the text as prose **in a named body
lane**, which sets aside the front matter the witness's own reading leaves in its
body.

| rule | fitted (487 papers) | novel (123 papers) | change |
|---|---|---|---|
| `dropped:running` | 0.972 (1,986) | **0.853** (672) | **−11.9** |
| `front:affiliations` | 0.952 (479) | **0.838** (148) | **−11.4** |
| `dropped:label` | 0.961 (181) | **0.857** (35) | **−10.4** |
| `front:notice` | 0.973 (332) | **0.910** (67) | **−6.3** |
| `front:keywords` | 0.375 (272) | 0.305 (59) | −7.0 · witness noise, both columns |
| `front:authors` | 0.998 (393) | 0.989 (89) | −0.9 |
| `front:correspondence` | 0.959 (417) | 0.963 (81) | +0.4 |
| `front:dates` | 0.989 (268) | 1.000 (46) | +1.1 |
| `front:funding` | 0.960 (25) | 1.000 (7) | +4.0 |
| `front:other` | 0.658 (155) | 0.786 (56) | +12.8 |
| `dropped:furniture` | 1.000 (69) | 1.000 (11) | 0.0 |

**The four rules that lose most on a publisher they have not seen are exactly the
four that read a publisher's strings**: running heads and furniture
(`_RUNNING`, `_FURNITURE`), affiliations, the label path, and the notice/licence
path. The rules that key on something the document itself provides —
`front:dates` (a date is a date), `front:authors`, `front:correspondence`,
`dropped:furniture` (recurrence across pages, pure geometry) — hold or improve.

`dropped:furniture` is the proof of the principle in miniature: it fires 69 times
on fitted publishers and 11 on novel ones, and it is **right every time on both**,
because it keys on a box recurring at the same height on three pages and nothing
else.

## 5. What the apparatus found about itself

Four measurements of mine were refused by other measurements, each at the cost of
one run. They are listed because the pattern matters more than any of them:

1. *T1 is 0.959.* No — that tokeniser ignored every digit, and the measure scored
   hyphenation as lost text (63.6 per cent of the apparent loss). 0.918 on the
   fitted corpora, 0.968 on DEV.
2. *T1's residue is figure text.* 0.7 per cent of it is.
3. *Then it is table text.* The test was worthless: 46 per cent of table boxes
   cover more than half their page, so it catches the body text with them.
4. *`front:keywords` is the reader's worst rule at 0.368.* It is not a reader
   error: the lines are keyword lines, correctly filed as front matter, which the
   **XML's own reading** leaves in its body. The measure was pricing the witness.

A fifth was found by the reviewer, not by me: the clustered-bootstrap test could
not fail.

## 6. Corpus integrity

- **21 of DEV's 135 papers were scored as absent**, because 15 PDFs printed
  neither a DOI nor a PMCID on their first pages and were filed under a content
  hash — and a hash meets nothing, so each lost the JATS twin that was to be its
  witness. Every one of them was *named* `PMC….pdf`. `sniff_ids` now falls back
  to the file's own name (a property of the file, not of a publisher), all 15 were
  re-filed, and DEV went from 114 papers to 123. The precision barely moved
  (0.9025 → 0.8999), which is the reassuring outcome: more data, same answer.
- `pairs_by_identity` pairs on DOI or PMCID when the keys differ, which
  `NOTES.md` had asked for after held-out 4 lost five papers the same way.
- **12 DEV papers remain unpaired** and are named in every run rather than
  quietly dropped.
- T4 on the corpus: `corpus-pdf` 334 parsed, **0 broken, 0 failed**; `corpus-xml`
  335 parsed, 1 failed carrying Docling's own reason.

## 7. A limit of the rule-pricing method, stated

`ruletable.py` prices each removal by asking whether the witness holds the text as
prose. For short, repeated text that is unreliable: a running head reading
`Volker Kahlenberg et al. — K0.72Na1.71…` matches the XML's *contributor block*,
so the removal scores wrong when it was right. The two-way judgement (any prose,
against prose in a named body lane) removes most of it, not all. Rules over short
strings — `dropped:running`, `front:authors`, `front:dates` — carry that caveat.
