# Decisions taken for Karim

Choices that are really Karim's to make. Each one records the reversible
default taken and why, so he can overturn it cheaply. Newest last.

---

## D1 — The integration branch is cut from the merge commit of PR #20

*2026-09-21, Phase 0.*

**The choice.** `PROMPT.md` says "start from the branch
`claude/decisions-by-meaning`". Three facts complicate that:

- PR #20 (`claude/reader-measured-on-pairs`) was merged **into**
  `claude/decisions-by-meaning`, not into `main`. Its merge commit is
  `8fae8aa`.
- `origin/main` is still at `dcef6a7`, the merge of PR #19. It does **not**
  contain the reader work whose numbers this campaign is asked to reproduce.
- The local checkout was at `ff124e7`, one commit behind `8fae8aa`, and
  content-identical to it (the merge commit changes no file).

**The default taken.** The integration branch `campaign/dynamic-reader` is cut
from `8fae8aa` — `origin/claude/decisions-by-meaning` at its tip. This *is*
"start from `claude/decisions-by-meaning`" on the correct reading: that branch
now contains the measured reader. Cutting from the older tip instead would have
discarded PR #20 and made the baseline unreproducible by construction.

**Reversibility.** Total. Rebase the branch onto any other base.

**What Karim may want to decide instead.** Whether `claude/decisions-by-meaning`
should be merged to `main` before this campaign's PRs stack on top of it. I have
not merged anything to `main` and will not.

---

## D2 — The campaign works on copies, under a campaign root of its own

*2026-09-21, Phase 0.*

**The choice.** `rebuild` writes to a library's `store.sqlite`, and the oracle's
verdicts are written to `lanes.sqlite` at the **library root**, shared by every
library under it. So any rebuild run against `~/.protracker/library` would write
into Karim's own data. `PROMPT.md` forbids that.

**The default taken.** `~/.protracker/campaign/library` holds byte-for-byte
copies of all eleven libraries (3.5 GB; 254 GB free at the time), plus a copy of
`lanes.sqlite` (with its WAL, so none of the 107,771 stored verdicts are lost)
and of `headings.json`. Every command this campaign runs sets
`LITRAG_ROOT=$HOME/.protracker/campaign/library`.

Karim's root was fingerprinted first — every file's size and mtime, 3,354 files,
in `campaign/karim-root-digest.json`. It is re-checked at each phase gate
and in the final report, so "his libraries were never written to" is a verified
claim rather than an intention.

**Reversibility.** Total. Delete the campaign root; it is 3.5 GB of copies.

**The cost.** Disk, and the risk that the copies drift from his originals if he
reads papers during the week. The fingerprint detects that too.

---

## D3 — Reported numbers come from the campaign root, not Karim's

*2026-09-21, Phase 0.*

**The choice.** The baseline numbers in `NOTES.md` were measured against
`~/.protracker/library`. Reproducing them against a copy is only valid if the
copy is faithful.

**The default taken.** Reproduce the baseline on the copies, and verify the
copies are faithful by file count and `store.sqlite` size against the source
(done: eleven of eleven match). If a baseline number fails to reproduce, check
it against Karim's root **read-only** before concluding anything about the code.

**Reversibility.** Total.

---

## D4 — The layout child ships on by default

*2026-09-21, Phase 1.*

**The choice.** The house rule is that a new stage ships **off** until its gate
passes (`PLAN.md` principle 6). The layout child is a new stage, so the rule
points at `off`.

**The default taken: on**, with `LITRAG_LAYOUT_CHILD=off` as the way back.

The rule exists for stages that change *what is read* — a model's verdict, a new
heuristic — where shipping on before the measurement is how a regression gets
in. This stage changes nothing that is read: the same Docling, the same options,
the same exported dict, written to the same path. What it changes is who dies
when the native crash happens. Shipping it off would mean shipping the crash,
and the crash is the reason the last corpus lost seventeen papers silently.

**Its gate**, met before this was committed: over the same PDFs, the raw
documents are byte-identical (pictures aside, see D5) and the node counts are
identical, child and in-process; and the fault-injection tests are green.

**Reversibility.** One environment variable, and the in-process path is still
there and still tested.

---

## D5 — Docling's picture boxes are not reproducible, and the campaign accepts that

*2026-09-21, Phase 1.*

**What was found.** Six conversions of one PDF in one process gave **two
distinct raw documents**. Stripping `pictures`, all six were identical; the
`texts` were identical across all six. So Docling's picture bounding boxes wobble
at about the fifth decimal of a point, roughly one run in six, and everything the
reader actually reads — the prose, the tables — is bit-reproducible.
(`doclingdet.py`.)

**The default taken.** Accept it, and say so wherever reproducibility is claimed.
A paper is parsed once and every later measurement runs on the saved document, so
this cannot move a campaign number; it can only mean that *re-parsing* a corpus
from the PDFs does not give byte-identical raw documents.

**The residual risk, stated.** `tree._decorative_pictures` rounds a picture's box
to 4 pt to spot one that recurs on three pages. A 1e-4 pt wobble crosses a 4 pt
boundary only if a box sits exactly on one. Not observed; not impossible.

**What Karim may want instead.** Pinning Docling's layout model to deterministic
kernels, if its options allow it, or dropping `pictures` from what is stored.
Both are larger than this week.

---

## D6 — Reading Karim's libraries made SQLite touch his directory, and the check caught it

*2026-09-21, Phase 3.*

**What happened.** `manifest.py` decided which papers the corpus must exclude by
reading every library under `~/.protracker/library` — his root, not the campaign's
copies. Opening a SQLite database creates its `-shm` and `-wal` sidecars **even
read-only**, so the integrity check went from UNCHANGED to MODIFIED: six sidecars
added, eight more touched.

**What was actually changed.** Nothing of his. All eleven `store.sqlite` files are
identical in size *and* mtime, every added file is a `-shm` or `-wal`, and every
`store.sqlite-wal` is **0 bytes** — no pending writes existed to flush. Verified
before anything was altered; the original per-file fingerprint is in this repo's
history.

**The default taken.** Two changes. `manifest.py` reads the campaign's copies,
which hold the same papers. And `karim_root.py` no longer fingerprints `-shm` and
`-wal`, because they are SQLite's scratch and carry no data — a check that fires
on them cries wolf, and a check that cries wolf gets ignored. A change to any
`store.sqlite` still fires.

**What Karim may want instead.** The claim in `reports/phase0.md` and
`reports/phase1.md` is now precisely: *no data in his libraries was read into,
written to, or modified*, and *his directory did acquire SQLite sidecar files from
read-only opens*. Those are different sentences and the second one is true.
